"""
Tests for lifecycle state machine with promotion/demotion rules.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.lifecycle import LifecycleManager
from core.storage import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary MemoryStore."""
    return MemoryStore(base_dir=str(tmp_path / 'memory'))


@pytest.fixture
def manager(store):
    """Create a LifecycleManager."""
    return LifecycleManager(store)


def _add_reinforcement_log(store, memory_id, timestamp, session_id="test-session"):
    """Helper: insert a consolidation_log 'promoted' entry at a specific timestamp."""
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """INSERT INTO consolidation_log
               (timestamp, session_id, action, memory_id, from_stage, to_stage, rationale)
               VALUES (?, ?, 'promoted', ?, 'consolidated', 'consolidated', 'Reinforced')""",
            (timestamp.isoformat(), session_id, memory_id),
        )
        conn.commit()


def test_promote_ephemeral_to_consolidated(store, manager):
    """Test promotion from ephemeral to consolidated stage."""
    # Create ephemeral memory
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Test Memory',
            'summary': 'A test memory'
        }
    )

    # Promote to consolidated
    new_stage = manager.promote(memory_id, rationale='Ready for consolidation')

    assert new_stage == 'consolidated'

    # Verify memory moved
    memory = store.get(memory_id)
    assert memory['stage'] == 'consolidated'
    assert 'consolidated/test_memory.md' in memory['file_path']

    # Verify file physically moved
    file_path = Path(memory['file_path'])
    assert file_path.exists()
    assert file_path.parent.name == 'consolidated'

    # Verify transition logged
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute('''
            SELECT * FROM consolidation_log
            WHERE memory_id = ? AND action = 'promoted'
        ''', (memory_id,))
        log = cursor.fetchone()

        assert log is not None
        assert log['from_stage'] == 'ephemeral'
        assert log['to_stage'] == 'consolidated'
        assert log['rationale'] == 'Ready for consolidation'


def test_promote_consolidated_to_crystallized_with_reinforcement(store, manager):
    """Test promotion to crystallized requires 3+ reinforcements (D-07)."""
    # Create consolidated memory with 3 reinforcements
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'consolidated',
            'title': 'Test Memory',
            'summary': 'A test memory',
            'reinforcement_count': 3
        }
    )

    # Should be eligible for promotion
    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is True
    assert '3 reinforcements' in reason

    # Promote to crystallized
    new_stage = manager.promote(memory_id, rationale='Met reinforcement threshold')
    assert new_stage == 'crystallized'


def test_cannot_promote_without_reinforcement(store, manager):
    """Test promotion to crystallized blocked without 3 reinforcements."""
    # Create consolidated memory with only 2 reinforcements
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'consolidated',
            'title': 'Test Memory',
            'summary': 'A test memory',
            'reinforcement_count': 2
        }
    )

    # Should not be eligible
    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'need 3+' in reason.lower()

    # Attempt to promote should fail
    with pytest.raises(ValueError, match='Cannot promote'):
        manager.promote(memory_id, rationale='Trying to promote')


def test_promote_crystallized_to_instinctive(store, manager):
    """Test promotion to instinctive requires importance > 0.85 and 10+ sessions."""
    # Create crystallized memory with high importance
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'crystallized',
            'title': 'Test Memory',
            'summary': 'A test memory',
            'importance': 0.9,
            'usage_count': 15
        }
    )

    # Record usage in 10+ unique sessions
    for i in range(10):
        session_id = f'session_{i}'
        store.record_injection(memory_id, session_id)
        store.record_usage(memory_id, session_id)

    # Should be eligible
    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is True
    assert 'High importance' in reason
    assert '10' in reason

    # Promote to instinctive
    new_stage = manager.promote(memory_id, rationale='Proven critical memory')
    assert new_stage == 'instinctive'


def test_cannot_promote_low_importance_to_instinctive(store, manager):
    """Test promotion to instinctive blocked by low importance."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'crystallized',
            'title': 'Test Memory',
            'importance': 0.7,  # Too low
            'usage_count': 15
        }
    )

    # Record 10+ sessions
    for i in range(10):
        session_id = f'session_{i}'
        store.record_injection(memory_id, session_id)
        store.record_usage(memory_id, session_id)

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'too low' in reason.lower()


def test_cannot_skip_stages_on_promotion(store, manager):
    """Test that promotion cannot skip stages."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Test Memory'
        }
    )

    # Try to jump directly to crystallized
    is_valid = manager.validate_transition('ephemeral', 'crystallized')
    assert is_valid is False

    # Try to jump to instinctive
    is_valid = manager.validate_transition('ephemeral', 'instinctive')
    assert is_valid is False


def test_demote_memory(store, manager):
    """Test demotion of memory to lower stage."""
    # Create crystallized memory
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'crystallized',
            'title': 'Test Memory'
        }
    )

    # Demote to consolidated
    new_stage = manager.demote(memory_id, rationale='Low usage')

    assert new_stage == 'consolidated'

    # Verify memory moved
    memory = store.get(memory_id)
    assert memory['stage'] == 'consolidated'

    # Verify transition logged
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute('''
            SELECT * FROM consolidation_log
            WHERE memory_id = ? AND action = 'demoted'
        ''', (memory_id,))
        log = cursor.fetchone()

        assert log is not None
        assert log['from_stage'] == 'crystallized'
        assert log['to_stage'] == 'consolidated'
        assert log['rationale'] == 'Low usage'


def test_demotion_can_skip_stages(store, manager):
    """Test that demotion can skip stages (unlike promotion)."""
    # Demotion from instinctive to consolidated is valid
    is_valid = manager.validate_transition('instinctive', 'consolidated')
    assert is_valid is True

    # Demotion from crystallized to ephemeral is valid
    is_valid = manager.validate_transition('crystallized', 'ephemeral')
    assert is_valid is True


def test_deprecate_memory(store, manager):
    """Test deprecation moves memory to archived directory."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Test Memory'
        }
    )

    original_path = Path(store.get(memory_id)['file_path'])
    assert original_path.exists()

    # Deprecate memory
    manager.deprecate(memory_id, rationale='No longer relevant')

    # Verify file moved to archived
    archived_path = store.base_dir / 'archived' / 'test_memory.md'
    assert archived_path.exists()
    assert not original_path.exists()

    # Verify removed from index
    with pytest.raises(ValueError, match='Memory not found'):
        store.get(memory_id)

    # Verify deprecation logged
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute('''
            SELECT * FROM consolidation_log
            WHERE memory_id = ? AND action = 'deprecated'
        ''', (memory_id,))
        log = cursor.fetchone()

        assert log is not None
        assert log['from_stage'] == 'ephemeral'
        assert log['to_stage'] == 'archived'


def test_get_promotion_candidates(store, manager):
    """Test retrieval of memories eligible for promotion to crystallized."""
    # Create consolidated memories with varying reinforcement counts
    memory_1 = store.create(
        path='memory_1.md',
        content='Content 1',
        metadata={
            'stage': 'consolidated',
            'title': 'Memory 1',
            'reinforcement_count': 5  # Eligible
        }
    )

    memory_2 = store.create(
        path='memory_2.md',
        content='Content 2',
        metadata={
            'stage': 'consolidated',
            'title': 'Memory 2',
            'reinforcement_count': 2  # Not eligible
        }
    )

    memory_3 = store.create(
        path='memory_3.md',
        content='Content 3',
        metadata={
            'stage': 'consolidated',
            'title': 'Memory 3',
            'reinforcement_count': 3  # Eligible (exactly 3)
        }
    )

    candidates = manager.get_promotion_candidates()

    # Should return only memories with reinforcement_count >= 3
    assert len(candidates) == 2
    candidate_ids = [c['id'] for c in candidates]
    assert memory_1 in candidate_ids
    assert memory_3 in candidate_ids
    assert memory_2 not in candidate_ids


def test_get_demotion_candidates(store, manager):
    """Test retrieval of memories with high injection but no usage (D-09)."""
    # Create memory with high injection count but zero usage
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'crystallized',
            'title': 'Unused Memory'
        }
    )

    # Inject 12 times without usage
    for i in range(12):
        store.record_injection(memory_id, f'session_{i}')

    candidates = manager.get_demotion_candidates()

    assert len(candidates) == 1
    assert candidates[0]['id'] == memory_id
    assert candidates[0]['injection_count'] >= 10
    assert candidates[0]['usage_count'] == 0
    assert 'never used' in candidates[0]['reason'].lower()


def test_get_demotion_candidates_ignores_used_memories(store, manager):
    """Test that demotion candidates exclude memories that are actually used."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'crystallized',
            'title': 'Used Memory'
        }
    )

    # Inject 15 times and use 5 times
    for i in range(15):
        session_id = f'session_{i}'
        store.record_injection(memory_id, session_id)
        if i < 5:
            store.record_usage(memory_id, session_id)

    candidates = manager.get_demotion_candidates()

    # Should not be a candidate because it was used
    assert len(candidates) == 0


def test_get_deprecation_candidates(store, manager):
    """Test retrieval of stale memories for deprecation."""
    # Create old ephemeral memory
    memory_id = store.create(
        path='old_memory.md',
        content='Old content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Old Memory'
        }
    )

    # Manually set last_injected_at to 40 days ago
    old_date = (datetime.now() - timedelta(days=40)).isoformat()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            'UPDATE memories SET last_injected_at = ? WHERE id = ?',
            (old_date, memory_id)
        )
        conn.commit()

    candidates = manager.get_deprecation_candidates(stale_sessions=30)

    assert len(candidates) == 1
    assert candidates[0]['id'] == memory_id
    assert 'No activity' in candidates[0]['reason']


def test_cannot_promote_from_highest_stage(store, manager):
    """Test that instinctive memories cannot be promoted further."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'instinctive',
            'title': 'Instinctive Memory'
        }
    )

    can_promote, reason = manager.can_promote(memory_id)
    assert can_promote is False
    assert 'highest stage' in reason.lower()

    with pytest.raises(ValueError, match='already at highest stage'):
        manager.promote(memory_id, rationale='Trying to promote')


def test_cannot_demote_from_lowest_stage(store, manager):
    """Test that ephemeral memories cannot be demoted further."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Ephemeral Memory'
        }
    )

    with pytest.raises(ValueError, match='already at lowest stage'):
        manager.demote(memory_id, rationale='Trying to demote')


def test_validate_transition_invalid_stages(manager):
    """Test transition validation with invalid stage names."""
    # Invalid source stage
    is_valid = manager.validate_transition('invalid', 'consolidated')
    assert is_valid is False

    # Invalid target stage
    is_valid = manager.validate_transition('ephemeral', 'invalid')
    assert is_valid is False

    # Both invalid
    is_valid = manager.validate_transition('invalid1', 'invalid2')
    assert is_valid is False


def test_validate_transition_same_stage(manager):
    """Test that transitioning to the same stage is invalid."""
    is_valid = manager.validate_transition('consolidated', 'consolidated')
    assert is_valid is False


def test_promotion_updates_file_path(store, manager):
    """Test that promotion physically moves the file and updates path."""
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Test Memory'
        }
    )

    original_memory = store.get(memory_id)
    original_path = Path(original_memory['file_path'])
    assert original_path.exists()
    assert 'ephemeral' in str(original_path)

    # Promote
    manager.promote(memory_id, rationale='Test promotion')

    # Verify new path
    updated_memory = store.get(memory_id)
    new_path = Path(updated_memory['file_path'])
    assert new_path.exists()
    assert 'consolidated' in str(new_path)
    assert not original_path.exists()


def test_multiple_promotions_in_sequence(store, manager):
    """Test promoting a memory through multiple stages."""
    # Start at ephemeral
    memory_id = store.create(
        path='test_memory.md',
        content='Test content',
        metadata={
            'stage': 'ephemeral',
            'title': 'Test Memory',
            'reinforcement_count': 5,
            'importance': 0.9
        }
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
        store.record_injection(memory_id, session_id)
        store.record_usage(memory_id, session_id)

    # Promote to instinctive
    stage = manager.promote(memory_id, rationale='Third promotion')
    assert stage == 'instinctive'

    # Verify all transitions logged
    with sqlite3.connect(store.db_path) as conn:
        cursor = conn.execute('''
            SELECT COUNT(*) FROM consolidation_log
            WHERE memory_id = ? AND action = 'promoted'
        ''', (memory_id,))
        count = cursor.fetchone()[0]
        assert count == 3


# -------------------------------------------------------------------
# Spacing effect tests
# -------------------------------------------------------------------


class TestSpacingEffect:
    """Test brain-inspired spacing effect for promotion quality."""

    def test_burst_reinforcement_blocked(self, store, manager):
        """3 reinforcements on the same day should NOT qualify for promotion."""
        memory_id = store.create(
            path='burst_memory.md',
            content='Burst reinforced content',
            metadata={
                'stage': 'consolidated',
                'title': 'Burst Memory',
                'summary': 'Reinforced rapidly',
                'reinforcement_count': 3,
            },
        )

        # All 3 reinforcements on the same day
        today = datetime.now()
        for i in range(3):
            _add_reinforcement_log(
                store, memory_id,
                today.replace(hour=10 + i, minute=0),
                session_id=f"session-{i}",
            )

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is False
        assert 'spacing' in reason.lower() or 'distinct day' in reason.lower()

    def test_spaced_reinforcement_allowed(self, store, manager):
        """3 reinforcements across 3 different days should qualify."""
        memory_id = store.create(
            path='spaced_memory.md',
            content='Spaced reinforced content',
            metadata={
                'stage': 'consolidated',
                'title': 'Spaced Memory',
                'summary': 'Reinforced over time',
                'reinforcement_count': 3,
            },
        )

        # Reinforcements on 3 different days
        base = datetime(2026, 3, 20, 14, 0)
        for i in range(3):
            _add_reinforcement_log(
                store, memory_id,
                base + timedelta(days=i * 3),
                session_id=f"session-day-{i}",
            )

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True
        assert 'distinct days' in reason.lower() or 'reinforcement' in reason.lower()

    def test_two_days_is_minimum_spacing(self, store, manager):
        """Reinforcements spanning exactly 2 distinct days should pass."""
        memory_id = store.create(
            path='two_day_memory.md',
            content='Two day content',
            metadata={
                'stage': 'consolidated',
                'title': 'Two Day Memory',
                'reinforcement_count': 3,
            },
        )

        day1 = datetime(2026, 3, 20, 10, 0)
        day2 = datetime(2026, 3, 21, 14, 0)
        _add_reinforcement_log(store, memory_id, day1, "s1")
        _add_reinforcement_log(store, memory_id, day1.replace(hour=15), "s2")
        _add_reinforcement_log(store, memory_id, day2, "s3")

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True

    def test_no_log_entries_fallback(self, store, manager):
        """Legacy memories with no consolidation log entries should still promote."""
        memory_id = store.create(
            path='legacy_memory.md',
            content='Legacy content',
            metadata={
                'stage': 'consolidated',
                'title': 'Legacy Memory',
                'reinforcement_count': 5,
            },
        )

        # No consolidation log entries at all — should still be promotable
        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is True

    def test_spacing_does_not_affect_count_check(self, store, manager):
        """Count check still applies — 2 reinforcements fail even if spaced."""
        memory_id = store.create(
            path='low_count_memory.md',
            content='Low count content',
            metadata={
                'stage': 'consolidated',
                'title': 'Low Count Memory',
                'reinforcement_count': 2,
            },
        )

        # Well-spaced but too few
        base = datetime(2026, 3, 10, 12, 0)
        _add_reinforcement_log(store, memory_id, base, "s1")
        _add_reinforcement_log(store, memory_id, base + timedelta(days=7), "s2")

        can_promote, reason = manager.can_promote(memory_id)
        assert can_promote is False
        assert 'need 3+' in reason.lower()

    def test_get_promotion_candidates_respects_spacing(self, store, manager):
        """get_promotion_candidates should filter out burst-reinforced memories."""
        # Memory with spaced reinforcement
        spaced_id = store.create(
            path='spaced.md',
            content='Spaced',
            metadata={
                'stage': 'consolidated',
                'title': 'Spaced',
                'reinforcement_count': 3,
            },
        )
        base = datetime(2026, 3, 1, 10, 0)
        for i in range(3):
            _add_reinforcement_log(store, spaced_id, base + timedelta(days=i * 2), f"s{i}")

        # Memory with burst reinforcement
        burst_id = store.create(
            path='burst.md',
            content='Burst',
            metadata={
                'stage': 'consolidated',
                'title': 'Burst',
                'reinforcement_count': 4,
            },
        )
        same_day = datetime(2026, 3, 15, 9, 0)
        for i in range(4):
            _add_reinforcement_log(store, burst_id, same_day.replace(hour=9 + i), f"b{i}")

        candidates = manager.get_promotion_candidates()
        candidate_ids = [c['id'] for c in candidates]
        assert spaced_id in candidate_ids
        assert burst_id not in candidate_ids
