"""
Remove sqlite-vec virtual table + companion metadata table.

The new VecStore stores embeddings in the regular Peewee `memory_embeddings`
table (numpy brute-force KNN). With sqlite-vec uninstalled, virtual tables
backed by the vec0 module can no longer be DROPped via standard SQL — SQLite
needs the module loaded to call its destructor.

Workaround: enable writable_schema, delete the rows from sqlite_master, then
disable. Embedding data is not preserved — local fastembed (~15ms/doc) makes
full reindex trivial. Reindex via `scripts/reindex_embeddings.py` after this
migration runs.
"""


def up(conn):
    """Drop vec_memories + vec_embedding_meta. Safe on fresh DBs (no rows)."""
    # Try the clean path first: standard DROP. Works if vec0 is still loadable.
    for tbl in ("vec_memories", "vec_embedding_meta"):
        try:
            conn.execute_sql(f"DROP TABLE IF EXISTS {tbl}")
        except Exception:
            # vec0 module unavailable → use writable_schema fallback below.
            pass

    # Fallback: forcibly remove vec0 virtual table entries from sqlite_master.
    # Required because DROP TABLE on a vec0 virtual table needs the extension
    # loaded to run the destructor; once sqlite-vec is uninstalled this fails.
    try:
        conn.execute_sql("PRAGMA writable_schema = 1")
        conn.execute_sql(
            "DELETE FROM sqlite_master WHERE name IN ('vec_memories', 'vec_embedding_meta')"
        )
        conn.execute_sql("PRAGMA writable_schema = 0")
    except Exception:
        pass

    # Also remove the optional shadow tables that vec0 may leave behind
    # (vec_memories_chunks, vec_memories_rowids, vec_memories_vector_chunks00).
    try:
        conn.execute_sql("PRAGMA writable_schema = 1")
        conn.execute_sql(
            "DELETE FROM sqlite_master WHERE name LIKE 'vec_memories_%'"
        )
        conn.execute_sql("PRAGMA writable_schema = 0")
    except Exception:
        pass
