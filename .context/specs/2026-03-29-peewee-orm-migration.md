# Peewee ORM Migration

> Replace raw sqlite3 with Peewee models. Models are the API — callers use them directly. MemoryStore goes away. Markdown files go away. DB is source of truth.

## Motivation

1. **Boilerplate** — 54 `conn.execute()` calls in storage.py, 26 `sqlite3.connect()` context managers, manual row-to-dict conversion everywhere.
2. **Type safety** — callers pass and receive `dict` for everything. No IDE completion, no contract enforcement.
3. **Leaky abstraction** — 22 raw SQL calls in feedback.py, lifecycle.py, self_reflection.py bypass storage.py entirely.
4. **Legibility** — code should read like intent, not like SQL plumbing.

## Architecture

### New files

| File | Responsibility |
|------|---------------|
| `core/models.py` | All Peewee model definitions. Schema is defined here. |
| `core/database.py` | `init_db(project_context)` — path resolution, `db.init()`, WAL mode, pragmas, migrations, vec table setup. Called once at startup. |

### Modified files

Every file that currently imports `MemoryStore` or runs raw SQL:

| File | Change |
|------|--------|
| `core/consolidator.py` | `store.get(id)` → `Memory.get_by_id(id)`, etc. |
| `core/crystallizer.py` | Same pattern. |
| `core/feedback.py` | Replace 3 raw SQL queries with Peewee queries. |
| `core/lifecycle.py` | Replace 4 raw SQL queries with Peewee queries. |
| `core/self_reflection.py` | Replace 1 raw SQL query with Peewee query. |
| `core/relevance.py` | `self.store.*` → model queries. |
| `core/threads.py` | Same. |
| `core/retrieval.py` | Same. |
| `core/manifest.py` | Same. |
| `hooks/pre_compact.py` | `MemoryStore(...)` → `init_db(...)`, model queries. |
| `hooks/consolidate_cron.py` | Same. |
| `hooks/session_start.py` | Same. |
| `hooks/user_prompt_inject.py` | Same. |
| `scripts/embed_backfill.py` | Same. |
| All test files | Replace `MemoryStore` fixtures with `init_db` + model fixtures. |

### Deleted files / code

| What | Why |
|------|-----|
| `core/storage.py` | Replaced entirely by models.py + database.py. |
| All `.md` memory files | DB is now sole source of truth. Markdown was a view. |
| `MemoryStore._format_markdown()` | No more markdown generation. |
| `MemoryStore._extract_frontmatter()` | No more markdown parsing. |
| `MemoryStore.init_dirs()` | No more stage directories. |
| Markdown stage directories | `ephemeral/`, `consolidated/`, etc. — no longer needed. |

## Models

### Memory

```python
class Memory(Model):
    id = TextField(primary_key=True, default=new_uuid)
    stage = TextField(constraints=[Check("stage IN ('ephemeral','consolidated','crystallized','instinctive')")])
    title = TextField(null=True)
    summary = TextField(null=True)
    content = TextField(default='')
    tags = TextField(default='[]')  # JSON-serialized list
    importance = FloatField(default=0.5, constraints=[Check("importance BETWEEN 0.0 AND 1.0")])
    reinforcement_count = IntegerField(default=0)
    created_at = TextField(default=now_iso)
    updated_at = TextField(default=now_iso)
    last_injected_at = TextField(null=True)
    last_used_at = TextField(null=True)
    injection_count = IntegerField(default=0)
    usage_count = IntegerField(default=0)
    project_context = TextField(null=True)
    source_session = TextField(null=True)
    content_hash = TextField(null=True)
    archived_at = TextField(null=True)
    subsumed_by = TextField(null=True)

    class Meta:
        database = db
        table_name = 'memories'
```

Key model methods:

```python
# Scopes (like ActiveRecord scopes)
@classmethod
def active(cls):
    """Non-archived memories."""
    return cls.select().where(cls.archived_at.is_null())

@classmethod
def by_stage(cls, stage, include_archived=False):
    query = cls.select().where(cls.stage == stage)
    if not include_archived:
        query = query.where(cls.archived_at.is_null())
    return query.order_by(cls.updated_at.desc())

# FTS search (raw SQL — FTS5 JOINs don't map cleanly to Peewee)
@classmethod
def search_fts(cls, query, limit=10):
    sanitized = cls.sanitize_fts_term(query)
    rows = db.execute_sql("""
        SELECT m.*, rank FROM memories_fts
        JOIN memories m ON memories_fts.rowid = m.rowid
        WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?
    """, (sanitized, limit))
    return [Memory(**row) for row in rows]

# Tags as Python list
@property
def tag_list(self):
    return json.loads(self.tags) if self.tags else []

@tag_list.setter
def tag_list(self, value):
    self.tags = json.dumps(value)

# Content hash for dedup
def compute_hash(self):
    return hashlib.md5(self.content.encode()).hexdigest()
```

**Important change:** `content` is now a column on the `memories` table, not read from a `.md` file. This is the key simplification — no more dual-write, no more file I/O, no more `file_path` column.

The `file_path` column is dropped from the schema.

### NarrativeThread

```python
class NarrativeThread(Model):
    id = TextField(primary_key=True, default=new_uuid)
    title = TextField()
    summary = TextField(null=True)
    narrative = TextField(null=True)
    created_at = TextField(default=now_iso)
    updated_at = TextField(default=now_iso)
    last_surfaced_at = TextField(null=True)

    class Meta:
        database = db
        table_name = 'narrative_threads'

    @property
    def members(self):
        return (Memory
            .select()
            .join(ThreadMember)
            .where(ThreadMember.thread_id == self.id)
            .order_by(ThreadMember.position))
```

### ThreadMember

```python
class ThreadMember(Model):
    thread_id = ForeignKeyField(NarrativeThread, backref='memberships')
    memory_id = ForeignKeyField(Memory, backref='thread_memberships')
    position = IntegerField()

    class Meta:
        database = db
        table_name = 'thread_members'
        primary_key = CompositeKey('thread_id', 'memory_id')
```

### RetrievalLog

```python
class RetrievalLog(Model):
    id = AutoField()
    timestamp = TextField(default=now_iso)
    session_id = TextField(null=True)
    memory_id = TextField(null=True)
    retrieval_type = TextField(constraints=[Check("retrieval_type IN ('injected','active_search','user_prompted')")])
    was_used = IntegerField(default=0)
    relevance_score = FloatField(null=True)
    project_context = TextField(null=True)

    class Meta:
        database = db
        table_name = 'retrieval_log'
```

### ConsolidationLog

```python
class ConsolidationLog(Model):
    id = AutoField()
    timestamp = TextField(default=now_iso)
    session_id = TextField(null=True)
    action = TextField(constraints=[Check("action IN ('kept','pruned','promoted','demoted','merged','deprecated','subsumed')")])
    memory_id = TextField(null=True)
    from_stage = TextField(null=True)
    to_stage = TextField(null=True)
    rationale = TextField(null=True)

    class Meta:
        database = db
        table_name = 'consolidation_log'
```

## FTS5 Handling

Peewee's `playhouse.sqlite_ext` has `FTS5Model`, but it's opinionated about table structure. Instead, we keep the FTS5 virtual table as raw SQL in `database.py` (created during init) and expose it through `Memory.search_fts()`.

FTS sync (insert/delete on content changes) moves into `Memory.save()` and `Memory.delete_instance()` overrides — the model owns its own index, not an external coordinator.

```python
class Memory(Model):
    def save(self, **kwargs):
        is_new = not bool(self.id and Memory.select().where(Memory.id == self.id).exists())
        if not is_new:
            self._fts_delete()
        self.updated_at = now_iso()
        self.content_hash = self.compute_hash()
        result = super().save(**kwargs)
        self._fts_insert()
        return result

    def delete_instance(self, **kwargs):
        self._fts_delete()
        return super().delete_instance(**kwargs)
```

## Vec Operations

Vec operations (`store_embedding`, `search_vector`, `get_embedding`) stay in a dedicated `core/vec.py` module. Reasons:

1. sqlite-vec requires `enable_load_extension` — a separate connection with the extension loaded.
2. The vec0 virtual table can't be represented as a Peewee model.
3. Vec availability is optional (graceful fallback when extension loading isn't available).

```python
# core/vec.py
class VecStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self.available = self._try_init()

    def store_embedding(self, memory_id, embedding): ...
    def search_vector(self, query_embedding, k=10, exclude_ids=None): ...
    def get_embedding(self, memory_id): ...
```

Callers that need vec operations use `get_vec_store()` from `database.py`:

```python
# In crystallizer.py, threads.py — _get_embeddings
from core.database import get_vec_store
raw = get_vec_store().get_embedding(memory_id)

# In relevance.py — _find_semantic_matches
results = get_vec_store().search_vector(query_embedding, k=20)
```

## database.py

```python
# core/database.py
from peewee import SqliteDatabase
from .models import db, Memory, NarrativeThread, ThreadMember, RetrievalLog, ConsolidationLog
from .vec import VecStore

_vec_store = None

def init_db(project_context=None, base_dir=None):
    """Initialize the database. Call once at startup."""
    global _vec_store
    path = _resolve_db_path(project_context, base_dir)

    db.init(str(path), pragmas={
        'journal_mode': 'wal',
        'synchronous': 'normal',
        'busy_timeout': 5000,
    })
    db.create_tables([Memory, NarrativeThread, ThreadMember, RetrievalLog, ConsolidationLog])
    _create_fts_table()
    _run_migrations()

    _vec_store = VecStore(path)
    return db

def get_vec_store():
    return _vec_store

def close_db():
    """Checkpoint WAL and close."""
    db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
```

## Migration Strategy

Existing databases have a `file_path` column and no `content` column. The migration:

1. Add `content` column to `memories` table.
2. For each row, read the markdown file at `file_path`, extract body (strip frontmatter), write to `content`.
3. Drop `file_path` column (SQLite doesn't support DROP COLUMN before 3.35 — use table rebuild if needed).
4. Delete the stage directories and markdown files.

This runs in `_run_migrations()` during `init_db()`, gated by a schema version check.

## Dependencies

Add to `requirements.txt` and `pyproject.toml`:
```
peewee>=3.17
```

Peewee is a single-file dependency (~300KB). No transitive deps.

## Testing

- Replace `MemoryStore` fixtures with `init_db(":memory:")` + model fixtures.
- Tests use Peewee's `test_database` context manager for isolation.
- Vec tests continue to skip when extension loading is unavailable.
- Helper functions like `_create_memory()` in test files become `Memory.create(...)` calls directly.

## What This Doesn't Change

- `core/embeddings.py` — unchanged, still wraps Bedrock Titan v2.
- `core/llm.py` — unchanged.
- `core/prompts.py` — unchanged.
- `scripts/reduce.py` / `scripts/consolidate.py` — these use a separate `observations.db` with their own schema. Out of scope.
- The plugin hook structure (hooks.json, hook scripts) — same shape, just different imports.
- Ephemeral session buffer files (`ephemeral/session-*.md`) — these are scratch pads for observation capture, not stored memories. They stay as plain text files. The `ephemeral/` directory survives; the other stage directories (`consolidated/`, `crystallized/`, `instinctive/`) are deleted.
