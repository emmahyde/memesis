"""Tests for core/verifier.py — declarative staleness predicates + auto-archive sweep."""

from __future__ import annotations

import subprocess

import pytest

from core.database import close_db, init_db
from core.models import ConsolidationLog, Memory
from core.verifier import evaluate_predicate, run_verifier_sweep


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with one tracked file containing a known marker."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "kept.py").write_text("ALIVE_MARKER = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=root,
        check=True,
    )
    return root


def _mem(project, **kw) -> Memory:
    return Memory.create(stage="consolidated", title="t", content="c", project=str(project), **kw)


# --- evaluate_predicate -----------------------------------------------------


def test_grep_present_holds_when_marker_present(db, repo):
    m = _mem(repo, verify_kind="grep_present", verify_arg="ALIVE_MARKER")
    assert evaluate_predicate("grep_present", "ALIVE_MARKER", m) is True


def test_grep_present_fails_when_marker_gone(db, repo):
    m = _mem(repo, verify_kind="grep_present", verify_arg="GHOST_MARKER")
    assert evaluate_predicate("grep_present", "GHOST_MARKER", m) is False


def test_grep_absent_holds_when_marker_gone(db, repo):
    m = _mem(repo, verify_kind="grep_absent", verify_arg="GHOST_MARKER")
    assert evaluate_predicate("grep_absent", "GHOST_MARKER", m) is True


def test_grep_absent_fails_when_marker_present(db, repo):
    m = _mem(repo, verify_kind="grep_absent", verify_arg="ALIVE_MARKER")
    assert evaluate_predicate("grep_absent", "ALIVE_MARKER", m) is False


def test_file_exists_true_and_false(db, repo):
    m = _mem(repo, verify_kind="file_exists", verify_arg="kept.py")
    assert evaluate_predicate("file_exists", "kept.py", m) is True
    assert evaluate_predicate("file_exists", "missing.py", m) is False


def test_file_exists_path_escape_is_inconclusive(db, repo):
    m = _mem(repo, verify_kind="file_exists", verify_arg="../../../etc/passwd")
    assert evaluate_predicate("file_exists", "../../../etc/passwd", m) is None


def test_unknown_kind_is_inconclusive(db, repo):
    m = _mem(repo, verify_kind="bogus", verify_arg="x")
    assert evaluate_predicate("bogus", "x", m) is None


def test_missing_project_is_inconclusive(db):
    m = Memory.create(stage="consolidated", title="t", content="c", project=None,
                       verify_kind="grep_present", verify_arg="x")
    assert evaluate_predicate("grep_present", "x", m) is None


# --- run_verifier_sweep -----------------------------------------------------


def test_sweep_archives_failed_predicate_only(db, repo):
    stale = _mem(repo, verify_kind="grep_present", verify_arg="GHOST_MARKER")
    fresh = _mem(repo, verify_kind="grep_present", verify_arg="ALIVE_MARKER")
    no_pred = _mem(repo)

    result = run_verifier_sweep()

    assert result["archived"] == 1
    assert result["holds"] == 1
    assert result["checked"] == 2  # no_pred has no verify_kind, not checked
    assert Memory.get_by_id(stale.id).archived_at is not None
    assert Memory.get_by_id(fresh.id).archived_at is None
    assert Memory.get_by_id(no_pred.id).archived_at is None
    # Archiving leaves an audit trail.
    assert ConsolidationLog.select().where(
        ConsolidationLog.memory_id == stale.id,
        ConsolidationLog.action == "deprecated",
    ).count() == 1


def test_sweep_does_not_archive_inconclusive(db):
    m = Memory.create(stage="consolidated", title="t", content="c", project=None,
                       verify_kind="grep_present", verify_arg="x")
    result = run_verifier_sweep()
    assert result["inconclusive"] == 1
    assert result["archived"] == 0
    assert Memory.get_by_id(m.id).archived_at is None
