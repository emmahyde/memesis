"""
Database initialisation and lifecycle management for the Peewee ORM layer.

Provides init_db() / close_db() that wire up the deferred SqliteDatabase
defined in core.models, create tables, run migrations, and initialise the
VecStore singleton.
"""

import logging
import re
from pathlib import Path
from typing import Optional

_SCHEMA_VERSION = 2

from .models import (
    AffectLog,
    ConsolidationLog,
    EvalRun,
    Memory,
    MemoryEdge,
    NarrativeThread,
    Observation,
    RetrievalLog,
    RetrievalCandidate,
    ThreadMember,
    db,
)

logger = logging.getLogger(__name__)

# Module-level singletons
_vec_store = None
_db_path: Optional[Path] = None
_base_dir: Optional[Path] = None

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
]


def _resolve_db_path(project_context: str = None, base_dir: str = None) -> tuple[Path, Path]:
    """
    Resolve the database path and base directory.

    Returns:
        (base_dir, db_path) tuple.
    """
    if project_context:
        path_hash = re.sub(r"[^a-zA-Z0-9-]", "-", project_context)
        bd = Path.home() / ".claude" / "projects" / path_hash / "memory"
    elif base_dir:
        bd = Path(base_dir).expanduser()
    else:
        bd = Path.home() / ".claude" / "memory"

    return bd, bd / "index.db"


def init_db(
    project_context: str = None,
    base_dir: str = None,
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

    Returns:
        The resolved base_dir Path.
    """
    global _vec_store, _db_path, _base_dir

    bd, dp = _resolve_db_path(project_context=project_context, base_dir=base_dir)
    _base_dir = bd
    _db_path = dp

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

    # Create tables (safe_create=True → IF NOT EXISTS)
    db.create_tables(ALL_TABLES, safe=True)

    # FTS5 virtual table
    _create_fts_table()

    # Schema migrations via core.migrations runner (RISK-10).
    # Fresh DB: user_version is 0 (SQLite default) after create_tables, but all
    # columns already exist in the model, so we bump to _SCHEMA_VERSION first so
    # the runner seeds (records without re-executing) all migration files.
    _cursor = db.execute_sql("PRAGMA user_version")
    if _cursor.fetchone()[0] == 0:
        db.execute_sql(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    from .migrations import run_migrations as _run_mig
    _run_mig(db)

    # VecStore
    from .vec import VecStore

    _vec_store = VecStore(dp)

    return bd


def get_vec_store():
    """Return the VecStore singleton (may be None if init_db hasn't been called)."""
    return _vec_store


def get_db_path() -> Optional[Path]:
    """Return the current database file path."""
    return _db_path


def get_base_dir() -> Optional[Path]:
    """Return the base directory (parent of index.db)."""
    return _base_dir


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


