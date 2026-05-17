"""
Database initialisation and lifecycle management for the Peewee ORM layer.

Provides init_db() / close_db() that wire up the deferred SqliteDatabase
defined in core.models, create tables, run migrations, and initialise the
VecStore singleton.
"""

import contextlib
import fcntl
import logging
import os
import re
from pathlib import Path
from typing import Optional

from peewee import SqliteDatabase

_SCHEMA_VERSION = 2

from .models import (
    AffectLog,
    ConsolidationLog,
    EvalRun,
    Memory,
    MemoryEdge,
    MemoryEmbedding,
    NarrativeThread,
    Observation,
    RetrievalLog,
    RetrievalCandidate,
    Rule,
    SessionDigest,
    ThreadMember,
    db,
)

logger = logging.getLogger(__name__)

# Module-level singletons
_vec_store = None
_db_path: Optional[Path] = None
_base_dir: Optional[Path] = None
_project: Optional[str] = None

ALL_TABLES = [
    Memory,
    NarrativeThread,
    ThreadMember,
    MemoryEdge,
    RetrievalLog,
    ConsolidationLog,
    Observation,
    RetrievalCandidate,
    AffectLog,
    EvalRun,
    MemoryEmbedding,
    Rule,
    SessionDigest,
]


def _resolve_db_path(project_context: str = None, base_dir: str = None) -> tuple[Path, Path]:
    """
    Resolve the database path and base directory.

    memesis uses ONE global database. `project_context` no longer routes the
    path — project identity is recorded per-row in the `project` column
    instead (see init_db / get_project). `base_dir` is an explicit override
    retained for tests.

    Returns:
        (base_dir, db_path) tuple.
    """
    if base_dir:
        bd = Path(base_dir).expanduser()
    else:
        bd = Path.home() / ".claude" / "memory"

    return bd, bd / "index.db"


@contextlib.contextmanager
def _migration_lock(db_path: Path):
    """Exclusive cross-process lock around the migration run.

    Multiple processes (the hourly cron, session hooks, the PreToolUse guard)
    each call init_db(). Without serialisation they can execute schema DDL
    concurrently — WAL serialises row writers but not multi-statement DDL plus
    schema-cache reads, which leaves a partial/stale schema (observed
    corruption). flock() on a sibling lock file serialises the migration run.
    """
    lock_path = db_path.with_name(db_path.name + ".migrate.lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def init_db(
    project_context: str = None,
    base_dir: str = None,
    project: str = None,
) -> Path:
    """
    Initialise (or re-initialise) the Peewee database.

    - Resolves the database path
    - Creates directories
    - Opens the SQLite connection with WAL pragmas
    - Creates tables if missing
    - Creates the FTS5 virtual table
    - Runs schema migrations
    - Initialises the VecStore singleton

    `project` records the project identity (a Claude Code directory slug)
    written into the `project` column of new memories/observations — see
    get_project(). It does not affect the database path. When omitted it is
    derived: from project_context (slugified) if given, else from base_dir
    when base_dir points at a `.../<slug>/memory` directory.

    Returns:
        The resolved base_dir Path.
    """
    global _vec_store, _db_path, _base_dir, _project

    bd, dp = _resolve_db_path(project_context=project_context, base_dir=base_dir)
    _base_dir = bd
    _db_path = dp

    if project:
        _project = project
    elif project_context:
        _project = project_slug(project_context)
    elif base_dir and bd.name == "memory":
        # Production callers pass base_dir=.../<slug>/memory; the slug is the
        # project identity (see consolidate_cron.py, scripts/ingest_one.py).
        _project = bd.parent.name
    else:
        _project = None

    # Ensure base directory exists
    bd.mkdir(parents=True, exist_ok=True)

    # Bind deferred database
    db.init(
        str(dp),
        pragmas={
            "journal_mode": "wal",
            "synchronous": "normal",
            "busy_timeout": 5000,
        },
    )

    # All schema DDL — create_tables, the FTS5 virtual table, and the
    # migration run — happens under one exclusive cross-process lock.
    # Concurrent callers (the cron, session hooks, the PreToolUse guard) would
    # otherwise issue DDL simultaneously and race the schema cache or hit
    # "database is locked". Serialised, the first caller does the real work and
    # every later caller's DDL collapses to a cheap idempotent no-op.
    from .migrations import migrations_pending as _pending, run_migrations as _run_mig
    with _migration_lock(dp):
        # Create tables (safe=True → IF NOT EXISTS)
        db.create_tables(ALL_TABLES, safe=True)

        # FTS5 virtual table
        _create_fts_table()

        # Fresh DB: user_version is 0 (SQLite default) after create_tables, but
        # all columns already exist in the model, so we bump to _SCHEMA_VERSION
        # first so the runner seeds (records without re-executing) all files.
        _cursor = db.execute_sql("PRAGMA user_version")
        if _cursor.fetchone()[0] == 0:
            db.execute_sql(f"PRAGMA user_version = {_SCHEMA_VERSION}")

        # Run migrations only when something is actually pending.
        if _pending(db):
            _run_mig(db)

    # VecStore
    from .vec import VecStore

    _vec_store = VecStore(dp)

    return bd


def connect_light(base_dir: str = None) -> Path:
    """Bind the Peewee db without running create_tables or migrations.

    For high-frequency callers (the PreToolUse guard) that read existing
    tables and at most write a counter. They must not run create_tables or
    migrations on every invocation — that would add another concurrent DDL
    writer on every tool call. Row writes are still fine (WAL + busy_timeout).
    Assumes the schema already exists (a session is already running); callers
    must fail open if it does not.

    Returns the resolved base_dir Path.
    """
    global _db_path, _base_dir
    bd, dp = _resolve_db_path(base_dir=base_dir)
    _base_dir = bd
    _db_path = dp
    db.init(
        str(dp),
        pragmas={
            "journal_mode": "wal",
            "synchronous": "normal",
            "busy_timeout": 5000,
        },
    )
    return bd


def make_connection(db_path: str) -> SqliteDatabase:
    """
    Factory for creating a Peewee SqliteDatabase with recommended pragmas.

    This is the canonical path for all new Peewee connections outside the
    main singleton managed by init_db().  It sets busy_timeout=5000ms so
    that concurrent writers back off gracefully instead of immediately
    raising OperationalError: database is locked.

    Args:
        db_path: Absolute path to the SQLite database file.

    Returns:
        A configured (but not yet opened/init'd) SqliteDatabase instance.
    """
    return SqliteDatabase(
        db_path,
        pragmas={
            "journal_mode": "wal",
            "synchronous": "normal",
            "busy_timeout": 5000,
        },
    )


def get_vec_store():
    """Return the VecStore singleton (may be None if init_db hasn't been called)."""
    return _vec_store


def get_db_path() -> Optional[Path]:
    """Return the current database file path."""
    return _db_path


def get_base_dir() -> Optional[Path]:
    """Return the base directory (parent of index.db)."""
    return _base_dir


def get_project() -> Optional[str]:
    """Return the project identity for the active database connection.

    This is the value stamped into the `project` column of new memories and
    observations, used to scope retrieval. None when init_db() could not
    determine a project (e.g. an anonymous test base_dir).
    """
    return _project


_commit_ref: Optional[str] = None
_commit_ref_resolved: bool = False


def get_commit_ref() -> Optional[str]:
    """Return the current git commit ref (short SHA), best-effort.

    Stamped into the `commit_ref` provenance column of new memories so a memory
    can be traced to the code state that produced it. Resolved once and cached;
    returns None outside a git repo or if git is unavailable. Never raises.
    """
    global _commit_ref, _commit_ref_resolved
    if _commit_ref_resolved:
        return _commit_ref
    _commit_ref_resolved = True
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _commit_ref = result.stdout.strip() or None
    except Exception:
        _commit_ref = None
    return _commit_ref


def project_slug(project_context: Optional[str]) -> Optional[str]:
    """Slugify a project path to the canonical `project` column key.

    Retrieval callers pass a filesystem path (os.getcwd()); the write path
    derives the same slug from the memory directory name. Both must run
    through this function so reads and writes agree on one key.
    """
    if not project_context:
        return None
    return re.sub(r"[^a-zA-Z0-9-]", "-", project_context)


def close_db():
    """WAL checkpoint and close the database."""
    try:
        db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _create_fts_table():
    """Create the FTS5 virtual table if it doesn't exist."""
    db.execute_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title, summary, tags, content,
            content='memories',
            content_rowid='rowid'
        )
        """
    )


def _run_migrations():
    """Backward-compat shim — delegates to core.migrations.run_migrations(db).

    Retained because tests outside the migration task import this name.
    New code should call core.migrations.run_migrations(db) directly.
    """
    from .migrations import run_migrations as _run_mig
    _run_mig(db)


