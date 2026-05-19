"""Provenance fields — memories carry source_session, commit_ref, source_pr."""

from __future__ import annotations

from core.database import close_db, get_commit_ref, init_db
from core.models import Memory


def test_memory_has_provenance_columns(tmp_path):
    """The provenance columns exist and accept values."""
    init_db(base_dir=str(tmp_path / "memory"))
    try:
        m = Memory.create(
            stage="consolidated",
            title="t",
            content="c",
            source_session="sess-123",
            commit_ref="abc1234",
            source_pr="#42",
        )
        fresh = Memory.get_by_id(m.id)
        assert fresh.source_session == "sess-123"
        assert fresh.commit_ref == "abc1234"
        assert fresh.source_pr == "#42"
    finally:
        close_db()


def test_provenance_columns_default_null(tmp_path):
    """Provenance columns are nullable and default to None."""
    init_db(base_dir=str(tmp_path / "memory"))
    try:
        m = Memory.create(stage="ephemeral", title="t", content="c")
        fresh = Memory.get_by_id(m.id)
        assert fresh.source_pr is None
        assert fresh.commit_ref is None
    finally:
        close_db()


def test_get_commit_ref_is_best_effort():
    """get_commit_ref returns a short SHA inside a git repo, or None — never raises."""
    ref = get_commit_ref()
    # memesis is a git repo, so a ref is expected here; the contract is just
    # "a non-empty string or None".
    assert ref is None or (isinstance(ref, str) and ref.strip() == ref and ref)


def test_get_commit_ref_is_cached():
    """Repeated calls return the same cached value."""
    assert get_commit_ref() == get_commit_ref()
