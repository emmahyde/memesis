"""Tests for memory_kind LLM classification and the promotion gate.

Covers:
  - classify_memory_kind: LLM fallback for kinds the deterministic map misses.
  - can_promote: stage-gated invariant blocking unclassified consolidated rows.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.models import Memory
from core.validators import classify_memory_kind


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _create_memory(**kwargs):
    now = datetime.now().isoformat()
    defaults = dict(
        stage="consolidated",
        title="Test Memory",
        summary="Summary",
        content="Test content",
        tags=json.dumps([]),
        importance=0.5,
        reinforcement_count=0,
        usage_count=0,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return Memory.create(**defaults)


# --- classify_memory_kind --------------------------------------------------

def test_classify_memory_kind_returns_valid_label():
    with mock.patch("core.llm.call_llm", return_value="gotcha\n"):
        assert classify_memory_kind("Title", "content") == "gotcha"


def test_classify_memory_kind_strips_punctuation_and_case():
    with mock.patch("core.llm.call_llm", return_value="  Decision. "):
        assert classify_memory_kind("Title", "content") == "decision"


def test_classify_memory_kind_rejects_label_outside_set():
    with mock.patch("core.llm.call_llm", return_value="banana"):
        assert classify_memory_kind("Title", "content") is None


def test_classify_memory_kind_returns_none_on_llm_error():
    with mock.patch("core.llm.call_llm", side_effect=RuntimeError("boom")):
        assert classify_memory_kind("Title", "content") is None


# --- can_promote gate ------------------------------------------------------

def test_can_promote_blocks_unclassified_consolidated(base):
    mem = _create_memory(stage="consolidated", memory_kind=None, kind=None)
    ok, reason = LifecycleManager().can_promote(mem.id)
    assert ok is False
    assert "Unclassified" in reason


def test_can_promote_exempts_open_question(base):
    mem = _create_memory(stage="consolidated", memory_kind=None, kind="open_question")
    _, reason = LifecycleManager().can_promote(mem.id)
    # open_question is exempt — it may still fail other crystallization rules,
    # but never on the memory_kind gate.
    assert "Unclassified" not in reason


def test_can_promote_classified_passes_kind_gate(base):
    mem = _create_memory(stage="consolidated", memory_kind="fact", kind="finding")
    _, reason = LifecycleManager().can_promote(mem.id)
    assert "Unclassified" not in reason


# --- migration 0018: memory_kind CHECK triggers ----------------------------

def test_invalid_memory_kind_rejected_by_trigger(base):
    from peewee import DatabaseError

    with pytest.raises(DatabaseError):
        _create_memory(memory_kind="banana")


def test_valid_memory_kind_accepted_by_trigger(base):
    mem = _create_memory(memory_kind="fact")
    assert mem.memory_kind == "fact"


def test_null_memory_kind_accepted_by_trigger(base):
    mem = _create_memory(memory_kind=None)
    assert mem.memory_kind is None
