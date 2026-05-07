# Skill: memesis reindex --vec

Re-embeds all memories under the active embedding model and performs an atomic
swap of the vec_memories table. Use this after changing the active embedding
model or dimension in `core/embeddings.py`.

## Trigger

```
/memesis:reindex --vec
```

## What it does

1. Reads all memories from the peewee `memories` table that have content.
2. For each memory, calls `core.embeddings.embed_for_memory()` under the
   current `DEFAULT_EMBEDDING_MODEL` / `DEFAULT_DIMENSIONS` constants.
3. Writes updated embeddings to `vec_memories` via `VecStore.store_embedding()`.
4. Updates `vec_embedding_meta` rows via the same `store_embedding()` call
   (the method upserts metadata automatically).
5. Clears stale `vec_embedding_meta` rows for memories that no longer exist.
6. Updates `_system` table with the new active model constants.

## Idempotency

Reindex is safe to run multiple times. Each call overwrites existing embeddings
with embeddings from the current model. Running reindex twice with the same
active model produces the same state as running it once.

## Atomic swap

Reindex does NOT use a temporary table swap because `vec0` virtual tables
cannot be renamed. Instead it overwrites row by row via `INSERT OR REPLACE`
(the `store_embedding()` DELETE+INSERT sequence). If the process is interrupted
mid-run, the database is left with a mix of old and new embeddings. Re-running
reindex will complete the backfill.

For a true atomic swap in environments that require it, the recommended pattern
is:
1. Create a new `index_new.db` with a fresh VecStore.
2. Write all re-embeddings there.
3. `os.replace(index_new.db, index.db)` — atomic on POSIX filesystems.

## Usage context

This skill is intended to be run by the Claude Code agent when the user requests
a model upgrade or when `VecStore` logs dimension-mismatch `ValueError` warnings
indicating stale embeddings from a prior model.

## Error handling

- If `embed_for_memory()` returns `None` (Bedrock unavailable), the row is
  skipped and the existing embedding is left intact.
- Dimension mismatches from `store_embedding()` raise `ValueError` and are
  logged; the affected memory is skipped.
- Progress is logged at `INFO` level every 100 memories.

## Dependencies

- `core.embeddings`: `embed_for_memory`, `DEFAULT_EMBEDDING_MODEL`,
  `DEFAULT_DIMENSIONS`, `DEFAULT_EMBEDDING_VERSION`
- `core.vec`: `VecStore`
- `core.database`: `init_db`, `get_vec_store`
- `core.models`: `Memory` (peewee model)
