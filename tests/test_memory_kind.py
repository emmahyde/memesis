"""Curated memory-kind taxonomy — core/validators.py:derive_memory_kind + column."""

from __future__ import annotations

import pytest

from core.database import close_db, init_db
from core.models import Memory
from core.validators import MEMORY_KIND_VALUES, derive_memory_kind


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


# --- derive_memory_kind -----------------------------------------------------


@pytest.mark.parametrize("obs_kind,expected", [
    ("decision", "decision"),
    ("correction", "gotcha"),
    ("constraint", "invariant"),
    ("preference", "opinion"),
    ("DECISION", "decision"),       # case-insensitive
    ("  decision  ", "decision"),   # whitespace-tolerant
])
def test_deterministic_mappings(obs_kind, expected):
    assert derive_memory_kind(obs_kind) == expected


def test_finding_with_evidence_is_a_lesson():
    assert derive_memory_kind("finding", evidence_count=2) == "lesson"
    assert derive_memory_kind("finding", evidence_count=5) == "lesson"


def test_finding_without_evidence_is_a_fact():
    assert derive_memory_kind("finding", evidence_count=0) == "fact"
    assert derive_memory_kind("finding", evidence_count=1) == "fact"
    assert derive_memory_kind("finding") == "fact"


def test_open_question_is_unmapped():
    """open_question is a lifecycle state, not a knowledge kind."""
    assert derive_memory_kind("open_question") is None


def test_unknown_or_missing_is_none():
    assert derive_memory_kind(None) is None
    assert derive_memory_kind("") is None
    assert derive_memory_kind("nonsense") is None


def test_every_mapped_value_is_a_valid_memory_kind():
    for obs in ("decision", "correction", "constraint", "preference"):
        assert derive_memory_kind(obs) in MEMORY_KIND_VALUES
    assert derive_memory_kind("finding", 2) in MEMORY_KIND_VALUES
    assert derive_memory_kind("finding", 0) in MEMORY_KIND_VALUES


def test_taxonomy_has_ten_values():
    assert len(MEMORY_KIND_VALUES) == 10


# --- column -----------------------------------------------------------------


def test_memory_kind_column_persists(db):
    m = Memory.create(stage="consolidated", title="t", content="c",
                       kind="decision", memory_kind="decision")
    assert Memory.get_by_id(m.id).memory_kind == "decision"


def test_memory_kind_defaults_null(db):
    m = Memory.create(stage="ephemeral", title="t", content="c")
    assert Memory.get_by_id(m.id).memory_kind is None
