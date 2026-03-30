"""Tests for saturation decay (Phase 17) and integration factor (Phase 18)."""

import json
from datetime import datetime

import pytest

from core.database import init_db, close_db
from core.models import Memory, ThreadMember, NarrativeThread
from core.relevance import RelevanceEngine


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


@pytest.fixture
def engine():
    return RelevanceEngine()


# -----------------------------------------------------------------------
# Phase 17: Saturation Decay
# -----------------------------------------------------------------------

class TestSaturationDecay:

    def test_no_penalty_when_used(self, store, engine):
        mem = Memory.create(
            stage="crystallized", title="Used memory",
            injection_count=10, usage_count=8, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score = engine.compute_relevance(mem)
        # unused = 10 - 8 = 2, penalty = 2 * 0.05 = 0.1
        # Still relatively small
        assert score > 0.3

    def test_heavy_penalty_for_unused(self, store, engine):
        mem_unused = Memory.create(
            stage="crystallized", title="Never used",
            injection_count=10, usage_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        mem_fresh = Memory.create(
            stage="crystallized", title="Fresh memory",
            injection_count=0, usage_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score_unused = engine.compute_relevance(mem_unused)
        score_fresh = engine.compute_relevance(mem_fresh)
        # SC2: unused memory should score lower than fresh one
        assert score_unused < score_fresh

    def test_penalty_caps_at_0_3(self, store, engine):
        mem = Memory.create(
            stage="crystallized", title="Very stale",
            injection_count=100, usage_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        # unused = 100, penalty = min(0.3, 100*0.05) = 0.3
        score = engine.compute_relevance(mem)
        assert score >= 0.0  # doesn't go negative

    def test_usage_resets_penalty(self, store, engine):
        mem = Memory.create(
            stage="crystallized", title="Was stale now used",
            injection_count=10, usage_count=10, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        # unused = 0, penalty = 0 (saturation gone)
        # May still be reduced by integration_factor (isolated memory)
        score = engine.compute_relevance(mem)
        assert score > 0.3  # no saturation penalty applied

    def test_flag_disabled_no_penalty(self, store, engine, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"saturation_decay": False, "integration_factor": False})
        mem_with = Memory.create(
            stage="crystallized", title="Unused with flag",
            injection_count=20, usage_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score_no_flag = engine.compute_relevance(mem_with)

        monkeypatch.setattr(core.flags, "_cache", {"saturation_decay": True, "integration_factor": False})
        score_with_flag = engine.compute_relevance(mem_with)

        # With saturation enabled, unused memory should score lower
        assert score_no_flag > score_with_flag


# -----------------------------------------------------------------------
# Phase 18: Integration Factor
# -----------------------------------------------------------------------

class TestIntegrationFactor:

    def test_isolated_memory_lower_relevance(self, store, engine):
        mem_isolated = Memory.create(
            stage="crystallized", title="Lone wolf",
            tags=json.dumps(["unique_tag_xyz"]),
            reinforcement_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        mem_connected = Memory.create(
            stage="crystallized", title="Part of group",
            tags=json.dumps(["shared_tag"]),
            reinforcement_count=3, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        # Create another memory with same tag to make it "connected"
        Memory.create(
            stage="crystallized", title="Also shared",
            tags=json.dumps(["shared_tag"]),
            importance=0.5,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score_isolated = engine.compute_relevance(mem_isolated)
        score_connected = engine.compute_relevance(mem_connected)
        assert score_isolated < score_connected

    def test_thread_membership_restores_factor(self, store, engine):
        mem = Memory.create(
            stage="crystallized", title="In thread",
            tags=json.dumps(["unique_only"]),
            reinforcement_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        # Score without thread
        score_before = engine.compute_relevance(mem)

        # Add to a thread
        thread = NarrativeThread.create(title="Test thread")
        ThreadMember.create(thread_id=thread.id, memory_id=mem.id, position=0)

        score_after = engine.compute_relevance(mem)
        assert score_after > score_before

    def test_tag_overlap_restores_factor(self, store, engine):
        mem = Memory.create(
            stage="crystallized", title="Has overlap",
            tags=json.dumps(["common_tag"]),
            reinforcement_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score_before = engine.compute_relevance(mem)

        # Create another memory with overlapping tag
        Memory.create(
            stage="crystallized", title="Shares tag",
            tags=json.dumps(["common_tag"]),
            importance=0.5,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score_after = engine.compute_relevance(mem)
        assert score_after > score_before

    def test_flag_disabled_no_penalty(self, store, engine, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"saturation_decay": True, "integration_factor": False})
        mem = Memory.create(
            stage="crystallized", title="Isolated no flag",
            tags=json.dumps(["unique_xyz"]),
            reinforcement_count=0, importance=0.7,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        score = engine.compute_relevance(mem)
        # Without integration penalty, fully isolated still gets full factor
        assert score > 0.3
