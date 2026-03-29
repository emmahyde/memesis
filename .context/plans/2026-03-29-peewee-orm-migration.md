# Peewee ORM Migration — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** Replace raw sqlite3 with Peewee models. Models are the API. Delete MemoryStore. Delete markdown files. DB is source of truth.

**This is greenfield.** No production data to migrate. No bridge pattern. Just build the new foundation and swap all callers.

**Spec:** `.context/specs/2026-03-29-peewee-orm-migration.md`

---

## Task 1: Create Peewee foundation

Create the new model layer. Don't modify existing files (except deps).

**Files:**
- Create: `core/models.py`
- Create: `core/database.py`
- Create: `core/vec.py`
- Modify: `pyproject.toml` (add `peewee>=3.17`)
- Modify: `requirements.txt` (add `peewee>=3.17`)

**core/models.py** — All model definitions:
- `db = SqliteDatabase(None)` (deferred)
- `Memory` — all fields from current schema + `content` column (replaces file reads). Scopes: `active()`, `by_stage()`. FTS: `search_fts()`, `sanitize_fts_term()`. Properties: `tag_list`. Override `save()` for FTS sync + auto timestamps + content hash. Override `delete_instance()` for FTS cleanup.
- `NarrativeThread` — with `members` and `member_ids` properties
- `ThreadMember` — composite PK (thread_id, memory_id)
- `RetrievalLog`, `ConsolidationLog` — simple log tables

**core/database.py** — Init/teardown:
- `init_db(project_context=None, base_dir=None)` — resolve path, db.init with WAL pragmas, create_tables, create FTS virtual table, init VecStore
- `get_vec_store()`, `get_db_path()`, `get_base_dir()`
- `close_db()` — WAL checkpoint + close

**core/vec.py** — Extract from storage.py:
- `VecStore` class with `store_embedding`, `search_vector`, `get_embedding`
- Same graceful fallback pattern (`available` flag)

Commit: `feat: create Peewee model layer (models.py, database.py, vec.py)`

---

## Task 2: Migrate all callers + delete storage.py

Replace every `MemoryStore` import and `store.method()` call with model queries. Delete storage.py.

**Files to modify (all callers):**
- `core/consolidator.py` — remove store param, use Memory/ConsolidationLog directly
- `core/crystallizer.py` — same
- `core/feedback.py` — replace 3 raw SQL blocks with Peewee queries
- `core/lifecycle.py` — replace 4 raw SQL blocks with Peewee queries
- `core/self_reflection.py` — replace 1 raw SQL block
- `core/relevance.py` — model queries + get_vec_store()
- `core/threads.py` — model queries + get_vec_store()
- `core/retrieval.py` — model queries
- `core/manifest.py` — model queries
- `hooks/pre_compact.py` — init_db() instead of MemoryStore()
- `hooks/consolidate_cron.py` — same
- `hooks/session_start.py` — same
- `hooks/user_prompt_inject.py` — replace raw SQL + init_db()
- `scripts/embed_backfill.py` — same

**Delete:** `core/storage.py`

Translation cheat sheet:
| Old | New |
|-----|-----|
| `store = MemoryStore(project_context=...)` | `init_db(project_context=...)` |
| `store.get(id)` | `Memory.get_by_id(id)` |
| `store.list_by_stage("consolidated")` | `list(Memory.by_stage("consolidated"))` |
| `store.create(path, content, metadata)` | `Memory.create(stage=..., title=..., content=..., ...)` |
| `store.update(id, content, metadata)` | `mem = Memory.get_by_id(id); mem.content = ...; mem.save()` |
| `store.delete(id)` | `Memory.get_by_id(id).delete_instance()` |
| `store.search_fts(q)` | `Memory.search_fts(q)` |
| `store.archive(id)` | `Memory.update(archived_at=now_iso()).where(Memory.id == id).execute()` |
| `store.log_consolidation(...)` | `ConsolidationLog.create(...)` |
| `store.record_injection(...)` | `RetrievalLog.create(...); Memory.update(...)` |
| `store.create_thread(...)` | `NarrativeThread.create(...) + ThreadMember.create(...)` |
| `store.get_embedding(id)` | `get_vec_store().get_embedding(id)` |
| `store.store_embedding(id, emb)` | `get_vec_store().store_embedding(id, emb)` |
| `store.close()` | `close_db()` |
| `store.base_dir` | `get_base_dir()` |
| `store.db_path` | `get_db_path()` |
| Raw `sqlite3.connect(store.db_path)` | Peewee query via models |

**Note:** Core module constructors lose the `store` parameter. `Consolidator(store, lifecycle)` → `Consolidator(lifecycle)`. Hooks that construct these objects must be updated accordingly.

**Note:** Current callers expect dicts from get()/list_by_stage(). After migration, they get model instances. Access changes from `mem["title"]` to `mem.title` and `mem["tags"]` (pre-parsed list) to `mem.tag_list`. Update ALL access patterns.

Commit: `refactor: migrate all callers to Peewee models, delete MemoryStore`

---

## Task 3: Migrate tests

Update all test files. Replace MemoryStore fixtures with init_db + model operations.

**Files:**
- Rename: `tests/test_storage.py` → `tests/test_models.py`
- Modify: all other test files
- Create or update: `tests/conftest.py` (shared fixtures)

Key changes:
- `MemoryStore(base_dir=...)` → `init_db(base_dir=...)`
- `store.create(path, content, metadata)` → `Memory.create(...)`
- `result["title"]` → `result.title`
- `result["tags"]` → `result.tag_list`
- `sqlite3.connect(store.db_path)` → `db.execute_sql(...)` or model queries
- `apsw.Connection(str(store.db_path))` → `db.execute_sql(...)` or model queries
- `Consolidator(store, lifecycle)` → `Consolidator(lifecycle)`
- `store.close()` → `close_db()`

Shared fixture in conftest.py:
```python
@pytest.fixture
def test_db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield db
    close_db()
```

All 497+ tests must pass.

Commit: `test: migrate all tests to Peewee models`

---

## Verification

1. `pytest tests/ -q` — all pass
2. `grep -r "MemoryStore\|from core.storage" core/ hooks/ scripts/ tests/` — no references
3. `ls core/storage.py` — should not exist
4. Code reads like intent: `Memory.by_stage("consolidated")`, `Memory.search_fts("query")`
