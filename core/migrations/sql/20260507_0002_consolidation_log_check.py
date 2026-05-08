"""
Migration: 20260507_0002_consolidation_log_check

Rebuilds the consolidation_log table to add 'subsumed' and 'archived' to the
CHECK constraint on the action column.

This is a .py migration because it requires multi-step DDL (read, drop, recreate,
reinsert) that cannot be expressed as simple ALTER statements.
"""


def up(conn) -> None:
    """
    Rebuild consolidation_log with updated CHECK constraint if needed.

    conn: peewee database object (exposes .execute_sql()).
    """
    cursor = conn.execute_sql(
        "SELECT sql FROM sqlite_master WHERE name='consolidation_log'"
    )
    row = cursor.fetchone()
    if not row:
        # Table doesn't exist yet — nothing to migrate
        return

    schema_sql = row[0] or ""
    if "'subsumed'" in schema_sql:
        # Already migrated
        return

    # Read existing rows (only columns that existed before observer instrumentation)
    rows = list(
        conn.execute_sql(
            "SELECT timestamp, session_id, action, memory_id, "
            "from_stage, to_stage, rationale FROM consolidation_log"
        ).fetchall()
    )

    conn.execute_sql("DROP TABLE consolidation_log")
    conn.execute_sql(
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
        conn.execute_sql(
            "INSERT INTO consolidation_log "
            "(timestamp, session_id, action, memory_id, from_stage, to_stage, rationale) "
            "VALUES (?,?,?,?,?,?,?)",
            list(r),
        )
