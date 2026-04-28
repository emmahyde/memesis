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

    # FTS5 virtual table and migrations (must run raw SQL)
    _create_fts_table()
    _run_migrations()

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
    """
    Run schema migrations for backwards compatibility.

    - Add 'content' column to memories if missing
    - Add 'archived_at' column if missing
    - Add 'subsumed_by' column if missing
    - Add 'project_context' to retrieval_log if missing
    - Add 'last_surfaced_at' to narrative_threads if missing
    - Rebuild consolidation_log CHECK constraint if outdated
    """
    # Helper: get column names for a table
    def _columns(table_name):
        cursor = db.execute_sql(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]

    # memories migrations
    mem_cols = _columns("memories")
    for col, typ in [
        ("content", "TEXT"),
        ("archived_at", "TEXT"),
        ("subsumed_by", "TEXT"),
        ("echo_count", "INTEGER DEFAULT 0"),
        ("next_injection_due", "TEXT"),
        ("injection_ease_factor", "REAL DEFAULT 2.5"),
        ("injection_interval_days", "REAL DEFAULT 1.0"),
        ("files_modified", "TEXT DEFAULT '[]'"),
    ]:
        if col not in mem_cols:
            try:
                db.execute_sql(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
            except Exception:
                pass

    # retrieval_log migration
    ret_cols = _columns("retrieval_log")
    for col, typ in [
        ("project_context", "TEXT"),
        ("query_text", "TEXT"),
        ("limit_count", "INTEGER"),
        ("selected_count", "INTEGER"),
        ("metadata", "TEXT"),
    ]:
        if col not in ret_cols:
            try:
                db.execute_sql(f"ALTER TABLE retrieval_log ADD COLUMN {col} {typ}")
            except Exception:
                pass

    # narrative_threads migration
    nt_cols = _columns("narrative_threads")
    for col, typ in [
        ("last_surfaced_at", "TEXT"),
        ("arc_affect", "TEXT"),
    ]:
        if col not in nt_cols:
            try:
                db.execute_sql(f"ALTER TABLE narrative_threads ADD COLUMN {col} {typ}")
            except Exception:
                pass

    # memory_edges migration
    edge_cols = _columns("memory_edges")
    if "metadata" not in edge_cols:
        try:
            db.execute_sql("ALTER TABLE memory_edges ADD COLUMN metadata TEXT")
        except Exception:
            pass

    # Consolidation log CHECK constraint migration
    cursor = db.execute_sql(
        "SELECT sql FROM sqlite_master WHERE name='consolidation_log'"
    )
    schema_row = cursor.fetchone()
    if schema_row and "'subsumed'" not in (schema_row[0] or ""):
        rows = list(
            db.execute_sql(
                "SELECT timestamp, session_id, action, memory_id, "
                "from_stage, to_stage, rationale FROM consolidation_log"
            ).fetchall()
        )
        db.execute_sql("DROP TABLE consolidation_log")
        db.execute_sql(
            """
            CREATE TABLE consolidation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT,
                action TEXT CHECK(action IN ('kept', 'pruned', 'promoted',
                    'demoted', 'merged', 'deprecated', 'subsumed', 'archived')),
                memory_id TEXT,
                from_stage TEXT,
                to_stage TEXT,
                rationale TEXT
            )
            """
        )
        for r in rows:
            db.execute_sql(
                "INSERT INTO consolidation_log "
                "(timestamp, session_id, action, memory_id, from_stage, to_stage, rationale) "
                "VALUES (?,?,?,?,?,?,?)",
                list(r),
            )

    # consolidation_log observer instrumentation columns
    con_cols = _columns("consolidation_log")
    for col, typ in [
        ("prompt", "TEXT"),
        ("llm_response", "TEXT"),
        ("model", "TEXT"),
        ("input_tokens", "INTEGER"),
        ("output_tokens", "INTEGER"),
        ("latency_ms", "INTEGER"),
        ("input_observation_refs", "TEXT"),
    ]:
        if col not in con_cols:
            try:
                db.execute_sql(f"ALTER TABLE consolidation_log ADD COLUMN {col} {typ}")
            except Exception:
                pass
