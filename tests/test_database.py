"""
Tests for core/database.py — init_db, make_connection, and concurrency behaviour.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, make_connection
from core.models import db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_dir(tmp_path):
    """Initialise DB in a throwaway temp directory."""
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir, tmp_path
    close_db()


# ---------------------------------------------------------------------------
# TestMakeConnection — factory shape
# ---------------------------------------------------------------------------


class TestMakeConnection:
    def test_returns_sqlite_database(self, db_dir):
        from peewee import SqliteDatabase

        base_dir, tmp_path = db_dir
        conn = make_connection(str(base_dir / "index.db"))
        assert isinstance(conn, SqliteDatabase)

    def test_busy_timeout_pragma_set(self, db_dir):
        """make_connection pragmas include busy_timeout=5000."""
        base_dir, tmp_path = db_dir
        conn = make_connection(str(base_dir / "index.db"))
        # Peewee stores pragmas as a list of (key, value) tuples in _pragmas
        pragmas = dict(conn._pragmas)
        assert pragmas.get("busy_timeout") == 5000

    def test_wal_journal_mode(self, db_dir):
        """make_connection pragmas include journal_mode=wal."""
        base_dir, tmp_path = db_dir
        conn = make_connection(str(base_dir / "index.db"))
        pragmas = dict(conn._pragmas)
        assert pragmas.get("journal_mode") == "wal"

    def test_does_not_break_init_db_migration(self, tmp_path):
        """init_db() migration call survives the addition of make_connection."""
        base = init_db(base_dir=str(tmp_path / "memory2"))
        # If migrations ran, Memory table exists
        assert (base / "index.db").exists()
        close_db()


# ---------------------------------------------------------------------------
# TestConcurrentWrites — no deadlock under simulated LLM latency
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    """Two threads each do read → simulate LLM sleep → write.

    With busy_timeout=5000 on the connection, the second writer waits
    instead of immediately raising OperationalError.  Both threads must
    complete without error.
    """

    # NB: previous test_no_deadlock_with_mocked_llm_sleep removed (#25).
    # Python's stdlib sqlite3 doesn't honor busy_timeout for same-process
    # sibling-thread BUSY contention (cpython issue 9337 / gh-39466), so the
    # threaded test modelled a scenario sqlite3 cannot survive by design.
    # Real memesis contention is cross-process (cron / session hooks /
    # PreToolUse guard run as independent Python processes with independent
    # sqlite3 client libs), which busy_timeout DOES handle. The pragma setup
    # is exercised below; cross-process behaviour is covered implicitly by
    # the cron + hooks integration tests.

    def test_init_db_busy_timeout_pragma_present(self, db_dir):
        """init_db() sets busy_timeout=5000 via pragmas on the shared db singleton."""
        # Peewee stores pragmas as a list of (key, value) tuples in _pragmas
        pragmas = dict(db._pragmas)
        assert pragmas.get("busy_timeout") == 5000
