"""
Tests for core/question_lifecycle.py (Sprint B WS-H, DS-F9).
"""

import os
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import Memory
from core.question_lifecycle import (
    QUESTION_RESOLUTION_THRESHOLD,
    detect_resolution,
    get_unresolved_questions,
    mark_resolved,
    pin_open_question,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _make_embedding(dims: int = 8, value: float = 0.5) -> list[float]:
    return [value] * dims


def _pack(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _create_memory(**kwargs) -> Memory:
    defaults = dict(
        stage="consolidated",
        title="Test",
        summary="Summary",
        content="Content",
        importance=0.5,
    )
    defaults.update(kwargs)
    m = Memory(**defaults)
    m.save(force_insert=True)
    return m


def _attach_embedding(memory: Memory, embedding: list[float]) -> None:
    """Attach a synthetic embedding to a Memory for cosine-similarity tests."""
    memory.embedding = embedding


# ---------------------------------------------------------------------------
# get_unresolved_questions
# ---------------------------------------------------------------------------


class TestGetUnresolvedQuestions:
    def test_returns_only_open_question_kind(self, store):
        _create_memory(kind="open_question", title="Q1", importance=0.7)
        _create_memory(kind="finding", title="F1", importance=0.9)
        results = get_unresolved_questions()
        assert all(m.kind == "open_question" for m in results)

    def test_excludes_resolved_questions(self, store):
        q = _create_memory(kind="open_question", title="Resolved Q", importance=0.6)
        Memory.update(resolved_at=datetime.now(timezone.utc)).where(Memory.id == q.id).execute()
        results = get_unresolved_questions()
        ids = [m.id for m in results]
        assert q.id not in ids

    def test_includes_unresolved_questions(self, store):
        q = _create_memory(kind="open_question", title="Unresolved Q", importance=0.6)
        results = get_unresolved_questions()
        ids = [m.id for m in results]
        assert q.id in ids

    def test_orders_by_importance_desc(self, store):
        _create_memory(kind="open_question", title="Low", importance=0.2)
        _create_memory(kind="open_question", title="High", importance=0.9)
        _create_memory(kind="open_question", title="Mid", importance=0.5)
        results = get_unresolved_questions()
        importances = [m.importance for m in results]
        assert importances == sorted(importances, reverse=True)

    def test_respects_limit(self, store):
        for i in range(5):
            _create_memory(kind="open_question", title=f"Q{i}", importance=float(i) / 10)
        results = get_unresolved_questions(limit=3)
        assert len(results) <= 3

    def test_excludes_archived(self, store):
        q = _create_memory(kind="open_question", title="Archived Q", importance=0.5)
        Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == q.id).execute()
        results = get_unresolved_questions()
        ids = [m.id for m in results]
        assert q.id not in ids

    def test_empty_when_no_questions(self, store):
        _create_memory(kind="finding", title="Not a question")
        results = get_unresolved_questions()
        assert results == []


# ---------------------------------------------------------------------------
# detect_resolution
# ---------------------------------------------------------------------------


class TestDetectResolution:
    def test_returns_none_for_non_resolving_kind(self, store):
        new_mem = _create_memory(kind="preference", title="Pref")
        q = _create_memory(kind="open_question", title="Q")
        assert detect_resolution(new_mem, [q]) is None

    def test_returns_none_for_decision_kind(self, store):
        new_mem = _create_memory(kind="decision", title="Dec")
        q = _create_memory(kind="open_question", title="Q")
        assert detect_resolution(new_mem, [q]) is None

    def test_returns_none_when_no_embedding(self, store):
        new_mem = _create_memory(kind="correction", title="Corr")
        q = _create_memory(kind="open_question", title="Q")
        # No embeddings attached — should return None gracefully
        assert detect_resolution(new_mem, [q]) is None

    def test_returns_none_when_similarity_below_threshold(self, store):
        new_mem = _create_memory(kind="correction", title="Corr")
        q = _create_memory(kind="open_question", title="Q")
        # Orthogonal vectors → similarity 0
        _attach_embedding(new_mem, [1.0, 0.0, 0.0, 0.0])
        _attach_embedding(q, [0.0, 1.0, 0.0, 0.0])
        assert detect_resolution(new_mem, [q]) is None

    def test_returns_question_id_when_similarity_passes(self, store):
        new_mem = _create_memory(kind="correction", title="Corr")
        q = _create_memory(kind="open_question", title="Q")
        # Identical vectors → similarity 1.0
        _attach_embedding(new_mem, [0.5, 0.5, 0.5, 0.5])
        _attach_embedding(q, [0.5, 0.5, 0.5, 0.5])
        result = detect_resolution(new_mem, [q])
        assert result == str(q.id)

    def test_returns_question_id_for_finding_kind(self, store):
        new_mem = _create_memory(kind="finding", title="Find")
        q = _create_memory(kind="open_question", title="Q")
        _attach_embedding(new_mem, [1.0, 1.0, 0.0, 0.0])
        _attach_embedding(q, [1.0, 1.0, 0.0, 0.0])
        result = detect_resolution(new_mem, [q])
        assert result == str(q.id)

    def test_knowledge_type_mismatch_blocks_resolution(self, store):
        new_mem = _create_memory(kind="correction", title="Corr", knowledge_type="factual")
        q = _create_memory(kind="open_question", title="Q", knowledge_type="procedural")
        _attach_embedding(new_mem, [0.5, 0.5, 0.5, 0.5])
        _attach_embedding(q, [0.5, 0.5, 0.5, 0.5])
        result = detect_resolution(new_mem, [q])
        assert result is None

    def test_knowledge_type_null_on_question_accepts_any(self, store):
        new_mem = _create_memory(kind="correction", title="Corr", knowledge_type="factual")
        q = _create_memory(kind="open_question", title="Q", knowledge_type=None)
        _attach_embedding(new_mem, [0.5, 0.5, 0.5, 0.5])
        _attach_embedding(q, [0.5, 0.5, 0.5, 0.5])
        result = detect_resolution(new_mem, [q])
        assert result == str(q.id)

    def test_skips_already_resolved_question(self, store):
        new_mem = _create_memory(kind="correction", title="Corr")
        q = _create_memory(kind="open_question", title="Q")
        Memory.update(resolved_at=datetime.now(timezone.utc)).where(Memory.id == q.id).execute()
        q_fresh = Memory.get_by_id(q.id)
        _attach_embedding(new_mem, [0.5, 0.5, 0.5, 0.5])
        _attach_embedding(q_fresh, [0.5, 0.5, 0.5, 0.5])
        result = detect_resolution(new_mem, [q_fresh])
        assert result is None

    def test_returns_best_matching_question(self, store):
        new_mem = _create_memory(kind="finding", title="Find")
        q_low = _create_memory(kind="open_question", title="QLow")
        q_high = _create_memory(kind="open_question", title="QHigh")
        # new_mem is closer to q_high
        _attach_embedding(new_mem, [1.0, 0.0])
        _attach_embedding(q_low, [0.5, 0.5])
        _attach_embedding(q_high, [1.0, 0.0])
        result = detect_resolution(new_mem, [q_low, q_high])
        assert result == str(q_high.id)


# ---------------------------------------------------------------------------
# mark_resolved
# ---------------------------------------------------------------------------


class TestMarkResolved:
    def test_sets_resolved_at_on_question(self, store):
        q = _create_memory(kind="open_question", title="Q")
        resolving = _create_memory(kind="correction", title="Corr")
        mark_resolved(q, resolving)
        q_fresh = Memory.get_by_id(q.id)
        assert q_fresh.resolved_at is not None

    def test_sets_resolves_question_id_on_resolving(self, store):
        q = _create_memory(kind="open_question", title="Q")
        resolving = _create_memory(kind="correction", title="Corr")
        mark_resolved(q, resolving)
        r_fresh = Memory.get_by_id(resolving.id)
        assert r_fresh.resolves_question_id == str(q.id)

    def test_atomic_both_rows_updated(self, store):
        q = _create_memory(kind="open_question", title="Q")
        resolving = _create_memory(kind="finding", title="Find")
        mark_resolved(q, resolving)
        q_fresh = Memory.get_by_id(q.id)
        r_fresh = Memory.get_by_id(resolving.id)
        assert q_fresh.resolved_at is not None
        assert r_fresh.resolves_question_id == str(q.id)

    def test_updates_in_memory_state(self, store):
        q = _create_memory(kind="open_question", title="Q")
        resolving = _create_memory(kind="correction", title="Corr")
        mark_resolved(q, resolving)
        assert q.resolved_at is not None
        assert resolving.resolves_question_id == str(q.id)


# ---------------------------------------------------------------------------
# pin_open_question
# ---------------------------------------------------------------------------


class TestPinOpenQuestion:
    def test_sets_is_pinned_true(self, store):
        q = _create_memory(kind="open_question", title="Q")
        pin_open_question(q)
        q_fresh = Memory.get_by_id(q.id)
        assert q_fresh.is_pinned == 1

    def test_raises_for_non_open_question_kind(self, store):
        m = _create_memory(kind="finding", title="Find")
        with pytest.raises(ValueError, match="open_question"):
            pin_open_question(m)

    def test_raises_for_correction_kind(self, store):
        m = _create_memory(kind="correction", title="Corr")
        with pytest.raises(ValueError):
            pin_open_question(m)

    def test_raises_for_none_kind(self, store):
        m = _create_memory(kind=None, title="No kind")
        with pytest.raises(ValueError):
            pin_open_question(m)

    def test_updates_in_memory_state(self, store):
        q = _create_memory(kind="open_question", title="Q")
        pin_open_question(q)
        assert q.is_pinned == 1

    def test_idempotent_double_pin(self, store):
        q = _create_memory(kind="open_question", title="Q")
        pin_open_question(q)
        pin_open_question(q)
        q_fresh = Memory.get_by_id(q.id)
        assert q_fresh.is_pinned == 1


# ---------------------------------------------------------------------------
# QUESTION_RESOLUTION_THRESHOLD env var
# ---------------------------------------------------------------------------


class TestThresholdEnvVar:
    def test_default_threshold(self):
        # Default is 0.85 when env var is unset
        original = os.environ.pop("MEMESIS_QUESTION_THRESHOLD", None)
        try:
            import importlib
            import core.question_lifecycle as ql
            importlib.reload(ql)
            assert ql.QUESTION_RESOLUTION_THRESHOLD == pytest.approx(0.85)
        finally:
            if original is not None:
                os.environ["MEMESIS_QUESTION_THRESHOLD"] = original
            import importlib
            import core.question_lifecycle as ql
            importlib.reload(ql)

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_QUESTION_THRESHOLD", "0.75")
        import importlib
        import core.question_lifecycle as ql
        importlib.reload(ql)
        assert ql.QUESTION_RESOLUTION_THRESHOLD == pytest.approx(0.75)
        # Reload to reset for subsequent tests
        monkeypatch.delenv("MEMESIS_QUESTION_THRESHOLD", raising=False)
        importlib.reload(ql)
