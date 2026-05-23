# pyright: reportUnusedFunction=false
"""Tests for the two-phase memory_kind assignment in Consolidator._execute_keep.

Three cases per spec:
  (a) derive_memory_kind returns a kind → classify_memory_kind is NOT called.
  (b) derive returns None on a non-open_question obs → classify_memory_kind IS called, result stored.
  (c) obs_kind == "open_question" → classify_memory_kind NOT called, memory_kind stays None.

Pytest fixtures and autouse decorators are not modeled by static analyzers — the
reportUnusedFunction suppression covers fixture defs.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.models import Memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_dedup_llm(monkeypatch):  # noqa: PT004  -- autouse fixture, name is sentinel
    """Prevent linking LLM calls from reaching real transport."""
    monkeypatch.setattr(
        "core.linking._llm_confirms_duplicate",
        lambda *_a, **_k: False,
        raising=False,
    )


@pytest.fixture
def _db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def consolidator(_db):
    lm = LifecycleManager()
    return Consolidator(lifecycle=lm, model="claude-sonnet-4-6")


@pytest.fixture
def ephemeral_file(tmp_path):
    p = tmp_path / "session.md"
    p.write_text(
        "- User prefers snake_case for Python variable names.\n",
        encoding="utf-8",
    )
    return str(p)


def _keep_decision(**overrides) -> dict:
    record = {
        "action": "keep",
        "observation": "Uses snake_case for Python variables.",
        "title": "snake_case preference",
        "summary": "User prefers snake_case.",
        "tags": [],
        "target_path": "style/snake_case.md",
        "reinforces": None,
        "contradicts": None,
        "kind": "preference",
        "evidence_count": 0,
    }
    record.update(overrides)
    return record


def _llm_response(decisions):
    return json.dumps({"decisions": decisions})


# ---------------------------------------------------------------------------
# (a) derive returns a kind → LLM fallback NOT called
# ---------------------------------------------------------------------------

def test_derive_returns_kind_no_llm_called(consolidator, ephemeral_file):
    """kind='preference' deterministically maps to 'opinion' — classify must not fire."""
    decision = _keep_decision(kind="preference")

    with patch("core.consolidator._call_llm_batch") as mock_batch, \
         patch("core.consolidator.classify_memory_kind") as mock_classify:
        mock_batch.return_value = [_llm_response([decision])]
        consolidator.consolidate_session(ephemeral_file, "sess-kind-a")

    mock_classify.assert_not_called()

    mem = Memory.select().where(Memory.title == "snake_case preference").get()
    assert mem.memory_kind == "opinion"


# ---------------------------------------------------------------------------
# (b) derive returns None, obs_kind != "open_question" → LLM called, result stored
# ---------------------------------------------------------------------------

def test_derive_returns_none_llm_fallback_called_and_stored(consolidator, ephemeral_file):
    """kind=None passes schema validation and derive_memory_kind(None) returns None,
    so the LLM fallback path fires for a non-open_question observation."""
    decision = _keep_decision(
        kind=None,  # None passes schema; derive_memory_kind(None) → None
        title="North-star goal",
        summary="Ship by Q3.",
        observation="Ship the feature by Q3.",
        target_path="goals/q3.md",
    )

    with patch("core.consolidator._call_llm_batch") as mock_batch, \
         patch("core.consolidator.classify_memory_kind", return_value="goal") as mock_classify:
        mock_batch.return_value = [_llm_response([decision])]
        consolidator.consolidate_session(ephemeral_file, "sess-kind-b")

    mock_classify.assert_called_once()
    call_args = mock_classify.call_args
    assert call_args[0][0] == "North-star goal"   # title
    # content arg is the body_content (observation text)
    assert "Q3" in call_args[0][1]

    mem = Memory.select().where(Memory.title == "North-star goal").get()
    assert mem.memory_kind == "goal"


# ---------------------------------------------------------------------------
# (c) obs_kind == "open_question" → LLM NOT called, memory_kind stays None
# ---------------------------------------------------------------------------

def test_open_question_never_classified(consolidator, ephemeral_file):
    """open_question is a lifecycle state, not a knowledge kind; must stay NULL."""
    decision = _keep_decision(
        kind="open_question",
        title="Open: should we migrate to Postgres?",
        summary="Pending decision.",
        observation="Should we migrate to Postgres?",
        target_path="questions/postgres.md",
    )

    with patch("core.consolidator._call_llm_batch") as mock_batch, \
         patch("core.consolidator.classify_memory_kind") as mock_classify:
        mock_batch.return_value = [_llm_response([decision])]
        consolidator.consolidate_session(ephemeral_file, "sess-kind-c")

    mock_classify.assert_not_called()

    mem = Memory.select().where(Memory.title == "Open: should we migrate to Postgres?").get()
    assert mem.memory_kind is None
