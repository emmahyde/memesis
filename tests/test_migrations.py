"""
Tests for core.migrations — RISK-10 migration runner.

Covers:
- Forward run from empty DB (new install, user_version=0 path)
- Seeding path from user_version >= 2 (existing DB with inline migrations already applied)
- Idempotency: applying twice is a no-op
- .py migration up(conn) is called correctly
- schema_migrations table records applied versions
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Helpers to build a minimal in-memory peewee-compatible database stub
# ---------------------------------------------------------------------------

import peewee


def _make_db(path: str) -> peewee.SqliteDatabase:
    """Create and connect a SqliteDatabase at `path`."""
    db = peewee.SqliteDatabase(
        path,
        pragmas={"journal_mode": "wal"},
    )
    db.connect()
    return db


def _tables(db: peewee.SqliteDatabase) -> set:
    cursor = db.execute_sql(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    return {row[0] for row in cursor.fetchall()}


def _applied(db: peewee.SqliteDatabase) -> set:
    cursor = db.execute_sql("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _user_version(db: peewee.SqliteDatabase) -> int:
    cursor = db.execute_sql("PRAGMA user_version")
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_db(tmp_path):
    """Fresh SQLite database with no schema, user_version=0."""
    path = str(tmp_path / "test.db")
    db = _make_db(path)
    # Create a minimal 'memories' table so ALTER TABLE statements don't fail for
    # missing table (the runner handles duplicate-column errors, not missing table)
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            stage TEXT,
            importance REAL,
            created_at TEXT,
            updated_at TEXT,
            tags TEXT
        )
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT,
            session_id TEXT,
            was_used INTEGER,
            score REAL,
            retrieved_at TEXT
        )
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS narrative_threads (
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT
        )
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS memory_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            target_id TEXT,
            relation TEXT
        )
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS consolidation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT,
            action TEXT CHECK(action IN ('kept', 'pruned', 'promoted', 'demoted', 'merged', 'deprecated')),
            memory_id TEXT,
            from_stage TEXT,
            to_stage TEXT,
            rationale TEXT
        )
        """
    )
    yield db
    try:
        db.close()
    except Exception:
        pass


@pytest.fixture()
def seeded_db(tmp_path):
    """Database with user_version=2 (simulates an existing install that ran inline ALTERs)."""
    path = str(tmp_path / "seeded.db")
    db = _make_db(path)
    # Create tables with all columns already present (simulates user who ran old inline code)
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT,
            stage TEXT,
            importance REAL,
            created_at TEXT,
            updated_at TEXT,
            tags TEXT,
            content TEXT,
            archived_at TEXT,
            subsumed_by TEXT,
            source TEXT DEFAULT 'human'
        )
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT,
            session_id TEXT,
            was_used INTEGER,
            score REAL,
            retrieved_at TEXT,
            project_context TEXT
        )
        """
    )
    db.execute_sql(
        "CREATE TABLE IF NOT EXISTS narrative_threads (id TEXT PRIMARY KEY, title TEXT)"
    )
    db.execute_sql(
        "CREATE TABLE IF NOT EXISTS memory_edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT)"
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS consolidation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT,
            action TEXT CHECK(action IN ('kept','pruned','promoted','demoted','merged','deprecated','subsumed','archived')),
            memory_id TEXT,
            from_stage TEXT,
            to_stage TEXT,
            rationale TEXT
        )
        """
    )
    # Set user_version to 2 — this is what the old inline _run_migrations() did
    db.execute_sql("PRAGMA user_version = 2")
    yield db
    try:
        db.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunMigrationsForwardRun:
    """Forward run: fresh DB, user_version=0, migrations execute."""

    def test_schema_migrations_table_created(self, empty_db):
        from core.migrations import run_migrations
        run_migrations(empty_db, seed_threshold=2)
        assert "schema_migrations" in _tables(empty_db)

    def test_migration_files_recorded(self, empty_db):
        from core.migrations import run_migrations, _migration_files
        run_migrations(empty_db, seed_threshold=2)
        applied = _applied(empty_db)
        for path in _migration_files():
            assert path.stem in applied, f"Migration not recorded: {path.stem}"

    def test_alters_applied_to_empty_db(self, empty_db):
        from core.migrations import run_migrations
        run_migrations(empty_db, seed_threshold=2)
        # Check that columns were added to memories
        cursor = empty_db.execute_sql("PRAGMA table_info(memories)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "content" in cols
        assert "archived_at" in cols
        assert "source" in cols

    def test_consolidation_log_check_migrated(self, empty_db):
        from core.migrations import run_migrations
        run_migrations(empty_db, seed_threshold=2)
        cursor = empty_db.execute_sql(
            "SELECT sql FROM sqlite_master WHERE name='consolidation_log'"
        )
        row = cursor.fetchone()
        assert row is not None
        assert "'subsumed'" in (row[0] or "")


class TestRunMigrationsSeedingPath:
    """Seeding path: user_version >= 2, migrations recorded but not executed."""

    def test_all_migrations_recorded_without_executing(self, seeded_db):
        from core.migrations import run_migrations, _migration_files
        run_migrations(seeded_db, seed_threshold=2)
        applied = _applied(seeded_db)
        for path in _migration_files():
            assert path.stem in applied, f"Migration not seeded: {path.stem}"

    def test_seeding_does_not_re_alter_existing_columns(self, seeded_db):
        """Seeding should not raise even though columns already exist."""
        from core.migrations import run_migrations
        # Should not raise
        run_migrations(seeded_db, seed_threshold=2)

    def test_seeding_preserves_existing_data(self, seeded_db):
        """Seeding must not drop/recreate tables that already have data."""
        seeded_db.execute_sql(
            "INSERT INTO memories (id, title, stage) VALUES ('m1', 'Test', 'ephemeral')"
        )
        from core.migrations import run_migrations
        run_migrations(seeded_db, seed_threshold=2)
        cursor = seeded_db.execute_sql("SELECT COUNT(*) FROM memories")
        assert cursor.fetchone()[0] == 1


class TestIdempotency:
    """Applying migrations twice is a no-op."""

    def test_double_run_does_not_raise(self, empty_db):
        from core.migrations import run_migrations
        run_migrations(empty_db, seed_threshold=2)
        # Should not raise
        run_migrations(empty_db, seed_threshold=2)

    def test_double_run_same_applied_set(self, empty_db):
        from core.migrations import run_migrations, _migration_files
        run_migrations(empty_db, seed_threshold=2)
        applied_first = _applied(empty_db)
        run_migrations(empty_db, seed_threshold=2)
        applied_second = _applied(empty_db)
        assert applied_first == applied_second

    def test_seeding_double_run(self, seeded_db):
        from core.migrations import run_migrations
        run_migrations(seeded_db, seed_threshold=2)
        applied_first = _applied(seeded_db)
        run_migrations(seeded_db, seed_threshold=2)
        applied_second = _applied(seeded_db)
        assert applied_first == applied_second


class TestPyMigration:
    """.py migration up(conn) is correctly loaded and called."""

    def test_up_callable_invoked(self, tmp_path):
        """Confirm that a .py migration's up() is called during forward run."""
        # Use the empty_db approach but check consolidation_log was rebuilt
        path = str(tmp_path / "py_test.db")
        db = _make_db(path)
        # Build a consolidation_log WITHOUT 'subsumed' in CHECK
        db.execute_sql(
            """
            CREATE TABLE consolidation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT,
                action TEXT CHECK(action IN ('kept', 'pruned', 'promoted', 'demoted', 'merged', 'deprecated')),
                memory_id TEXT,
                from_stage TEXT,
                to_stage TEXT,
                rationale TEXT
            )
            """
        )
        # Also create minimal memories/retrieval_log/etc so the SQL migration doesn't fail
        db.execute_sql("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, title TEXT, stage TEXT)")
        db.execute_sql(
            "CREATE TABLE IF NOT EXISTS retrieval_log (id INTEGER PRIMARY KEY, memory_id TEXT, session_id TEXT, was_used INTEGER)"
        )
        db.execute_sql("CREATE TABLE IF NOT EXISTS narrative_threads (id TEXT PRIMARY KEY)")
        db.execute_sql("CREATE TABLE IF NOT EXISTS memory_edges (id INTEGER PRIMARY KEY)")

        from core.migrations import run_migrations
        run_migrations(db, seed_threshold=2)

        # The .py migration should have rebuilt consolidation_log with 'subsumed'
        cursor = db.execute_sql(
            "SELECT sql FROM sqlite_master WHERE name='consolidation_log'"
        )
        row = cursor.fetchone()
        assert row is not None
        assert "'subsumed'" in (row[0] or ""), "consolidation_log CHECK not updated by .py migration"
        db.close()


class TestInitDbIntegration:
    """init_db() calls run_migrations via the new integration point."""

    def test_init_db_creates_schema_migrations(self, tmp_path):
        from core.database import init_db, close_db
        try:
            init_db(base_dir=str(tmp_path))
            # Import db after init_db
            from core.models import db as peewee_db
            assert "schema_migrations" in _tables(peewee_db)
        finally:
            close_db()

    def test_init_db_idempotent(self, tmp_path):
        """Calling init_db twice on the same path should not raise."""
        from core.database import init_db, close_db
        try:
            init_db(base_dir=str(tmp_path))
            close_db()
            init_db(base_dir=str(tmp_path))
        finally:
            close_db()
