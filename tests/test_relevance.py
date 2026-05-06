"""
Tests for the RelevanceEngine -- scoring, archival, and rehydration.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.relevance import RelevanceEngine, ARCHIVE_THRESHOLD, REHYDRATE_THRESHOLD
from core.database import init_db, close_db, get_base_dir, get_db_path, get_vec_store
from core.models import Memory, ConsolidationLog, RetrievalLog, db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_new_relevance_flags(monkeypatch):
    """Disable saturation_decay and integration_factor for existing tests.

    These features change the formula and would break existing score thresholds.
    They're tested separately in test_saturation_integration.py.
    """
    import core.flags
    monkeypatch.setattr(core.flags, "_cache", {
        "saturation_decay": False,
        "integration_factor": False,
    })


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def engine(base):
    return RelevanceEngine()


def _create_memory(title, stage="consolidated", importance=0.5,
                    days_ago=0, usage_count=0, injection_count=0,
                    project_context=None):
    """Helper to create a memory with specific age and usage."""
    now = datetime.now()
    past = (now - timedelta(days=days_ago)).isoformat()

    mem = Memory.create(
        stage=stage,
        title=title,
        summary=f"Summary of {title}",
        content=f"Content about {title}",
        tags=json.dumps([title.split()[0].lower()]),
        importance=importance,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )

    # Memory.save() overrides updated_at, so we must use raw SQL to set
    # past timestamps and other fields after creation.
    updates = {"updated_at": past, "created_at": past}
    if days_ago > 0:
        updates["last_injected_at"] = past
        if usage_count > 0:
            updates["last_used_at"] = past
    if usage_count > 0:
        updates["usage_count"] = usage_count
    if injection_count > 0:
        updates["injection_count"] = injection_count
    if project_context:
        updates["project_context"] = project_context

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [mem.id]
    db.execute_sql(f"UPDATE memories SET {set_clause} WHERE id = ?", values)

    return mem.id


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

class TestComputeRelevance:
    def test_fresh_high_importance_scores_high(self, engine, base):
        mid = _create_memory("Important Fresh", importance=0.9, days_ago=1)
        memory = Memory.get_by_id(mid)
        score = engine.compute_relevance(memory)
        assert score > 0.7

    def test_old_low_importance_scores_low(self, engine, base):
        mid = _create_memory("Old Boring", importance=0.15, days_ago=180)
        memory = Memory.get_by_id(mid)
        score = engine.compute_relevance(memory)
        assert score < 0.3

    def test_recency_decays_over_time(self, engine, base):
        mid1 = _create_memory("Fresh One", importance=0.5, days_ago=1)
        mid2 = _create_memory("Old One", importance=0.5, days_ago=120)

        score_fresh = engine.compute_relevance(Memory.get_by_id(mid1))
        score_old = engine.compute_relevance(Memory.get_by_id(mid2))
        assert score_fresh > score_old

    def test_usage_boosts_relevance(self, engine, base):
        mid_used = _create_memory("Used Memory", importance=0.5,
                                   usage_count=5, injection_count=10)
        mid_unused = _create_memory("Unused Memory", importance=0.5,
                                     usage_count=0, injection_count=10)

        score_used = engine.compute_relevance(Memory.get_by_id(mid_used))
        score_unused = engine.compute_relevance(Memory.get_by_id(mid_unused))
        assert score_used > score_unused

    def test_context_match_boosts_relevance(self, engine, base):
        mid = _create_memory("Project Mem", importance=0.5,
                              project_context="/home/user/myproject")
        memory = Memory.get_by_id(mid)

        score_match = engine.compute_relevance(memory, project_context="/home/user/myproject")
        score_nomatch = engine.compute_relevance(memory, project_context="/other/project")
        assert score_match > score_nomatch

    def test_score_is_bounded_zero_one(self, engine, base):
        mid = _create_memory("Extreme", importance=1.0, days_ago=0,
                              usage_count=100, injection_count=1)
        memory = Memory.get_by_id(mid)
        score = engine.compute_relevance(memory, project_context="match")
        assert 0.0 <= score <= 1.0

    def test_very_old_memory_has_low_but_nonzero_score(self, engine, base):
        mid = _create_memory("Ancient", importance=0.5, days_ago=365)
        memory = Memory.get_by_id(mid)
        score = engine.compute_relevance(memory)
        assert 0.0 < score < 0.3


# ---------------------------------------------------------------------------
# Archival
# ---------------------------------------------------------------------------

class TestArchival:
    def test_stale_memory_is_archival_candidate(self, engine, base):
        _create_memory("Stale Mem", importance=0.05, days_ago=300)
        candidates = engine.get_archival_candidates()
        assert len(candidates) >= 1
        assert candidates[0].title == "Stale Mem"

    def test_fresh_memory_is_not_archival_candidate(self, engine, base):
        _create_memory("Fresh Mem", importance=0.8, days_ago=1)
        candidates = engine.get_archival_candidates()
        assert len(candidates) == 0

    def test_archive_stale_sets_archived_at(self, engine, base):
        mid = _create_memory("To Archive", importance=0.05, days_ago=300)
        archived = engine.archive_stale()
        assert len(archived) == 1

        mem = Memory.get_by_id(mid)
        assert mem.archived_at is not None

    def test_archived_excluded_from_list_by_stage(self, engine, base):
        mid = _create_memory("Hidden", importance=0.05, days_ago=300)
        engine.archive_stale()

        active = list(Memory.by_stage("consolidated"))
        assert not any(m.id == mid for m in active)

    def test_archived_included_with_flag(self, engine, base):
        mid = _create_memory("Hidden But Findable", importance=0.05, days_ago=300)
        engine.archive_stale()

        all_memories = list(Memory.by_stage("consolidated", include_archived=True))
        assert any(m.id == mid for m in all_memories)

    def test_archive_logs_consolidation(self, engine, base):
        _create_memory("Log Test", importance=0.05, days_ago=300)
        engine.archive_stale()

        row = ConsolidationLog.get_or_none(ConsolidationLog.to_stage == 'archived')
        assert row is not None

    def test_instinctive_memories_not_archived(self, engine, base):
        """Instinctive memories should never be archival candidates."""
        _create_memory("Core Behavior", stage="instinctive",
                        importance=0.05, days_ago=300)
        candidates = engine.get_archival_candidates()
        assert len(candidates) == 0

    def test_ephemeral_memories_not_archived(self, engine, base):
        """Ephemeral memories are handled by consolidation, not archival."""
        _create_memory("Scratch", stage="ephemeral",
                        importance=0.05, days_ago=300)
        candidates = engine.get_archival_candidates()
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Rehydration
# ---------------------------------------------------------------------------

class TestRehydration:
    def test_rehydrate_for_matching_context(self, engine, base):
        mid = _create_memory("Project Specific", importance=0.7,
                              days_ago=10, project_context="/my/project")
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        rehydrated = engine.rehydrate_for_context(project_context="/my/project")
        assert len(rehydrated) >= 1
        assert rehydrated[0].id == mid

    def test_rehydrated_memory_is_active_again(self, engine, base):
        mid = _create_memory("Revived", importance=0.7,
                              days_ago=10, project_context="/my/project")
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        engine.rehydrate_for_context(project_context="/my/project")

        active = list(Memory.by_stage("consolidated"))
        assert any(m.id == mid for m in active)

    def test_irrelevant_archived_not_rehydrated(self, engine, base):
        mid = _create_memory("Wrong Project", importance=0.15,
                              days_ago=150, project_context="/other/project")
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        rehydrated = engine.rehydrate_for_context(project_context="/my/project")
        assert len(rehydrated) == 0

    def test_unarchive_clears_archived_at(self, base):
        mid = _create_memory("Clear Test", importance=0.5)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()
        Memory.update(archived_at=None, updated_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        mem = Memory.get_by_id(mid)
        assert mem.archived_at is None

    def test_list_archived_returns_only_archived(self, base):
        mid1 = _create_memory("Active", importance=0.5)
        mid2 = _create_memory("Archived", importance=0.5)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid2).execute()

        archived = list(Memory.select().where(Memory.archived_at.is_null(False)))
        assert len(archived) == 1
        assert archived[0].id == mid2

    def test_rehydration_by_observation(self, engine, base):
        mid = _create_memory("Payment Pipeline Locking",
                              importance=0.6, days_ago=30)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        matches = engine.find_rehydration_by_observation(
            "We need to fix the payment pipeline deadlock issue"
        )
        assert any(m.id == mid for m in matches)

    def test_rehydration_by_observation_ignores_active(self, engine, base):
        _create_memory("Active Payment", importance=0.6)
        # Not archived -- should not appear
        matches = engine.find_rehydration_by_observation("payment processing update")
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# NLTK rehydration (D-07)
# ---------------------------------------------------------------------------


class TestNLTKRehydration:
    """NLTK stemming and stopword filtering in find_rehydration_by_observation (D-07)."""

    def test_stemmed_observation_finds_archived_memory(self, engine, base):
        """Observation with inflected form should still match via Porter stem."""
        mid = _create_memory(
            'Payment Pipeline Locking',
            importance=0.6,
            days_ago=30,
        )
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        matches = engine.find_rehydration_by_observation(
            'payments pipeline deadlock needs investigation'
        )

        assert any(m.id == mid for m in matches)

    def test_rehydration_fallback_when_nltk_unavailable(self, engine, base, monkeypatch):
        """LookupError from nltk.data.find must not raise -- returns a list."""
        import nltk as _nltk

        monkeypatch.setattr(
            _nltk.data,
            'find',
            lambda *a, **kw: (_ for _ in ()).throw(LookupError('not found')),
        )

        mid = _create_memory('Cache Invalidation', importance=0.6, days_ago=10)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        result = engine.find_rehydration_by_observation('cache invalidation strategy')

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Inhibition (retrieval-induced forgetting)
# ---------------------------------------------------------------------------

class TestInhibition:
    """Subsumed memories should be inhibited from rehydration."""

    def test_subsumed_memory_not_rehydrated(self, engine, base):
        """A memory with subsumed_by set should never be a rehydration candidate."""
        mid = _create_memory("Source Episode", importance=0.9,
                              days_ago=5, project_context="/my/project")
        Memory.update(subsumed_by="crystal-123").where(Memory.id == mid).execute()
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        candidates = engine.get_rehydration_candidates(project_context="/my/project")
        assert not any(c.id == mid for c in candidates)

    def test_non_subsumed_archived_still_rehydrates(self, engine, base):
        """Regular archived memories (no subsumed_by) should still rehydrate."""
        mid = _create_memory("Normally Archived", importance=0.7,
                              days_ago=10, project_context="/my/project")
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        candidates = engine.get_rehydration_candidates(project_context="/my/project")
        assert any(c.id == mid for c in candidates)

    def test_subsumed_memory_not_found_by_observation(self, engine, base):
        """Subsumed memories should not match observation-based rehydration."""
        mid = _create_memory("Payment Pipeline Locking",
                              importance=0.6, days_ago=30)
        Memory.update(subsumed_by="crystal-456").where(Memory.id == mid).execute()
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        matches = engine.find_rehydration_by_observation(
            "We need to fix the payment pipeline deadlock issue"
        )
        assert not any(m.id == mid for m in matches)

    def test_rehydrate_for_context_skips_subsumed(self, engine, base):
        """Full rehydration cycle should skip subsumed memories."""
        mid = _create_memory("Subsumed Context", importance=0.8,
                              days_ago=5, project_context="/my/project")
        Memory.update(subsumed_by="crystal-789").where(Memory.id == mid).execute()
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        rehydrated = engine.rehydrate_for_context(project_context="/my/project")
        assert not any(r.id == mid for r in rehydrated)

        # Verify it's still archived
        mem = Memory.get_by_id(mid)
        assert mem.archived_at is not None

    def test_maintenance_does_not_rehydrate_subsumed(self, engine, base):
        """run_maintenance should not rehydrate subsumed memories."""
        mid = _create_memory("Subsumed Maintenance", importance=0.8,
                              days_ago=5, project_context="/my/project")
        Memory.update(subsumed_by="crystal-abc").where(Memory.id == mid).execute()
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        result = engine.run_maintenance(project_context="/my/project")
        rehydrated_ids = [r.id for r in result["rehydrated"]]
        assert mid not in rehydrated_ids


# ---------------------------------------------------------------------------
# Maintenance cycle
# ---------------------------------------------------------------------------

class TestMaintenance:
    def test_run_maintenance_archives_and_rehydrates(self, engine, base):
        # Create one stale memory (will be archived)
        _create_memory("Stale One", importance=0.05, days_ago=300)

        # Create and archive one relevant memory (will be rehydrated)
        mid = _create_memory("Relevant Archived", importance=0.7,
                              days_ago=5, project_context="/my/project")
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        result = engine.run_maintenance(project_context="/my/project")
        assert len(result["archived"]) >= 1
        assert len(result["rehydrated"]) >= 1


# ---------------------------------------------------------------------------
# Score all
# ---------------------------------------------------------------------------

class TestScoreAll:
    def test_score_all_returns_sorted_by_relevance(self, engine, base):
        _create_memory("High", importance=0.9, days_ago=1)
        _create_memory("Low", importance=0.2, days_ago=100)

        scored = engine.score_all()
        assert len(scored) == 2
        assert scored[0]["relevance"] >= scored[1]["relevance"]

    def test_score_all_excludes_ephemeral(self, engine, base):
        _create_memory("Ephemeral", stage="ephemeral")
        _create_memory("Consolidated", stage="consolidated")

        scored = engine.score_all()
        assert all(s["stage"] != "ephemeral" for s in scored)

    def test_score_all_excludes_archived(self, engine, base):
        mid = _create_memory("Archived", importance=0.5)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        scored = engine.score_all()
        assert not any(s["id"] == mid for s in scored)


# ---------------------------------------------------------------------------
# Days since last activity
# ---------------------------------------------------------------------------

class TestDaysSinceActivity:
    def test_uses_most_recent_timestamp(self, engine, base):
        mid = _create_memory("Multi Timestamp", days_ago=0)
        memory = Memory.get_by_id(mid)

        days = engine._days_since_last_activity(memory)
        assert days < 1.0

    def test_old_memory_reports_many_days(self, engine, base):
        mid = _create_memory("Old", days_ago=90)
        memory = Memory.get_by_id(mid)
        days = engine._days_since_last_activity(memory)
        assert 89 < days < 91

    def test_no_timestamps_returns_365(self, engine):
        memory = {}  # no timestamp fields
        days = engine._days_since_last_activity(memory)
        assert days == 365.0


# ---------------------------------------------------------------------------
# Semantic rehydration (D-09, D-10)
# ---------------------------------------------------------------------------


class TestSemanticRehydration:
    """Tests for _find_semantic_matches supplementing FTS in find_rehydration_by_observation."""

    def test_semantic_match_supplements_fts(self, engine, base):
        """A memory that FTS would miss is found via the semantic path."""
        mid = _create_memory("Zymurgical Process", importance=0.6, days_ago=30)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()
        archived_mem = Memory.get_by_id(mid)

        fake_embedding = b"\x00" * 512

        vec_store = get_vec_store()
        with patch("core.embeddings.embed_text", return_value=fake_embedding), \
             patch.object(vec_store, "search_vector", return_value=[{"memory_id": mid, "distance": 0.1}]):
            matches = engine.find_rehydration_by_observation("software deployment pipeline")

        assert any(m.id == mid for m in matches), (
            "The archived memory should be found via the semantic supplement path"
        )

    def test_semantic_match_deduplicates_fts_results(self, engine, base):
        """A memory returned by both FTS and semantic paths appears exactly once."""
        mid = _create_memory("Payment Pipeline Locking", importance=0.6, days_ago=30)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        fake_embedding = b"\x00" * 512

        vec_store = get_vec_store()
        with patch("core.embeddings.embed_text", return_value=fake_embedding), \
             patch.object(vec_store, "search_vector", return_value=[{"memory_id": mid, "distance": 0.1}]):
            matches = engine.find_rehydration_by_observation(
                "We need to fix the payment pipeline deadlock issue"
            )

        ids = [m.id for m in matches]
        assert ids.count(mid) == 1, (
            "Memory returned by both FTS and semantic paths should appear only once"
        )

    def test_semantic_rehydration_fallback_when_unavailable(self, engine, base):
        """When embed_text returns None, _find_semantic_matches returns [] and FTS still works."""
        mid = _create_memory("Payment Pipeline Locking", importance=0.6, days_ago=30)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mid).execute()

        with patch("core.embeddings.embed_text", return_value=None):
            matches = engine.find_rehydration_by_observation(
                "We need to fix the payment pipeline deadlock issue"
            )

        assert any(m.id == mid for m in matches), (
            "FTS path should work independently of semantic availability"
        )


# ---------------------------------------------------------------------------
# Schema-promoted column weighting (Wave 3a → Wave 3c)
# ---------------------------------------------------------------------------

class TestSchemaWeighting:
    """Verify affect_valence, temporal_scope, and confidence factor into relevance."""

    @pytest.fixture
    def w5_engine(self, base, monkeypatch):
        # Re-enable schema flags overridden by the autouse fixture.
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {
            "saturation_decay": False,
            "integration_factor": False,
            "affect_weighted_retrieval": True,
            "temporal_scope_weighting": True,
            "confidence_weighting": True,
        })
        return RelevanceEngine()

    def _set_w5(self, mem_id, **fields):
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [mem_id]
        db.execute_sql(f"UPDATE memories SET {set_clause} WHERE id = ?", values)

    def test_friction_valence_boosts_relevance(self, w5_engine, base):
        """affect_valence=friction → ~20% boost over neutral."""
        mid_neutral = _create_memory("Neutral mem", importance=0.5, days_ago=1)
        mid_friction = _create_memory("Friction mem", importance=0.5, days_ago=1)
        self._set_w5(mid_friction, affect_valence="friction")

        s_neutral = w5_engine.compute_relevance(Memory.get_by_id(mid_neutral))
        s_friction = w5_engine.compute_relevance(Memory.get_by_id(mid_friction))
        assert s_friction > s_neutral
        assert pytest.approx(s_friction / s_neutral, abs=0.02) == 1.20

    def test_session_local_scope_penalizes(self, w5_engine, base):
        """temporal_scope=session-local → ~40% penalty."""
        mid_durable = _create_memory("Durable", importance=0.5, days_ago=1)
        mid_local = _create_memory("Local", importance=0.5, days_ago=1)
        self._set_w5(mid_durable, temporal_scope="cross-session-durable")
        self._set_w5(mid_local, temporal_scope="session-local")

        s_durable = w5_engine.compute_relevance(Memory.get_by_id(mid_durable))
        s_local = w5_engine.compute_relevance(Memory.get_by_id(mid_local))
        assert s_local < s_durable

    def test_low_confidence_demoted(self, w5_engine, base):
        """confidence=0.3 → factor 0.79; confidence=1.0 → factor 1.0."""
        mid_high = _create_memory("High conf", importance=0.5, days_ago=1)
        mid_low = _create_memory("Low conf", importance=0.5, days_ago=1)
        self._set_w5(mid_high, confidence=1.0)
        self._set_w5(mid_low, confidence=0.3)

        s_high = w5_engine.compute_relevance(Memory.get_by_id(mid_high))
        s_low = w5_engine.compute_relevance(Memory.get_by_id(mid_low))
        assert s_low < s_high

    def test_null_fields_no_op(self, w5_engine, base):
        """Memories without W5 cols (legacy rows) score same as with-flags-disabled."""
        mid = _create_memory("Legacy", importance=0.5, days_ago=1)
        # All W5 fields NULL by default.
        score = w5_engine.compute_relevance(Memory.get_by_id(mid))
        assert 0.0 <= score <= 1.0

    def test_flag_off_disables_weighting(self, base, monkeypatch):
        """affect_weighted_retrieval=False → friction valence has no effect."""
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {
            "saturation_decay": False,
            "integration_factor": False,
            "affect_weighted_retrieval": False,
            "temporal_scope_weighting": False,
            "confidence_weighting": False,
        })
        engine = RelevanceEngine()

        mid_neutral = _create_memory("Neutral", importance=0.5, days_ago=1)
        mid_friction = _create_memory("Friction", importance=0.5, days_ago=1)
        self._set_w5(mid_friction, affect_valence="friction")

        s_neutral = engine.compute_relevance(Memory.get_by_id(mid_neutral))
        s_friction = engine.compute_relevance(Memory.get_by_id(mid_friction))
        assert pytest.approx(s_neutral, abs=0.01) == s_friction
