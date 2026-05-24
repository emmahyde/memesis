"""
Migration 0020 — FTS5 sync triggers (defense-in-depth).

Creates three SQL triggers on the `memories` table so that any direct
SQLite write — whether through Peewee, a raw sqlite3.connect(), or any
future bypass path — automatically keeps `memories_fts` in sync.

Prior to this migration, FTS sync was Python-only (Memory.save() /
Memory.delete_instance() overrides in core/models.py). Any code that
opened a raw sqlite3 connection and wrote to `memories` silently left
the FTS index stale, as happened in scripts/consolidate_project_dbs.py
and scripts/backfill_enrichment_fields.py after the global-DB cutover
(commit 3079d2a, May 17 corruption event).

After this migration:
- Triggers handle every write path uniformly.
- The Python-level _fts_insert / _fts_delete_from_db methods in
  core/models.py are REMOVED (see companion refactor commit) to avoid
  double-writes.
- hard_delete() in Memory retains its explicit FTS delete call because
  it uses DELETE FROM memories directly (not routed through the ORM),
  but with the BEFORE DELETE trigger in place the explicit call becomes
  redundant; it is left in for one release cycle and will be removed
  once trigger coverage is confirmed in production.

Trigger semantics (external-content FTS5, content='memories'):
  memories_ai: AFTER INSERT  — insert new FTS row
  memories_ad: BEFORE DELETE — delete old FTS row (must be BEFORE so
               old.* values are still accessible)
  memories_au: AFTER UPDATE  — delete old FTS row then insert new one
"""


def up(conn) -> None:
    """Create FTS5 sync triggers. Safe to re-run (CREATE TRIGGER IF NOT EXISTS)."""

    conn.execute_sql("""
        CREATE TRIGGER IF NOT EXISTS memories_ai
        AFTER INSERT ON memories
        BEGIN
            INSERT INTO memories_fts(rowid, title, summary, tags, content)
            VALUES (
                new.rowid,
                COALESCE(new.title, ''),
                COALESCE(new.summary, ''),
                COALESCE(new.tags, ''),
                COALESCE(new.content, '')
            );
        END
    """)

    conn.execute_sql("""
        CREATE TRIGGER IF NOT EXISTS memories_ad
        BEFORE DELETE ON memories
        BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, summary, tags, content)
            VALUES (
                'delete',
                old.rowid,
                COALESCE(old.title, ''),
                COALESCE(old.summary, ''),
                COALESCE(old.tags, ''),
                COALESCE(old.content, '')
            );
        END
    """)

    conn.execute_sql("""
        CREATE TRIGGER IF NOT EXISTS memories_au
        AFTER UPDATE ON memories
        BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, summary, tags, content)
            VALUES (
                'delete',
                old.rowid,
                COALESCE(old.title, ''),
                COALESCE(old.summary, ''),
                COALESCE(old.tags, ''),
                COALESCE(old.content, '')
            );
            INSERT INTO memories_fts(rowid, title, summary, tags, content)
            VALUES (
                new.rowid,
                COALESCE(new.title, ''),
                COALESCE(new.summary, ''),
                COALESCE(new.tags, ''),
                COALESCE(new.content, '')
            );
        END
    """)
