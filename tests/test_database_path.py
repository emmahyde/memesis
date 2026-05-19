"""DB path resolution is single-location; project identity is a column, not a path."""

from pathlib import Path

from core.database import _resolve_db_path, init_db, get_project, close_db


def test_resolve_db_path_ignores_project_context():
    """project_context no longer routes the path — it only sets the column."""
    bd, dp = _resolve_db_path(project_context="/Users/x/projects/foo")
    assert bd == Path.home() / ".claude" / "memory"
    assert dp == Path.home() / ".claude" / "memory" / "index.db"


def test_resolve_db_path_default_is_canonical():
    bd, dp = _resolve_db_path()
    assert dp == Path.home() / ".claude" / "memory" / "index.db"


def test_resolve_db_path_base_dir_override_survives():
    """base_dir is the test-only escape hatch and still routes the path."""
    bd, dp = _resolve_db_path(base_dir="/tmp/test-mem")
    assert bd == Path("/tmp/test-mem")
    assert dp == Path("/tmp/test-mem") / "index.db"


def test_init_db_records_project_from_context(tmp_path):
    init_db(base_dir=str(tmp_path), project_context="/Users/x/projects/foo")
    try:
        assert get_project() == "-Users-x-projects-foo"
    finally:
        close_db()


def test_init_db_records_explicit_project(tmp_path):
    init_db(base_dir=str(tmp_path), project="my-slug")
    try:
        assert get_project() == "my-slug"
    finally:
        close_db()
