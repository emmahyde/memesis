"""Tests for core/decomposer.py — bundled-row decomposition sweep."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.decomposer import _decompose_one, run_decomposer_sweep
from core.models import ConsolidationLog, Memory


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _long(text: str) -> str:
    """Pad a body past MIN_DECOMPOSE_LENGTH so the sweep prefilter selects it."""
    return text + " " + ("filler. " * 60)


def _mem(content, stage="consolidated", checked=0) -> Memory:
    return Memory.create(
        stage=stage, title="bundled", summary="s", content=content,
        importance=0.5, decompose_checked=checked,
    )


SPLIT_RESPONSE = json.dumps({
    "verdict": "SPLIT",
    "children": [
        {"title": "API key location", "content": "The API key lives in env var FOO.",
         "memory_kind": "fact"},
        {"title": "User friction on retries", "content": "Emma abandons after two retries.",
         "memory_kind": "lesson"},
    ],
    "rationale": "Two unrelated atoms.",
})

COHERENT_RESPONSE = json.dumps({
    "verdict": "COHERENT", "children": [], "rationale": "Single subject.",
})


# --- _decompose_one ---------------------------------------------------------


def test_decompose_one_returns_children_on_split(db):
    m = _mem(_long("two topics here"))
    with patch("core.decomposer.call_llm", return_value=SPLIT_RESPONSE):
        children = _decompose_one(m)
    assert children is not None and len(children) == 2
    assert children[0]["memory_kind"] == "fact"


def test_decompose_one_returns_none_on_coherent(db):
    m = _mem(_long("one topic"))
    with patch("core.decomposer.call_llm", return_value=COHERENT_RESPONSE):
        assert _decompose_one(m) is None


def test_single_child_split_is_rejected(db):
    """A 'split' into one fragment is not a real split — guard against over-split."""
    one_child = json.dumps({
        "verdict": "SPLIT",
        "children": [{"title": "x", "content": "y", "memory_kind": "fact"}],
        "rationale": "r",
    })
    m = _mem(_long("text"))
    with patch("core.decomposer.call_llm", return_value=one_child):
        assert _decompose_one(m) is None


def test_invalid_memory_kind_coerced_to_none(db):
    bad_kind = json.dumps({
        "verdict": "SPLIT",
        "children": [
            {"title": "a", "content": "aaa", "memory_kind": "bogus"},
            {"title": "b", "content": "bbb", "memory_kind": "lesson"},
        ],
        "rationale": "r",
    })
    m = _mem(_long("text"))
    with patch("core.decomposer.call_llm", return_value=bad_kind):
        children = _decompose_one(m)
    assert children[0]["memory_kind"] is None
    assert children[1]["memory_kind"] == "lesson"


# --- run_decomposer_sweep ---------------------------------------------------


def test_sweep_splits_bundled_memory(db):
    bundled = _mem(_long("bundled content"))
    with patch("core.decomposer.call_llm", return_value=SPLIT_RESPONSE):
        result = run_decomposer_sweep()

    assert result["split"] == 1
    assert Memory.get_by_id(bundled.id).archived_at is not None
    # Two live children replace it.
    children = list(Memory.select().where(
        Memory.archived_at.is_null(), Memory.stage == "consolidated",
    ))
    assert len(children) == 2
    assert all(c.decompose_checked == 1 for c in children)
    assert ConsolidationLog.select().where(
        ConsolidationLog.memory_id == bundled.id,
        ConsolidationLog.action == "deprecated",
    ).count() == 1


def test_sweep_flags_coherent_memory(db):
    m = _mem(_long("coherent content"))
    with patch("core.decomposer.call_llm", return_value=COHERENT_RESPONSE):
        result = run_decomposer_sweep()

    assert result["coherent"] == 1
    assert result["split"] == 0
    fresh = Memory.get_by_id(m.id)
    assert fresh.archived_at is None
    assert fresh.decompose_checked == 1


def test_sweep_skips_short_memories(db):
    short = _mem("too short to bundle")
    with patch("core.decomposer.call_llm") as mock_llm:
        result = run_decomposer_sweep()
        mock_llm.assert_not_called()
    assert result["checked"] == 0
    assert Memory.get_by_id(short.id).decompose_checked == 0


def test_sweep_skips_already_checked(db):
    _mem(_long("already audited"), checked=1)
    with patch("core.decomposer.call_llm") as mock_llm:
        result = run_decomposer_sweep()
        mock_llm.assert_not_called()
    assert result["checked"] == 0


def test_sweep_llm_error_is_non_fatal(db):
    m = _mem(_long("content"))
    with patch("core.decomposer.call_llm", side_effect=RuntimeError("timeout")):
        result = run_decomposer_sweep()
    assert result["errors"] == 1
    # The memory is left intact and un-flagged so a later run retries it.
    fresh = Memory.get_by_id(m.id)
    assert fresh.archived_at is None
    assert fresh.decompose_checked == 0


def test_sweep_respects_limit(db):
    for _ in range(5):
        _mem(_long("bundled"))
    with patch("core.decomposer.call_llm", return_value=COHERENT_RESPONSE):
        result = run_decomposer_sweep(limit=2)
    assert result["checked"] == 2
