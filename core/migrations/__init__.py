"""
Migration runner for memesis schema versioning (RISK-10).

Scans core/migrations/sql/ for *.sql and *.py files in lexicographic order,
applies each once, and records applied versions in schema_migrations.

Usage:
    from core.migrations import run_migrations
    run_migrations(db)  # db is the peewee database object

File naming convention:
    YYYYMMDD_NNNN_description.sql   — plain DDL statements (one per line)
    YYYYMMDD_NNNN_description.py    — multi-step Python migrations; must export up(conn)

Seeding behaviour:
    When called on a database that already has PRAGMA user_version >= SEED_THRESHOLD,
    all migration files are recorded as applied in schema_migrations WITHOUT executing
    their SQL. This prevents re-running ALTERs that succeeded under the old inline
    codepath.

Idempotency:
    A migration is skipped if its version key already exists in schema_migrations.
    SQL migrations also wrap each statement in a try/except so duplicate-column ALTERs
    and already-existing indexes are silently ignored.
"""

import importlib.util
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum user_version that triggers the seeding path (skip execution, just record).
SEED_THRESHOLD = 2

# Migrations that were applied inline in the old database.py codepath.
# Only these are seeded (not executed) on existing DBs (user_version >= SEED_THRESHOLD).
# Migrations added after the runner was introduced must always execute.
_LEGACY_MIGRATION_STEMS = frozenset({
    "20260507_0001_initial_alters",
    "20260507_0002_consolidation_log_check",
})

# Directory containing migration files (relative to this package).
_SQL_DIR = Path(__file__).parent / "sql"

# Table DDL — created once on first run_migrations() call.
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def _ensure_migrations_table(conn) -> None:
    conn.execute_sql(_SCHEMA_MIGRATIONS_DDL)


def _applied_versions(conn) -> set:
    cursor = conn.execute_sql("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _record_version(conn, version: str) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute_sql(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        [version, now],
    )


def _migration_files() -> list[Path]:
    """Return migration files sorted lexicographically (timestamp-prefix order)."""
    if not _SQL_DIR.exists():
        return []
    files = sorted(
        f for f in _SQL_DIR.iterdir()
        if f.suffix in (".sql", ".py") and not f.name.startswith("_")
    )
    return files


def _apply_sql(conn, path: Path) -> None:
    """Apply a .sql migration file. Each statement is run individually; ALTER
    errors (duplicate column, etc.) are caught and logged, not re-raised."""
    sql = path.read_text(encoding="utf-8")
    # Split on semicolons, ignore blank/comment-only chunks
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        # Skip comment-only blocks
        non_comment = "\n".join(
            line for line in stmt.splitlines()
            if not line.strip().startswith("--")
        ).strip()
        if not non_comment:
            continue
        try:
            conn.execute_sql(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            # Tolerate "duplicate column name" and "index already exists"
            if "duplicate column" in msg or "already exists" in msg:
                logger.debug("Skipping already-applied statement in %s: %s", path.name, exc)
            else:
                raise


def _apply_py(conn, path: Path) -> None:
    """Load a .py migration module and call its up(conn) function."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    if not callable(getattr(module, "up", None)):
        raise AttributeError(f"Migration {path.name} must export an up(conn) callable")
    module.up(conn)


def run_migrations(conn, seed_threshold: int = SEED_THRESHOLD) -> None:
    """
    Apply pending migrations to the database.

    Parameters
    ----------
    conn:
        Peewee database object.  Must expose .execute_sql() and .atomic().
    seed_threshold:
        user_version value at or above which all unapplied migrations are
        recorded as already-applied instead of executed (seeding path).
    """
    _ensure_migrations_table(conn)

    # Determine current user_version for seeding decision
    cursor = conn.execute_sql("PRAGMA user_version")
    user_version = cursor.fetchone()[0]
    seed_mode = user_version >= seed_threshold

    if seed_mode:
        logger.debug(
            "seed mode: user_version=%d >= %d; marking migrations applied without executing",
            user_version,
            seed_threshold,
        )

    applied = _applied_versions(conn)
    files = _migration_files()

    for path in files:
        version = path.stem  # e.g. "20260507_0001_initial_alters"
        if version in applied:
            logger.debug("Migration already applied: %s", version)
            continue

        if seed_mode and version in _LEGACY_MIGRATION_STEMS:
            # Legacy migration — was applied inline; record without executing
            with conn.atomic():
                _record_version(conn, version)
            logger.info("Seeded (not executed): %s", version)
            continue

        logger.info("Applying migration: %s", version)
        try:
            with conn.atomic():
                if path.suffix == ".sql":
                    _apply_sql(conn, path)
                elif path.suffix == ".py":
                    _apply_py(conn, path)
                else:
                    logger.warning("Unknown migration type, skipping: %s", path)
                    continue
                _record_version(conn, version)
        except Exception:
            logger.exception("Migration failed: %s", version)
            raise

    logger.debug("Migrations complete. Applied: %d files.", len(files))
