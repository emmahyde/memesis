"""Migration 0019: collapse dual kind taxonomy into single 7+2 KIND_VALUES.

Renames old obs.kind and memory_kind values to the new unified vocabulary,
then drops the memory_kind column entirely (single-taxonomy architecture).

Pre-migration cross-tab (2026-05-23, active rows):
  obs.kind:   finding=145, decision=51, None=38, preference=37,
              correction=24, open_question=18, constraint=17, hypothesis=11
  memory_kind: fact=147, None=54, decision=51, opinion=38,
               gotcha=24, invariant=17, lesson=10

Post-migration expected kind distribution:
  fact          = 147 (finding rows + memory_kind=fact when kind NULL)
  decision      = 51
  preference    = 38 (memory_kind=opinion rows)
  correction    = 24 (memory_kind=gotcha rows)
  directive     = 17 (constraint rows + memory_kind=invariant rows)
  lesson        = 10
  open_question = 18 (unchanged)
  hypothesis    = 11 (unchanged)
  None          = ~25 (rows with no kind and no memory_kind that maps)

Idempotency: all UPDATE WHERE clauses are no-ops on re-run (source value no
longer present after first pass). Table-recreate guard checks for
memory_kind column presence before rebuilding.
"""


def up(conn) -> None:
    # -----------------------------------------------------------------------
    # Step 1: Rename obs.kind values that changed in the new taxonomy.
    # -----------------------------------------------------------------------

    # finding → fact  (was the "dumping ground" kind; now a proper content kind)
    conn.execute_sql("UPDATE memories SET kind='fact' WHERE kind='finding'")

    # constraint → directive  (replaced by directive)
    conn.execute_sql("UPDATE memories SET kind='directive' WHERE kind='constraint'")

    # -----------------------------------------------------------------------
    # Step 2: Promote memory_kind into obs.kind for rows where memory_kind
    # carries a more specific classification than the current obs.kind.
    # -----------------------------------------------------------------------

    # memory_kind=gotcha → kind=correction
    conn.execute_sql(
        "UPDATE memories SET kind='correction' "
        "WHERE memory_kind='gotcha' AND (kind IS NULL OR kind='correction')"
    )

    # memory_kind=invariant → kind=directive
    conn.execute_sql(
        "UPDATE memories SET kind='directive' "
        "WHERE memory_kind='invariant' AND (kind IS NULL OR kind='directive')"
    )

    # memory_kind=opinion → kind=preference
    conn.execute_sql(
        "UPDATE memories SET kind='preference' "
        "WHERE memory_kind='opinion' AND (kind IS NULL OR kind='preference')"
    )

    # memory_kind=fact → kind=fact  (only when kind is NULL)
    conn.execute_sql(
        "UPDATE memories SET kind='fact' "
        "WHERE memory_kind='fact' AND kind IS NULL"
    )

    # memory_kind=lesson → kind=lesson  (only when kind is NULL)
    conn.execute_sql(
        "UPDATE memories SET kind='lesson' "
        "WHERE memory_kind='lesson' AND kind IS NULL"
    )

    # memory_kind=decision → kind=decision  (only when kind is NULL)
    conn.execute_sql(
        "UPDATE memories SET kind='decision' "
        "WHERE memory_kind='decision' AND kind IS NULL"
    )

    # memory_kind=goal → kind=goal  (only when kind is NULL)
    conn.execute_sql(
        "UPDATE memories SET kind='goal' "
        "WHERE memory_kind='goal' AND kind IS NULL"
    )

    # -----------------------------------------------------------------------
    # Step 3: Drop triggers from migration 0018 (memory_kind CHECK triggers).
    # -----------------------------------------------------------------------
    conn.execute_sql("DROP TRIGGER IF EXISTS memory_kind_check_insert")
    conn.execute_sql("DROP TRIGGER IF EXISTS memory_kind_check_update")

    # -----------------------------------------------------------------------
    # Step 4: Drop the memory_kind column via table-recreate (SQLite pattern).
    # Guard: if memory_kind is already absent, the migration already ran.
    # -----------------------------------------------------------------------
    cursor = conn.execute_sql("PRAGMA table_info(memories)")
    columns = [(row[1], row[2], row[3], row[4]) for row in cursor.fetchall()]
    col_names = [c[0] for c in columns]

    if "memory_kind" not in col_names:
        # Already applied — nothing more to do.
        return

    _KIND_VALUES = (
        "decision", "fact", "lesson", "correction",
        "directive", "preference", "goal",
        "open_question", "hypothesis",
    )
    kind_check = ", ".join(f"'{k}'" for k in _KIND_VALUES)

    # Build new table DDL from actual column list, excluding memory_kind.
    # Columns obtained from PRAGMA table_info above; NOT NULL and DEFAULT
    # are preserved. PRIMARY KEY comes from sqlite_autoindex (id column).
    _KEEP_COLS = [c[0] for c in columns if c[0] != "memory_kind"]

    def _col_def(col_info):
        name, typ, notnull, default = col_info
        if name == "memory_kind":
            return None
        parts = [f'"{name}"']
        if typ:
            parts.append(typ)
        if name == "id":
            parts.append("NOT NULL PRIMARY KEY")
        elif notnull:
            parts.append("NOT NULL")
        if default is not None:
            parts.append(f"DEFAULT {default}")
        return " ".join(parts)

    # Build kind column with CHECK constraint
    def _col_def_with_kind_check(col_info):
        name, typ, notnull, default = col_info
        if name != "kind":
            return _col_def(col_info)
        parts = [f'"{name}"']
        if typ:
            parts.append(typ)
        parts.append(f"CHECK(kind IS NULL OR kind IN ({kind_check}))")
        return " ".join(parts)

    col_defs = []
    for c in columns:
        if c[0] == "memory_kind":
            continue
        col_defs.append(_col_def_with_kind_check(c))

    ddl = "CREATE TABLE memories_new (\n    " + ",\n    ".join(col_defs) + "\n)"
    conn.execute_sql(ddl)

    # Copy all columns except memory_kind.
    cols_sql = ", ".join(f'"{c}"' for c in _KEEP_COLS)
    conn.execute_sql(
        f"INSERT INTO memories_new ({cols_sql}) SELECT {cols_sql} FROM memories"
    )

    conn.execute_sql("DROP TABLE memories")
    conn.execute_sql("ALTER TABLE memories_new RENAME TO memories")

    # Recreate useful indexes (memory_kind indexes are intentionally dropped).
    conn.execute_sql(
        "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories (project)"
    )
    conn.execute_sql(
        "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories (kind)"
    )
    conn.execute_sql(
        "CREATE INDEX IF NOT EXISTS idx_memories_cluster ON memories (cluster)"
    )
