"""
Tests for core/observability.py — Sprint A WS-A + agentic-memory BLOCKER set (C3).

Covers:
- compute_activation round-trips
- log functions produce valid JSONL
- log functions tolerate missing context fields
- shadow_prune batch performance (< 1 second for 100 memories)
- SHADOW_ONLY flag: shadow mode writes JSONL only, live mode also soft-archives
- flipping SHADOW_ONLY mid-test produces the correct behaviour switch
"""

import json
import math
import time
from pathlib import Path

import pytest

# Use a temp directory so tests never write to the real backfill-output/
import os


@pytest.fixture(autouse=True)
def isolate_obs_dir(tmp_path, monkeypatch):
    """Redirect observability output to a temp directory for every test."""
    obs_out = tmp_path / "observability"
    obs_out.mkdir()
    monkeypatch.setenv("MEMESIS_REPO_ROOT", str(tmp_path))
    # Re-import to pick up the env var — patch the module-level _REPO_ROOT
    import importlib
    import core.observability as obs_mod
    obs_mod._REPO_ROOT = tmp_path
    obs_mod._OBS_DIR = obs_out
    yield obs_out


# ---------------------------------------------------------------------------
# DB fixture used by SHADOW_ONLY tests
# ---------------------------------------------------------------------------

@pytest.fixture
def base(tmp_path):
    """Isolated peewee + FTS database for tests that write Memory rows."""
    from core.database import init_db, close_db
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path
    close_db()


# ---------------------------------------------------------------------------
# compute_activation
# ---------------------------------------------------------------------------


def test_activation_at_age_zero_access_zero_equals_importance():
    from core.observability import compute_activation
    importance = 0.75
    result = compute_activation(importance=importance, age_hours=0.0, decay_tau_hours=48.0, access_count=0)
    # recency = exp(0) = 1.0; access_boost = 1 + log(1) = 1.0
    assert abs(result - importance) < 1e-9


def test_activation_at_age_tau_equals_importance_times_recip_e():
    from core.observability import compute_activation
    importance = 0.6
    tau = 48.0
    result = compute_activation(importance=importance, age_hours=tau, decay_tau_hours=tau, access_count=0)
    # recency = exp(-1) ≈ 0.3679; access_boost = 1.0
    expected = importance * math.exp(-1.0)
    assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"


def test_activation_increases_with_access_count():
    from core.observability import compute_activation
    base = compute_activation(0.5, 10.0, 48.0, 0)
    boosted = compute_activation(0.5, 10.0, 48.0, 10)
    assert boosted > base


def test_activation_zero_tau_returns_zero():
    from core.observability import compute_activation
    result = compute_activation(0.9, 100.0, 0.0, 5)
    assert result == 0.0


def test_activation_log_boost_is_sublinear():
    """Access boost grows sub-linearly (log curve)."""
    from core.observability import compute_activation
    a1 = compute_activation(1.0, 0.0, 48.0, 1)
    a10 = compute_activation(1.0, 0.0, 48.0, 10)
    a100 = compute_activation(1.0, 0.0, 48.0, 100)
    # Each 10x increase in count should add diminishing boost
    assert (a10 - a1) > (a100 - a10) * 0.5  # rough sub-linearity check


# ---------------------------------------------------------------------------
# log_retrieval
# ---------------------------------------------------------------------------


def test_log_retrieval_appends_valid_jsonl(isolate_obs_dir):
    from core.observability import log_retrieval
    rid = log_retrieval(
        query="EventBus pattern",
        candidate_ids=["a", "b", "c"],
        returned_ids=["a", "b"],
        scores={"a": 0.9, "b": 0.7},
        context={"session_id": "sess-1", "project": "sector"},
    )
    path = isolate_obs_dir / "retrieval-trace.jsonl"
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["query"] == "EventBus pattern"
    assert record["candidate_count"] == 3
    assert record["returned_ids"] == ["a", "b"]
    assert record["retrieval_id"] == rid
    assert "ts" in record


def test_log_retrieval_tolerates_empty_context(isolate_obs_dir):
    from core.observability import log_retrieval
    # Should not raise even with empty context
    log_retrieval("query", [], [], {}, {})
    path = isolate_obs_dir / "retrieval-trace.jsonl"
    assert path.exists()
    record = json.loads(path.read_text().strip())
    assert record["session_id"] is None


def test_log_retrieval_accumulates_multiple_records(isolate_obs_dir):
    from core.observability import log_retrieval
    for i in range(3):
        log_retrieval(f"query {i}", [], [], {}, {})
    lines = (isolate_obs_dir / "retrieval-trace.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# log_acceptance
# ---------------------------------------------------------------------------


def test_log_acceptance_appends_valid_jsonl(isolate_obs_dir):
    from core.observability import log_acceptance
    log_acceptance("rid-123", ["m1", "m2"], ["m3"])
    path = isolate_obs_dir / "acceptance-trace.jsonl"
    assert path.exists()
    record = json.loads(path.read_text().strip())
    assert record["retrieval_id"] == "rid-123"
    assert record["accepted_count"] == 2
    assert record["rejected_count"] == 1


def test_log_acceptance_tolerates_empty_lists(isolate_obs_dir):
    from core.observability import log_acceptance
    log_acceptance("rid-456", [], [])
    record = json.loads((isolate_obs_dir / "acceptance-trace.jsonl").read_text().strip())
    assert record["accepted_count"] == 0
    assert record["rejected_count"] == 0


# ---------------------------------------------------------------------------
# log_consolidation_decision
# ---------------------------------------------------------------------------


def test_log_consolidation_decision_appends_valid_jsonl(isolate_obs_dir):
    from core.observability import log_consolidation_decision
    log_consolidation_decision(
        observation_id="obs-1",
        decision="KEEP",
        importance=0.7,
        kind="finding",
        knowledge_type="conceptual",
        rationale="Load-bearing architecture detail.",
    )
    path = isolate_obs_dir / "consolidation-decisions.jsonl"
    record = json.loads(path.read_text().strip())
    assert record["decision"] == "KEEP"
    assert record["importance"] == 0.7
    assert record["kind"] == "finding"


def test_log_consolidation_decision_tolerates_none_fields(isolate_obs_dir):
    from core.observability import log_consolidation_decision
    log_consolidation_decision("obs-2", "PRUNE", 0.3, None, None, "")
    record = json.loads(
        (isolate_obs_dir / "consolidation-decisions.jsonl").read_text().strip()
    )
    assert record["kind"] is None
    assert record["knowledge_type"] is None


# ---------------------------------------------------------------------------
# log_shadow_prune
# ---------------------------------------------------------------------------


def test_log_shadow_prune_appends_valid_jsonl(isolate_obs_dir):
    from core.observability import log_shadow_prune
    log_shadow_prune(
        memory_id="mem-abc",
        computed_activation=0.03,
        threshold=0.05,
        would_prune=True,
        importance=0.35,
        age_hours=120.0,
        access_count=0,
        tier="T4",
    )
    path = isolate_obs_dir / "shadow-prune.jsonl"
    record = json.loads(path.read_text().strip())
    assert record["memory_id"] == "mem-abc"
    assert record["would_prune"] is True
    assert record["tier"] == "T4"


def test_log_shadow_prune_batch_100_memories_under_one_second(isolate_obs_dir):
    from core.observability import log_shadow_prune, compute_activation
    start = time.monotonic()
    for i in range(100):
        activation = compute_activation(0.5, float(i), 48.0, i % 10)
        log_shadow_prune(
            memory_id=f"mem-{i:04d}",
            computed_activation=activation,
            threshold=0.05,
            would_prune=(activation < 0.05),
            importance=0.5,
            age_hours=float(i),
            access_count=i % 10,
            tier="T3",
        )
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"Batch of 100 took {elapsed:.3f}s — exceeded 1s limit"
    lines = (isolate_obs_dir / "shadow-prune.jsonl").read_text().strip().splitlines()
    assert len(lines) == 100


# ---------------------------------------------------------------------------
# SHADOW_ONLY flag tests (Decision C3)
# ---------------------------------------------------------------------------


def _make_memory_for_prune(stage: str = "ephemeral") -> "Memory":
    """Create a minimal Memory row and return it."""
    from core.models import Memory
    return Memory.create(
        stage=stage,
        title="prune candidate",
        summary="test",
        importance=0.1,
    )


class TestShadowOnlyFlag:
    """Decision C3: SHADOW_ONLY=True logs only; SHADOW_ONLY=False also soft-archives."""

    def test_shadow_only_true_does_not_update_archived_at(self, base, isolate_obs_dir):
        """With SHADOW_ONLY=True, log_shadow_prune(would_prune=True) writes JSONL
        but leaves archived_at NULL on the memory row."""
        import core.observability as obs_mod
        original = obs_mod.SHADOW_ONLY
        try:
            obs_mod.SHADOW_ONLY = True
            mem = _make_memory_for_prune()
            from core.observability import log_shadow_prune
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=200.0,
                access_count=0,
                tier="T4",
            )
            # JSONL must be written
            path = isolate_obs_dir / "shadow-prune.jsonl"
            assert path.exists()
            record = json.loads(path.read_text().strip())
            assert record["memory_id"] == mem.id
            assert record["would_prune"] is True
            assert record["shadow_only"] is True

            # archived_at must remain NULL
            from core.models import Memory
            refreshed = Memory.get_by_id(mem.id)
            assert refreshed.archived_at is None, (
                f"Expected archived_at=None in shadow mode, got {refreshed.archived_at!r}"
            )
        finally:
            obs_mod.SHADOW_ONLY = original

    def test_shadow_only_false_sets_archived_at(self, base, isolate_obs_dir):
        """With SHADOW_ONLY=False, log_shadow_prune(would_prune=True) writes JSONL
        and also sets archived_at on the memory row."""
        import core.observability as obs_mod
        original = obs_mod.SHADOW_ONLY
        try:
            obs_mod.SHADOW_ONLY = False
            mem = _make_memory_for_prune()
            from core.observability import log_shadow_prune
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=200.0,
                access_count=0,
                tier="T4",
            )
            # JSONL written with shadow_only=False
            path = isolate_obs_dir / "shadow-prune.jsonl"
            record = json.loads(path.read_text().strip())
            assert record["shadow_only"] is False

            # archived_at must be set
            from core.models import Memory
            refreshed = Memory.get_by_id(mem.id)
            assert refreshed.archived_at is not None, (
                "Expected archived_at to be set when SHADOW_ONLY=False"
            )
        finally:
            obs_mod.SHADOW_ONLY = original

    def test_shadow_only_false_no_prune_does_not_archive(self, base, isolate_obs_dir):
        """With SHADOW_ONLY=False, would_prune=False must NOT touch archived_at."""
        import core.observability as obs_mod
        original = obs_mod.SHADOW_ONLY
        try:
            obs_mod.SHADOW_ONLY = False
            mem = _make_memory_for_prune()
            from core.observability import log_shadow_prune
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.9,
                threshold=0.05,
                would_prune=False,
                importance=0.9,
                age_hours=1.0,
                access_count=5,
                tier="T3",
            )
            from core.models import Memory
            refreshed = Memory.get_by_id(mem.id)
            assert refreshed.archived_at is None, (
                "archived_at must remain NULL when would_prune=False"
            )
        finally:
            obs_mod.SHADOW_ONLY = original

    def test_shadow_only_false_idempotent_on_already_archived(self, base, isolate_obs_dir):
        """With SHADOW_ONLY=False, calling log_shadow_prune twice on an already-archived
        memory must not overwrite the existing archived_at value."""
        import core.observability as obs_mod
        original = obs_mod.SHADOW_ONLY
        try:
            obs_mod.SHADOW_ONLY = False
            mem = _make_memory_for_prune()
            from core.observability import log_shadow_prune

            # First call — sets archived_at
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=200.0,
                access_count=0,
                tier="T4",
            )
            from core.models import Memory
            first_archived_at = Memory.get_by_id(mem.id).archived_at
            assert first_archived_at is not None

            # Second call — WHERE archived_at IS NULL means no update
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=201.0,
                access_count=0,
                tier="T4",
            )
            second_archived_at = Memory.get_by_id(mem.id).archived_at
            assert second_archived_at == first_archived_at, (
                "archived_at must not be overwritten on a second call"
            )
        finally:
            obs_mod.SHADOW_ONLY = original

    def test_flipping_shadow_only_mid_test(self, base, isolate_obs_dir):
        """Flipping SHADOW_ONLY from True → False mid-test produces the correct switch:
        first call (shadow) leaves archived_at NULL; second call (live) sets it."""
        import core.observability as obs_mod
        original = obs_mod.SHADOW_ONLY
        try:
            mem = _make_memory_for_prune()
            from core.observability import log_shadow_prune
            from core.models import Memory

            # Pass 1: shadow mode — no DB mutation
            obs_mod.SHADOW_ONLY = True
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=100.0,
                access_count=0,
                tier="T4",
            )
            assert Memory.get_by_id(mem.id).archived_at is None

            # Pass 2: live mode — soft-archive fires
            obs_mod.SHADOW_ONLY = False
            log_shadow_prune(
                memory_id=mem.id,
                computed_activation=0.01,
                threshold=0.05,
                would_prune=True,
                importance=0.1,
                age_hours=101.0,
                access_count=0,
                tier="T4",
            )
            assert Memory.get_by_id(mem.id).archived_at is not None

            # JSONL should have two records (one per call)
            lines = (isolate_obs_dir / "shadow-prune.jsonl").read_text().strip().splitlines()
            assert len(lines) == 2
            records = [json.loads(l) for l in lines]
            assert records[0]["shadow_only"] is True
            assert records[1]["shadow_only"] is False
        finally:
            obs_mod.SHADOW_ONLY = original
