"""Tests for core/cursors.py — CursorStore with cwd column."""

import sqlite3
import time

import pytest

from core.cursors import CursorRow, CursorStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "cursors.db"
    cs = CursorStore(db_path=db_path)
    yield cs
    cs.close()


class TestMigrationIdempotency:
    def test_init_on_fresh_db_creates_cwd_column(self, tmp_path):
        db_path = tmp_path / "cursors.db"
        cs = CursorStore(db_path=db_path)
        cs.close()
        conn = sqlite3.connect(str(db_path))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(transcript_cursors)")]
        conn.close()
        assert "cwd" in cols

    def test_init_on_db_without_cwd_adds_column_without_error(self, tmp_path):
        db_path = tmp_path / "cursors.db"
        # Create the table manually without cwd column (simulates old schema).
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE transcript_cursors (
              session_id       TEXT PRIMARY KEY,
              transcript_path  TEXT NOT NULL,
              last_byte_offset INTEGER NOT NULL DEFAULT 0,
              first_seen_at    INTEGER NOT NULL,
              last_run_at      INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        # __init__ should add cwd without raising.
        cs = CursorStore(db_path=db_path)
        cs.close()

        conn = sqlite3.connect(str(db_path))
        cols = [row[1] for row in conn.execute("PRAGMA table_info(transcript_cursors)")]
        conn.close()
        assert "cwd" in cols

    def test_double_init_is_idempotent(self, tmp_path):
        db_path = tmp_path / "cursors.db"
        cs1 = CursorStore(db_path=db_path)
        cs1.close()
        # Second init must not raise (ALTER TABLE on already-present column is suppressed).
        cs2 = CursorStore(db_path=db_path)
        cs2.close()


class TestCwdRoundTrip:
    def test_upsert_with_cwd_then_get_returns_cwd(self, store):
        store.upsert("sess-1", "/path/to/transcript.jsonl", 0, cwd="/home/user/project")
        row = store.get("sess-1")
        assert row is not None
        assert row.cwd == "/home/user/project"

    def test_new_session_without_cwd_stores_null(self, store):
        store.upsert("sess-2", "/path/to/transcript.jsonl", 0)
        row = store.get("sess-2")
        assert row is not None
        assert row.cwd is None

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("no-such-session") is None


class TestCwdPreservation:
    def test_update_offset_without_cwd_preserves_existing_cwd(self, store):
        store.upsert("sess-3", "/path/transcript.jsonl", 0, cwd="/my/project")
        # Update offset only — no cwd kwarg.
        store.upsert("sess-3", "/path/transcript.jsonl", 512)
        row = store.get("sess-3")
        assert row is not None
        assert row.cwd == "/my/project"
        assert row.last_byte_offset == 512

    def test_explicit_none_cwd_does_not_overwrite_existing_cwd(self, store):
        store.upsert("sess-4", "/path/transcript.jsonl", 0, cwd="/preserved")
        store.upsert("sess-4", "/path/transcript.jsonl", 100, cwd=None)
        row = store.get("sess-4")
        assert row is not None
        assert row.cwd == "/preserved"

    def test_new_session_cwd_none_stays_null(self, store):
        store.upsert("sess-5", "/path/transcript.jsonl", 0, cwd=None)
        row = store.get("sess-5")
        assert row is not None
        assert row.cwd is None

    def test_cwd_can_be_set_on_existing_null_cwd_row(self, store):
        store.upsert("sess-6", "/path/transcript.jsonl", 0)
        store.upsert("sess-6", "/path/transcript.jsonl", 0, cwd="/new/project")
        row = store.get("sess-6")
        assert row is not None
        assert row.cwd == "/new/project"


class TestCursorRowDataclass:
    def test_cursor_row_has_cwd_field(self):
        row = CursorRow(
            session_id="s",
            transcript_path="/p",
            last_byte_offset=0,
            first_seen_at=1,
            last_run_at=2,
            cwd="/cwd",
        )
        assert row.cwd == "/cwd"

    def test_cursor_row_cwd_defaults_to_none(self):
        row = CursorRow(
            session_id="s",
            transcript_path="/p",
            last_byte_offset=0,
            first_seen_at=1,
            last_run_at=2,
        )
        assert row.cwd is None
