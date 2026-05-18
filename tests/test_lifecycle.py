"""
Tests for lifecycle state machine with promotion/demotion rules.
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.lifecycle import LifecycleManager
from core.database import init_db, close_db
from core.models import Memory, ConsolidationLog, RetrievalLog
from core.tiers import stage_to_tier, tier_ttl


@pytest.fixture
def base(tmp_path):
    """Initialize DB in a throwaway temp directory."""
    init_db(base_dir=str(tmp_path / 'memory'))
    yield
    close_db()


@pytest.fixture
def manager(base):
    """Create a LifecycleManager."""
    return LifecycleManager()


def _create_memory(stage='ephemeral', title='Test Memory', content='Test content', **kwargs):
    """Helper to create a memory and return its ID."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage=stage,
        title=title,
        summary=kwargs.get('summary', f'Summary of {title}'),
        content=content,
        tags=json.dumps(kwargs.get('tags', [])),
        importance=kwargs.get('importance', 0.5),
        reinforcement_count=kwargs.get('reinforcement_count', 0),
        usage_count=kwargs.get('usage_count', 0),
        # Classified by default so consolidated rows clear the can_promote
        # memory_kind gate; tests exercising the gate pass memory_kind=None.
        memory_kind=kwargs.get('memory_kind', 'fact'),
        created_at=now,
        updated_at=now,
    )
    return mem.id


def _add_reinforcement_log(memory_id, timestamp, session_id="test-session"):
    """Helper: insert a consolidation_log 'promoted' entry at a specific timestamp."""
    ConsolidationLog.create(
        timestamp=timestamp.isoformat(),
        session_id=session_id,
        action='promoted',
        memory_id=memory_id,
        from_stage='consolidated',
        to_stage='consolidated',
        rationale='Reinforced',
    )


def _record_injection(memory_id, session_id):
    """Helper: record an injection for usage tracking."""
    now = datetime.now().isoformat()
    Memory.update(
        last_injected_at=now,
        injection_count=Memory.injection_count + 1,
    ).where(Memory.id == memory_id).execute()
    RetrievalLog.create(
        timestamp=now,
        session_id=session_id,
        memory_id=memory_id,
        retrieval_type='injected',
    )


def _record_usage(memory_id, session_id):
    """Helper: record that a memory was used."""
    now = datetime.now().isoformat()
    Memory.update(
        last_used_at=now,
        usage_count=Memory.usage_count + 1,
    ).where(Memory.id == memory_id).execute()
    RetrievalLog.update(was_used=1).where(
        RetrievalLog.memory_id == memory_id,
        RetrievalLog.session_id == session_id,
    ).execute()


@pytest.mark.usefixtures("base")
def test_promote_ephemeral_to_consolidated(manager):
    """Test promotion from ephemeral to consolidated stage."""
    memory_id = _create_memory(stage='ephemeral', title='Test Memory')

    new_stage = manager.promote(memory_id, rationale='Ready for consolidation')
    assert new_stage == 'consolidated'

    memory = Memory.get_by_id(memory_id)
    assert memory.stage == 'consolidated'

    # Verify transition logged
    row = ConsolidationLog.get_or_none(
        (ConsolidationLog.memory_id == memory_id) &
        (ConsolidationLog.action == 'promoted')
    )
    assert row is not None
    assert row.from_stage == 'ephemeral'
    assert row.to_stage == 'consolidated'
    assert row.rationale == 'Ready for consolidation'


@pytest.mark.usefixtures("base")
def test_promote_consolidated_to_crystallized_with_reinforcement(manager):
    """Test promotion to crystallized requires 3+ reinforcements (D-07)."""
    memory_id = _create_memory(
        stage='consolidated',
        reinforcement_count=3,
    )

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is True
    assert '3 reinforcements' in reason

    new_stage = manager.promote(memory_id, rationale='Met reinforcement threshold')
    assert new_stage == 'crystallized'


@pytest.mark.usefixtures("base")
def test_cannot_promote_without_reinforcement(manager):
    """Test promotion to crystallized blocked without 3 reinforcements."""
    memory_id = _create_memory(
        stage='consolidated',
        reinforcement_count=2,
    )

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'need 3+' in reason.lower()

    with pytest.raises(ValueError, match='Cannot promote'):
        manager.promote(memory_id, rationale='Trying to promote')


@pytest.mark.usefixtures("base")
def test_promote_crystallized_to_instinctive(manager):
    """Test promotion to instinctive requires importance > 0.85 and 10+ sessions."""
    memory_id = _create_memory(
        stage='crystallized',
        importance=0.9,
        usage_count=15,
    )

    # Record usage in 10+ unique sessions
    for i in range(10):
        session_id = f'session_{i}'
        _record_injection(memory_id, session_id)
        _record_usage(memory_id, session_id)

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is True
    assert 'High importance' in reason
    assert '10' in reason

    new_stage = manager.promote(memory_id, rationale='Proven critical memory')
    assert new_stage == 'instinctive'


@pytest.mark.usefixtures("base")
def test_cannot_promote_low_importance_to_instinctive(manager):
    """Test promotion to instinctive blocked by low importance."""
    memory_id = _create_memory(
        stage='crystallized',
        importance=0.7,
        usage_count=15,
    )

    for i in range(10):
        session_id = f'session_{i}'
        _record_injection(memory_id, session_id)
        _record_usage(memory_id, session_id)

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'too low' in reason.lower()


@pytest.mark.usefixtures("base")
def test_cannot_skip_stages_on_promotion(manager):
    """Test that promotion cannot skip stages."""
    _create_memory(stage='ephemeral')

    is_valid = manager.validate_transition('ephemeral', 'crystallized')
    assert is_valid is False

    is_valid = manager.validate_transition('ephemeral', 'instinctive')
    assert is_valid is False


@pytest.mark.usefixtures("base")
def test_demote_memory(manager):
    """Test demotion of memory to lower stage."""
    memory_id = _create_memory(stage='crystallized')

    new_stage = manager.demote(memory_id, rationale='Low usage')
    assert new_stage == 'consolidated'

    memory = Memory.get_by_id(memory_id)
    assert memory.stage == 'consolidated'

    # Verify transition logged
    row = ConsolidationLog.get_or_none(
        (ConsolidationLog.memory_id == memory_id) &
        (ConsolidationLog.action == 'demoted')
    )
    assert row is not None
    assert row.from_stage == 'crystallized'
    assert row.to_stage == 'consolidated'
    assert row.rationale == 'Low usage'


@pytest.mark.usefixtures("base")
def test_demotion_can_skip_stages(manager):
    """Test that demotion can skip stages (unlike promotion)."""
    is_valid = manager.validate_transition('instinctive', 'consolidated')
    assert is_valid is True

    is_valid = manager.validate_transition('crystallized', 'ephemeral')
    assert is_valid is True


@pytest.mark.usefixtures("base")
def test_deprecate_memory(manager):
    """Test deprecation moves memory to archived directory."""
    memory_id = _create_memory(stage='ephemeral')

    manager.deprecate(memory_id, rationale='No longer relevant')

    # No longer retrievable from active store
    with pytest.raises(Memory.DoesNotExist):
        Memory.get_by_id(memory_id)

    # Verify deprecation logged
    row = ConsolidationLog.get_or_none(
        (ConsolidationLog.memory_id == memory_id) &
        (ConsolidationLog.action == 'deprecated')
    )
    assert row is not None
    assert row.from_stage == 'ephemeral'
    assert row.to_stage == 'archived'


@pytest.mark.usefixtures("base")
def test_get_promotion_candidates(manager):
    """Test retrieval of memories eligible for promotion to crystallized."""
    memory_1 = _create_memory(
        stage='consolidated',
        title='Memory 1',
        reinforcement_count=5,
    )

    memory_2 = _create_memory(
        stage='consolidated',
        title='Memory 2',
        reinforcement_count=2,
    )

    memory_3 = _create_memory(
        stage='consolidated',
        title='Memory 3',
        reinforcement_count=3,
    )

    candidates = manager.get_promotion_candidates()

    assert len(candidates) == 2
    candidate_ids = [c['id'] for c in candidates]
    assert memory_1 in candidate_ids
    assert memory_3 in candidate_ids
    assert memory_2 not in candidate_ids


@pytest.mark.usefixtures("base")
def test_get_demotion_candidates(manager):
    """Test retrieval of memories with high injection but no usage (D-09)."""
    memory_id = _create_memory(
        stage='crystallized',
        title='Unused Memory',
    )

    # Inject 12 times without usage
    for i in range(12):
        _record_injection(memory_id, f'session_{i}')

    candidates = manager.get_demotion_candidates()

    assert len(candidates) == 1
    assert candidates[0]['id'] == memory_id
    assert candidates[0]['injection_count'] >= 10
    assert candidates[0]['usage_count'] == 0
    assert 'never used' in candidates[0]['reason'].lower()


@pytest.mark.usefixtures("base")
def test_get_demotion_candidates_ignores_used_memories(manager):
    """Test that demotion candidates exclude memories that are actually used."""
    memory_id = _create_memory(
        stage='crystallized',
        title='Used Memory',
    )

    for i in range(15):
        session_id = f'session_{i}'
        _record_injection(memory_id, session_id)
        if i < 5:
            _record_usage(memory_id, session_id)

    candidates = manager.get_demotion_candidates()
    assert len(candidates) == 0


@pytest.mark.usefixtures("base")
def test_get_deprecation_candidates(manager):
    """Test retrieval of stale memories for deprecation."""
    memory_id = _create_memory(
        stage='ephemeral',
        title='Old Memory',
    )

    # Manually set last_injected_at to 40 days ago
    old_date = (datetime.now() - timedelta(days=40)).isoformat()
    Memory.update(last_injected_at=old_date).where(Memory.id == memory_id).execute()

    candidates = manager.get_deprecation_candidates(stale_sessions=30)

    assert len(candidates) == 1
    assert candidates[0]['id'] == memory_id
    assert 'No activity' in candidates[0]['reason']


@pytest.mark.usefixtures("base")
def test_cannot_promote_from_highest_stage(manager):
    """Test that instinctive memories cannot be promoted further."""
    memory_id = _create_memory(stage='instinctive')

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'highest stage' in reason.lower()

    with pytest.raises(ValueError, match='already at highest stage'):
        manager.promote(memory_id, rationale='Trying to promote')


@pytest.mark.usefixtures("base")
def test_cannot_demote_from_lowest_stage(manager):
    """Test that ephemeral memories cannot be demoted further."""
    memory_id = _create_memory(stage='ephemeral')

    with pytest.raises(ValueError, match='already at lowest stage'):
        manager.demote(memory_id, rationale='Trying to demote')


def test_validate_transition_invalid_stages(manager):
    """Test transition validation with invalid stage names."""
    assert manager.validate_transition('invalid', 'consolidated') is False
    assert manager.validate_transition('ephemeral', 'invalid') is False
    assert manager.validate_transition('invalid1', 'invalid2') is False


def test_validate_transition_same_stage(manager):
    """Test that transitioning to the same stage is invalid."""
    assert manager.validate_transition('consolidated', 'consolidated') is False


@pytest.mark.usefixtures("base")
def test_promotion_updates_stage(manager):
    """Test that promotion updates the stage."""
    memory_id = _create_memory(stage='ephemeral')

    memory = Memory.get_by_id(memory_id)
    assert memory.stage == 'ephemeral'

    manager.promote(memory_id, rationale='Test promotion')

    updated = Memory.get_by_id(memory_id)
    assert updated.stage == 'consolidated'


@pytest.mark.usefixtures("base")
def test_multiple_promotions_in_sequence(manager):
    """Test promoting a memory through multiple stages."""
    memory_id = _create_memory(
        stage='ephemeral',
        reinforcement_count=5,
        importance=0.9,
    )

    # Promote to consolidated
    stage = manager.promote(memory_id, rationale='First promotion')
    assert stage == 'consolidated'

    # Promote to crystallized (has 5 reinforcements)
    stage = manager.promote(memory_id, rationale='Second promotion')
    assert stage == 'crystallized'

    # Record usage in 10+ sessions to enable instinctive promotion
    for i in range(10):
        session_id = f'session_{i}'
        _record_injection(memory_id, session_id)
        _record_usage(memory_id, session_id)

    # Promote to instinctive
    stage = manager.promote(memory_id, rationale='Third promotion')
    assert stage == 'instinctive'

    # Verify all transitions logged
    count = ConsolidationLog.select().where(
        (ConsolidationLog.memory_id == memory_id) &
        (ConsolidationLog.action == 'promoted')
    ).count()
    assert count == 3


# -------------------------------------------------------------------
# Spacing effect tests
# -------------------------------------------------------------------


@pytest.mark.usefixtures("base")
class TestSpacingEffect:
    """Test brain-inspired spacing effect for promotion quality."""

    @pytest.fixture(autouse=True)
    def _enable_spacing_gate(self, monkeypatch):
        """The spacing gate is globally disabled (commit 065d1d0 set MIN_REINFORCEMENT_SPAN_DAYS=0).

        These tests exist to verify the gate works *when enabled*. Re-enable
        per-test via monkeypatch so the gate's behavior is exercised without
        flipping the global default.
        """
        from core.lifecycle import LifecycleManager
        monkeypatch.setattr(LifecycleManager, "MIN_REINFORCEMENT_SPAN_DAYS", 2)

    def test_burst_reinforcement_blocked(self, manager):
        """3 reinforcements on the same day should NOT qualify for promotion."""
        memory_id = _create_memory(
            stage='consolidated',
            reinforcement_count=3,
        )

        # All 3 reinforcements on the same day
        today = datetime.now()
        for i in range(3):
            _add_reinforcement_log(
                memory_id,
                today.replace(hour=10 + i, minute=0),
                session_id=f"session-{i}",
            )

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is False
        assert 'spacing' in reason.lower() or 'distinct day' in reason.lower()

    def test_spaced_reinforcement_allowed(self, manager):
        """3 reinforcements across 3 different days should qualify."""
        memory_id = _create_memory(
            stage='consolidated',
            reinforcement_count=3,
        )

        base_time = datetime(2026, 3, 20, 14, 0)
        for i in range(3):
            _add_reinforcement_log(
                memory_id,
                base_time + timedelta(days=i * 3),
                session_id=f"session-day-{i}",
            )

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True
        assert 'distinct days' in reason.lower() or 'reinforcement' in reason.lower()

    def test_two_days_is_minimum_spacing(self, manager):
        """Reinforcements spanning exactly 2 distinct days should pass."""
        memory_id = _create_memory(
            stage='consolidated',
            reinforcement_count=3,
        )

        day1 = datetime(2026, 3, 20, 10, 0)
        day2 = datetime(2026, 3, 21, 14, 0)
        _add_reinforcement_log(memory_id, day1, "s1")
        _add_reinforcement_log(memory_id, day1.replace(hour=15), "s2")
        _add_reinforcement_log(memory_id, day2, "s3")

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True

    def test_no_log_entries_fallback(self, manager):
        """Legacy memories with no consolidation log entries should still promote."""
        memory_id = _create_memory(
            stage='consolidated',
            reinforcement_count=5,
        )

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True

    def test_spacing_does_not_affect_count_check(self, manager):
        """Count check still applies -- 2 reinforcements fail even if spaced."""
        memory_id = _create_memory(
            stage='consolidated',
            reinforcement_count=2,
        )

        base_time = datetime(2026, 3, 10, 12, 0)
        _add_reinforcement_log(memory_id, base_time, "s1")
        _add_reinforcement_log(memory_id, base_time + timedelta(days=7), "s2")

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is False
        assert 'need 3+' in reason.lower()

    def test_get_promotion_candidates_respects_spacing(self, manager):
        """get_promotion_candidates should filter out burst-reinforced memories."""
        # Memory with spaced reinforcement
        spaced_id = _create_memory(
            stage='consolidated',
            title='Spaced',
            reinforcement_count=3,
        )
        base_time = datetime(2026, 3, 1, 10, 0)
        for i in range(3):
            _add_reinforcement_log(spaced_id, base_time + timedelta(days=i * 2), f"s{i}")

        # Memory with burst reinforcement
        burst_id = _create_memory(
            stage='consolidated',
            title='Burst',
            reinforcement_count=4,
        )
        same_day = datetime(2026, 3, 15, 9, 0)
        for i in range(4):
            _add_reinforcement_log(burst_id, same_day.replace(hour=9 + i), f"b{i}")

        candidates = manager.get_promotion_candidates()
        candidate_ids = [c['id'] for c in candidates]
        assert spaced_id in candidate_ids
        assert burst_id not in candidate_ids


# -------------------------------------------------------------------
# Expiry wiring tests (B2 + B3)
# -------------------------------------------------------------------


@pytest.mark.usefixtures("base")
class TestExpiryWiring:
    """Test that promote/demote set expires_at via set_expiry (B2)."""

    SLACK = 10  # seconds of tolerance for timestamp comparisons

    def test_promote_sets_expires_at(self, manager):
        """After promote(), expires_at is non-null and approximately now + T3 TTL."""
        memory_id = _create_memory(stage='ephemeral')

        before = int(time.time())
        manager.promote(memory_id, rationale='Test expiry on promote')
        after = int(time.time())

        memory = Memory.get_by_id(memory_id)
        assert memory.stage == 'consolidated'

        expected_ttl = tier_ttl(stage_to_tier('consolidated'))  # T3 = 90 days
        assert expected_ttl is not None
        assert memory.expires_at is not None
        assert before + expected_ttl - self.SLACK <= memory.expires_at <= after + expected_ttl + self.SLACK

    def test_demote_sets_expires_at_to_lower_tier(self, manager):
        """After demote(), expires_at reflects the lower tier TTL."""
        memory_id = _create_memory(stage='crystallized')

        before = int(time.time())
        manager.demote(memory_id, rationale='Test expiry on demote')
        after = int(time.time())

        memory = Memory.get_by_id(memory_id)
        assert memory.stage == 'consolidated'

        expected_ttl = tier_ttl(stage_to_tier('consolidated'))  # T3 = 90 days
        assert expected_ttl is not None
        assert memory.expires_at is not None
        assert before + expected_ttl - self.SLACK <= memory.expires_at <= after + expected_ttl + self.SLACK

    def test_promote_to_instinctive_sets_expires_at_none(self, manager):
        """T1 (instinctive) promotion sets expires_at = None (never expire)."""
        memory_id = _create_memory(
            stage='crystallized',
            importance=0.9,
            usage_count=15,
        )
        for i in range(10):
            session_id = f'session_{i}'
            _record_injection(memory_id, session_id)
            _record_usage(memory_id, session_id)

        manager.promote(memory_id, rationale='Instinctive promotion')

        memory = Memory.get_by_id(memory_id)
        assert memory.stage == 'instinctive'
        assert memory.expires_at is None

    def test_demote_from_instinctive_sets_expires_at(self, manager):
        """Demoting from instinctive (T1) to crystallized (T2) sets expires_at."""
        memory_id = _create_memory(stage='instinctive')

        before = int(time.time())
        manager.demote(memory_id, rationale='Drop from instinctive')
        after = int(time.time())

        memory = Memory.get_by_id(memory_id)
        assert memory.stage == 'crystallized'

        expected_ttl = tier_ttl(stage_to_tier('crystallized'))  # T2 = 180 days
        assert expected_ttl is not None
        assert memory.expires_at is not None
        assert before + expected_ttl - self.SLACK <= memory.expires_at <= after + expected_ttl + self.SLACK

    def test_deprecate_does_not_call_set_expiry(self, manager):
        """deprecate() (archive path) must NOT set expires_at — archived_at handles exclusion."""
        memory_id = _create_memory(stage='ephemeral')

        # deprecate() deletes the row entirely; just verify no exception and no row remains
        manager.deprecate(memory_id, rationale='Test deprecate leaves expiry alone')

        with pytest.raises(Memory.DoesNotExist):
            Memory.get_by_id(memory_id)


# -------------------------------------------------------------------
# Importance-gated hybrid crystallization gate
# -------------------------------------------------------------------


@pytest.mark.usefixtures("base")
class TestImportanceGatedCrystallization:
    """Hybrid gate: high-importance memories crystallize at any rc; standard path at rc=3.

    The ``_setup`` fixture keeps spacing active (MIN_REINFORCEMENT_SPAN_DAYS=2)
    specifically to prove the high-importance path ignores it.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        from core.lifecycle import LifecycleManager
        monkeypatch.setattr(LifecycleManager, "MIN_REINFORCEMENT_SPAN_DAYS", 2)
        monkeypatch.setattr(LifecycleManager, "CRYSTALLIZE_IMPORTANCE_THRESH", 0.75)

    def test_high_importance_rc0_promotes(self, manager):
        """importance >= thresh promotes at rc=0 — no reinforcement required."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=0, importance=0.80)

        can, reason = manager.can_promote(memory_id)
        assert can is True
        assert 'high-importance' in reason.lower()

    def test_high_importance_rc1_promotes(self, manager):
        """importance >= thresh promotes at rc=1."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=1, importance=0.80)
        _add_reinforcement_log(memory_id, datetime(2026, 3, 1, 10, 0), "s1")

        can, reason = manager.can_promote(memory_id)
        assert can is True
        assert 'high-importance' in reason.lower()

    def test_high_importance_ignores_spacing(self, manager):
        """High-importance path promotes even with same-day (unspaced) reinforcements."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=2, importance=0.90)
        same_day = datetime(2026, 3, 15, 9, 0)
        _add_reinforcement_log(memory_id, same_day, "s1")
        _add_reinforcement_log(memory_id, same_day.replace(hour=14), "s2")

        can, reason = manager.can_promote(memory_id)
        assert can is True
        assert 'high-importance' in reason.lower()

    def test_importance_at_threshold_qualifies(self, manager):
        """importance == thresh exactly is accepted (>= boundary), at rc=0."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=0, importance=0.75)

        can, reason = manager.can_promote(memory_id)
        assert can is True

    def test_low_importance_rc0_blocks(self, manager):
        """importance < thresh AND rc=0 -> blocked on standard path (need 3+)."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=0, importance=0.60)

        can, reason = manager.can_promote(memory_id)
        assert can is False
        assert 'need 3+' in reason.lower()

    def test_low_importance_rc2_blocks(self, manager):
        """importance < thresh AND rc=2 -> still blocked on standard path (need 3+)."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=2, importance=0.60)
        base = datetime(2026, 3, 1, 10, 0)
        _add_reinforcement_log(memory_id, base, "s1")
        _add_reinforcement_log(memory_id, base + timedelta(days=2), "s2")

        can, reason = manager.can_promote(memory_id)
        assert can is False
        assert 'need 3+' in reason.lower()

    def test_rc3_promotes_regardless_of_importance(self, manager):
        """rc >= 3 with low importance still crystallizes via standard path."""
        memory_id = _create_memory(stage='consolidated', reinforcement_count=3, importance=0.40)
        base = datetime(2026, 3, 1, 10, 0)
        for i in range(3):
            _add_reinforcement_log(memory_id, base + timedelta(days=i * 2), f"s{i}")

        can, reason = manager.can_promote(memory_id)
        assert can is True

    def test_get_promotion_candidates_filters_by_importance(self, manager):
        """get_promotion_candidates returns high-importance memories at any rc, not low ones."""
        hi_id = _create_memory(
            stage='consolidated', title='High importance', reinforcement_count=0, importance=0.80
        )
        lo_id = _create_memory(
            stage='consolidated', title='Low importance', reinforcement_count=2, importance=0.50
        )
        base = datetime(2026, 3, 1, 10, 0)
        _add_reinforcement_log(lo_id, base, "s3")
        _add_reinforcement_log(lo_id, base + timedelta(days=2), "s4")

        candidates = manager.get_promotion_candidates()
        ids = [c['id'] for c in candidates]
        assert hi_id in ids
        assert lo_id not in ids
