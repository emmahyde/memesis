"""
Tests for core/database.py — init_db, make_connection, and concurrency behaviour.
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, make_connection
from core.models import Memory, db


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

    def test_no_deadlock_with_mocked_llm_sleep(self, db_dir):
        """Concurrent write-after-sleep does not deadlock or raise OperationalError."""
        base_dir, tmp_path = db_dir

        errors: list[Exception] = []
        results: list[str] = []

        def worker(session_id: str) -> None:
            """Simulate: read phase, LLM call (mocked with sleep), write phase."""
            try:
                # Read phase — no write lock held
                _ = list(Memory.select().where(Memory.stage == "consolidated"))

                # Simulate LLM network latency
                time.sleep(0.1)

                # Write phase
                now = __import__("datetime").datetime.now().isoformat()
                mem = Memory.create(
                    stage="consolidated",
                    title=f"Concurrent write from {session_id}",
                    summary="test",
                    content="test content",
                    tags="[]",
                    importance=0.5,
                    reinforcement_count=0,
                    created_at=now,
                    updated_at=now,
                    source_session=session_id,
                )
                results.append(str(mem.id))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("sess-a",))
        t2 = threading.Thread(target=worker, args=("sess-b",))

        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"Thread(s) raised: {errors}"
        assert len(results) == 2, f"Expected 2 writes, got {len(results)}"

    def test_init_db_busy_timeout_pragma_present(self, db_dir):
        """init_db() sets busy_timeout=5000 via pragmas on the shared db singleton."""
        # Peewee stores pragmas as a list of (key, value) tuples in _pragmas
        pragmas = dict(db._pragmas)
        assert pragmas.get("busy_timeout") == 5000
