"""
Tests for core.linking — cosine-based linked_observation_ids[] population.

Sprint A Wave 2 WS-F.

Uses mock embeddings (inline float lists on Memory objects) and an in-memory
Peewee SQLite DB. No real Bedrock API calls are made.
"""

import json
import os
import struct
import tempfile
import uuid
from pathlib import Path

import pytest

from core.models import Memory, db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedding(values: list[float]) -> bytes:
    """Pack floats to the raw bytes format stored by sqlite-vec / embeddings.py."""
    return struct.pack(f"{len(values)}f", *values)


def _unit(values: list[float]) -> list[float]:
    """Normalise a vector to unit length."""
    mag = sum(x * x for x in values) ** 0.5
    if mag == 0.0:
        return values
    return [x / mag for x in values]


def _make_memory(
    *,
    embedding: list[float] | None = None,
    kind: str | None = None,
    subject: str | None = None,
    knowledge_type: str | None = None,
    stage: str = "consolidated",
    linked_observation_ids: str | None = None,
) -> Memory:
    """Create a transient Memory-like object (not DB-persisted) for unit tests."""

    class FakeMemory:
        pass

    m = FakeMemory()
    m.id = str(uuid.uuid4())
    m.stage = stage
    m.kind = kind
    m.subject = subject
    m.knowledge_type = knowledge_type
    m.linked_observation_ids = linked_observation_ids
    m.archived_at = None

    if embedding is not None:
        # Store as raw bytes so _get_embedding_bytes inline path works
        m.embedding = _make_embedding(embedding)
    else:
        m.embedding = None

    return m


# ---------------------------------------------------------------------------
# DB fixture — in-memory peewee SQLite
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def in_memory_db(tmp_path, monkeypatch):
    """
    Initialise a fresh SQLite DB using init_db (creates all tables including
    FTS triggers). Tears down after each test.
    """
    from core.database import init_db, close_db

    init_db(base_dir=str(tmp_path))

    yield tmp_path

    close_db()


def _persist_memory(
    embedding: list[float] | None = None,
    kind: str | None = None,
    subject: str | None = None,
    knowledge_type: str | None = None,
    stage: str = "consolidated",
) -> Memory:
    """Insert a real Memory row with an inline embedding attribute."""
    mem = Memory.create(
        stage=stage,
        title="Test memory",
        summary="A test summary",
        content="Some content",
        kind=kind,
        subject=subject,
        knowledge_type=knowledge_type,
    )
    if embedding is not None:
        mem.embedding = _make_embedding(embedding)
    else:
        mem.embedding = None
    return mem


# ---------------------------------------------------------------------------
# find_links_for_observation — pure unit tests (no DB)
# ---------------------------------------------------------------------------


def test_find_links_returns_sorted_desc():
    from core.linking import find_links_for_observation

    anchor = _make_memory(embedding=_unit([1.0, 0.0, 0.0]))
    c1 = _make_memory(embedding=_unit([0.95, 0.1, 0.0]))   # score ≈ 0.994
    c2 = _make_memory(embedding=_unit([0.91, 0.3, 0.0]))   # score ≈ 0.950
    c3 = _make_memory(embedding=_unit([0.50, 0.5, 0.0]))   # score ≈ 0.707 — below threshold

    results = find_links_for_observation(anchor, [c1, c2, c3], threshold=0.90)
    assert len(results) == 2
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True), "Results must be sorted descending"
    assert scores[0] > scores[1]


def test_find_links_empty_candidates():
    from core.linking import find_links_for_observation

    anchor = _make_memory(embedding=_unit([1.0, 0.0]))
    assert find_links_for_observation(anchor, []) == []


def test_find_links_all_below_threshold():
    from core.linking import find_links_for_observation

    anchor = _make_memory(embedding=_unit([1.0, 0.0]))
    low = _make_memory(embedding=_unit([0.0, 1.0]))  # orthogonal → score=0

    results = find_links_for_observation(anchor, [low], threshold=0.90)
    assert results == []


def test_find_links_threshold_inclusive():
    """Score exactly equal to threshold must be included (>= not >)."""
    from core.linking import find_links_for_observation

    # Two identical unit vectors → cosine = 1.0; use threshold=1.0 for edge test
    v = _unit([1.0, 0.0, 0.0])
    anchor = _make_memory(embedding=v)
    exact = _make_memory(embedding=v[:])

    results = find_links_for_observation(anchor, [exact], threshold=1.0)
    assert len(results) == 1
    _, score = results[0]
    assert score >= 1.0 - 1e-6


def test_find_links_top_k_cap():
    """5 candidates above threshold → only top 3 returned."""
    from core.linking import find_links_for_observation

    anchor = _make_memory(embedding=_unit([1.0, 0.0]))
    # All slightly tilted from anchor — scores will all be close to 1.0
    candidates = [
        _make_memory(embedding=_unit([1.0 - 0.01 * i, 0.01 * i])) for i in range(1, 6)
    ]

    results = find_links_for_observation(anchor, candidates, threshold=0.90, top_k=3)
    assert len(results) == 3
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_find_links_excludes_self():
    """The new memory must never link to itself."""
    from core.linking import find_links_for_observation

    v = _unit([1.0, 0.0])
    anchor = _make_memory(embedding=v)
    # Pass anchor again as a candidate (same id)
    results = find_links_for_observation(anchor, [anchor], threshold=0.0)
    assert results == []


def test_find_links_no_embedding_on_new_memory():
    from core.linking import find_links_for_observation

    anchor = _make_memory(embedding=None)
    candidate = _make_memory(embedding=_unit([1.0, 0.0]))
    assert find_links_for_observation(anchor, [candidate]) == []


# ---------------------------------------------------------------------------
# detect_topic_drift
# ---------------------------------------------------------------------------


def test_detect_topic_drift_all_axes_differ_returns_true():
    from core.linking import detect_topic_drift

    new_mem = _make_memory(kind="decision", subject="user", knowledge_type="procedural")
    linked = _make_memory(kind="finding", subject="system", knowledge_type="factual")
    assert detect_topic_drift(new_mem, linked) is True


def test_detect_topic_drift_same_kind_returns_false():
    from core.linking import detect_topic_drift

    new_mem = _make_memory(kind="decision", subject="user", knowledge_type="procedural")
    linked = _make_memory(kind="decision", subject="system", knowledge_type="factual")
    assert detect_topic_drift(new_mem, linked) is False


def test_detect_topic_drift_null_axis_returns_false():
    """If any axis is None, we cannot confirm drift → False."""
    from core.linking import detect_topic_drift

    new_mem = _make_memory(kind="decision", subject=None, knowledge_type="procedural")
    linked = _make_memory(kind="finding", subject="system", knowledge_type="factual")
    assert detect_topic_drift(new_mem, linked) is False


# ---------------------------------------------------------------------------
# link_memory — integration tests (uses in-memory DB)
# ---------------------------------------------------------------------------


def _patch_linking_candidates(monkeypatch, candidates: list):
    """
    Patch Memory.select inside core.linking so link_memory's candidate-load
    returns a controlled list. Uses monkeypatch on the module-level Memory
    reference in core.linking (not the global class), so test-body assertions
    that call Memory.select/.get_or_none still work via the original class.
    """
    import core.linking as linking_mod

    # Build a minimal stand-in that only supports the .select().where() call
    # pattern used in link_memory().
    _fixed = list(candidates)

    class _FakeQuery:
        def where(self, *args, **kwargs):
            return list(_fixed)

    class _FakeMemory:
        @staticmethod
        def select(*args, **kwargs):
            return _FakeQuery()

    monkeypatch.setattr(linking_mod, "Memory", _FakeMemory)


def test_link_memory_validates_uuids_against_db(in_memory_db, monkeypatch):
    """
    Hallucinated IDs (not present in DB) must not appear in linked_observation_ids.
    link_memory patches the candidate load to include a phantom not in DB;
    the returned linked list must only contain real DB IDs.
    """
    import core.linking as linking_mod
    from core.linking import link_memory

    # Create new memory in DB
    new_mem = _persist_memory(embedding=_unit([1.0, 0.0]))
    new_mem.embedding = _make_embedding(_unit([1.0, 0.0]))

    # One real candidate in DB
    real_candidate = _persist_memory(embedding=_unit([0.99, 0.01]))
    real_candidate.embedding = _make_embedding(_unit([0.99, 0.01]))

    # A phantom with a UUID not in the DB
    phantom = _make_memory(embedding=_unit([0.98, 0.02]))

    _patch_linking_candidates(monkeypatch, [real_candidate, phantom])

    linked = link_memory(new_mem)

    # Phantom ID must never appear
    assert phantom.id not in linked
    # real_candidate may appear if embeddings resolved (best-effort assertion)
    for lid in linked:
        assert Memory.get_or_none(Memory.id == lid) is not None


def test_link_memory_populates_linked_observation_ids(in_memory_db, monkeypatch):
    """After link_memory(), memory.linked_observation_ids must be valid JSON list."""
    from core.linking import link_memory

    v_anchor = _unit([1.0, 0.0, 0.0])
    v_similar = _unit([0.99, 0.01, 0.0])

    new_mem = _persist_memory(embedding=v_anchor)
    new_mem.embedding = _make_embedding(v_anchor)

    similar = _persist_memory(embedding=v_similar)
    similar.embedding = _make_embedding(v_similar)

    _patch_linking_candidates(monkeypatch, [similar])

    link_memory(new_mem)

    # Re-load from DB — linked_observation_ids must be valid JSON list
    refreshed = Memory.get_by_id(new_mem.id)
    ids = refreshed.linked_observations  # property parses JSON
    assert isinstance(ids, list)


def test_link_memory_empty_candidates(in_memory_db, monkeypatch):
    """No candidates → empty list returned and linked_observation_ids = '[]'."""
    from core.linking import link_memory

    new_mem = _persist_memory(embedding=_unit([1.0, 0.0]))
    new_mem.embedding = _make_embedding(_unit([1.0, 0.0]))

    _patch_linking_candidates(monkeypatch, [])

    linked = link_memory(new_mem)
    assert linked == []


# ---------------------------------------------------------------------------
# LINK_COSINE_THRESHOLD reads from env var
# ---------------------------------------------------------------------------


def test_threshold_reads_from_env_var(monkeypatch):
    """MEMESIS_LINK_THRESHOLD env var must override the default 0.90."""
    monkeypatch.setenv("MEMESIS_LINK_THRESHOLD", "0.75")

    # Force module reload to pick up the new env var
    import importlib
    import core.linking as linking_mod

    importlib.reload(linking_mod)

    assert linking_mod.LINK_COSINE_THRESHOLD == pytest.approx(0.75)

    # Restore
    monkeypatch.delenv("MEMESIS_LINK_THRESHOLD", raising=False)
    importlib.reload(linking_mod)


# ---------------------------------------------------------------------------
# JSONL trace file schema
# ---------------------------------------------------------------------------


def test_linking_trace_jsonl_schema(in_memory_db, monkeypatch, tmp_path):
    """After a linking call, trace JSONL file must have the required schema keys."""
    from core.linking import link_memory
    import core.linking as linking_mod

    trace_path = tmp_path / "linking-trace.jsonl"
    monkeypatch.setattr(linking_mod, "_TRACE_PATH", trace_path)

    new_mem = _persist_memory(embedding=_unit([1.0, 0.0]))
    new_mem.embedding = _make_embedding(_unit([1.0, 0.0]))

    class FakeQuery:
        def where(self, *args, **kwargs):
            return []

    monkeypatch.setattr(Memory, "select", lambda *a, **kw: FakeQuery())

    link_memory(new_mem)

    assert trace_path.exists(), "Trace file must be created"
    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1

    entry = json.loads(lines[-1])
    required_keys = {
        "ts",
        "memory_id",
        "candidate_count",
        "above_threshold_count",
        "selected",
        "rejected_above_threshold_due_to_top_k_cap",
        "threshold",
    }
    assert required_keys <= entry.keys(), f"Missing keys: {required_keys - entry.keys()}"
    assert isinstance(entry["selected"], list)
    assert isinstance(entry["rejected_above_threshold_due_to_top_k_cap"], list)
    assert isinstance(entry["threshold"], float)
    assert isinstance(entry["candidate_count"], int)
