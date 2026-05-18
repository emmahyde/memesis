"""Pytest configuration and fixtures."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure tests never hit the real Bedrock API
os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)

# Redirect observability JSONL output to a session-scoped temp dir so test
# runs don't pollute backfill-output/observability/ in the real repo.
_TEST_OBS_DIR = Path(tempfile.mkdtemp(prefix="memesis-test-obs-"))
os.environ["MEMESIS_OBS_DIR"] = str(_TEST_OBS_DIR)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir

# Real user memory store — tests must never touch it (CLAUDE.md Rule 3).
# HOME is captured at import time, before any fixture redirects it.
_REAL_HOME = os.environ.get("HOME", os.path.expanduser("~"))
_REAL_MEMORY_DB = Path(_REAL_HOME) / ".claude" / "memory" / "index.db"


@pytest.fixture(autouse=True)
def _redirect_home_to_tmp(monkeypatch, tmp_path):
    """Default HOME to a per-test temp dir so global DB path stays isolated."""
    monkeypatch.setenv("HOME", str(tmp_path))


def _is_real_home(value: str | None) -> bool:
    """Return True when HOME points at the real user home directory."""
    if not value:
        return False
    try:
        return (
            Path(value).expanduser().resolve()
            == Path(_REAL_HOME).expanduser().resolve()
        )
    except Exception:
        return Path(value).expanduser() == Path(_REAL_HOME).expanduser()


@pytest.fixture(autouse=True)
def _guard_subprocess_home(monkeypatch):
    """Fail fast if a test subprocess attempts to use the real HOME."""
    orig_run = subprocess.run
    orig_popen = subprocess.Popen

    def _assert_safe_home(kwargs):
        env = kwargs.get("env")
        home = (
            os.environ.get("HOME")
            if env is None
            else env.get("HOME", os.environ.get("HOME"))
        )
        if _is_real_home(home):
            pytest.fail(
                "Subprocess attempted to use real HOME; set HOME to tmp_path in test env."
            )

    def _guarded_run(*args, **kwargs):
        _assert_safe_home(kwargs)
        return orig_run(*args, **kwargs)

    def _guarded_popen(*args, **kwargs):
        _assert_safe_home(kwargs)
        return orig_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)
    yield


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp)


@pytest.fixture
def memory_store(temp_dir):
    """Initialize the Peewee database with temporary storage."""
    init_db(base_dir=str(temp_dir))
    yield temp_dir  # yield the base_dir path
    close_db()


@pytest.fixture
def project_memory_store(temp_dir):
    """Initialize the Peewee database with project context."""
    # Override home to use temp_dir
    original_home = os.environ.get("HOME")
    os.environ["HOME"] = str(temp_dir)

    try:
        init_db(project_context="/Users/test/my-project")
        yield temp_dir
    finally:
        close_db()
        if original_home:
            os.environ["HOME"] = original_home
        else:
            del os.environ["HOME"]
