# RFC: APSW Removal from Memesis

**Status:** Draft
**Date:** 2026-05-13
**Author:** Architecture review pass
**Decision required from:** Emma Hyde

---

## 1. Problem Statement

### 1.1 Why apsw was introduced

`apsw` first entered the codebase to handle `sqlite-vec` extension loading because Apple's bundled stdlib `sqlite3` ships without `enable_load_extension` support. The doc comment in `core/vec.py:5-6` is explicit: "the stdlib sqlite3 module on macOS ships without load_extension support."

That justification is no longer valid. The project now runs on Python 3.12 via mise, which compiles `sqlite3` with `SQLITE_ENABLE_LOAD_EXTENSION`. Verified empirically:

```
$ .venv/bin/python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); print('OK')"
OK
```

End-to-end test — stdlib sqlite3 creating and querying a `vec0` virtual table via `sqlite_vec.load()` — passes cleanly.

### 1.2 The dual-connection WAL risk

There are **three** connection paths touching vec data in the same `index.db`:

1. **apsw connections** — `VecStore._connect()` (`core/vec.py:90-94`) and `SessionVecStore._connect()` (`core/session_vec.py:91-95`).
2. **Peewee / stdlib sqlite3 via VecSqliteDatabase** — `core/models.py:36-49`. Already calls `conn.enable_load_extension(True)` + `_sqlite_vec.load(conn)` on every connection.
3. **Direct Peewee SQL** — `Memory.hard_delete()` (`core/models.py:243`): `db.execute_sql("DELETE FROM vec_memories WHERE memory_id = ?", ...)`.

CLAUDE.md's warning ("Never use raw `sqlite3.connect()` on the plugin's `index.db` — it is managed by `apsw` in WAL mode") is itself acknowledgment of fragility, but the existing code already violates it: `VecSqliteDatabase` opens stdlib sqlite3 connections on every Peewee operation.

Practical risks from mixed apsw + stdlib sqlite3 on the same WAL:

- **WAL version mismatch**: apsw bundles its own SQLite (≥3.46), differing from system SQLite. Different versions writing WAL pages may cause readers to misparse the WAL header.
- **Busy timeout asymmetry**: apsw uses `setbusytimeout(5000)`; Peewee uses `pragmas={"busy_timeout": 5000}`. Different lock-handling behavior under concurrent writes.
- **Transaction semantics differ**: apsw autocommits by default; Peewee uses `sqlite3`'s `isolation_level`. Concurrent operations through the two drivers may see different snapshots within what appears to be one logical transaction.

**The dual-connection problem is the actual bug. apsw removal is the mechanism, not the goal.**

---

## 2. Goals and Non-Goals

### Goals

- Remove `apsw` from `pyproject.toml` and from all `import` statements.
- Eliminate dual-driver: after removal, exactly one SQLite library talks to `index.db`.
- Ship as a single PR (per CLAUDE.md: "Cross-subsystem refactors ship as one PR").
- Preserve current vector retrieval: KNN search, embedding storage, session-scoped dedup via `SessionVecStore`, embedding metadata tracking.
- Rewrite `tests/test_vec.py` to work without apsw.
- Update CLAUDE.md to remove inaccurate apsw WAL warning.
- Fix the "stale embeddings after content update" bug at the same time (touches all vec plumbing anyway).

### Non-Goals

- Changing Bedrock embedding API or `core/embeddings.py`.
- Changing FTS5 or hybrid RRF retrieval logic in `core/retrieval.py`.
- Optimizing for 100K+ memories (target stays 10K).
- Introducing HNSW or ANN indexes (out of scope per PROJECT.md).
- Migrating Peewee models to a different ORM.

---

## 3. Options

### Option A: Route VecStore through Peewee connection (smallest diff)

**Insight**: `VecSqliteDatabase` in `core/models.py` already loads `sqlite_vec` on every Peewee connection. `VecStore._connect()` is the only remaining reason for apsw. Stop opening apsw connections; route all vec operations through the existing Peewee connection.

**Code shape**:

```python
# Before (core/vec.py)
def _connect(self):
    conn = apsw.Connection(self._db_path)
    conn.setbusytimeout(5000)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    return conn

# After (Option A)
from .models import db

def _get_conn(self):
    return db.connection()  # already has sqlite_vec loaded
```

`__init__` DDL becomes `db.execute_sql(...)`. `finally: conn.close()` blocks removed (Peewee owns lifecycle).

**Dependency delta**: apsw removed. sqlite_vec stays.

**Tests**: 11 `apsw.Connection` uses in `tests/test_vec.py` replaced with stdlib `sqlite3.connect()` (with `enable_load_extension(True)` + `sqlite_vec.load()`) or `db.execute_sql()`.

**Risks**:
- `db.connection()` returns the active connection — may be inside a Peewee `db.atomic()` block. Vec writes participate in the same transaction (actually desirable for atomicity).
- `SessionVecStore.drop()` runs DROP TABLE inside whatever Peewee transaction is open. Need to verify safe for `REFRAME_A_ENABLED` path.
- `VecSqliteDatabase` subclass remains as permanent tech debt.

**Verdict**: Smallest diff. Eliminates apsw. Preserves sqlite-vec. Resolves dual-driver WAL risk. No data migration. Session vec table pattern is the main risk to validate.

---

### Option A2: pysqlite3-binary swap

Swap apsw for `pysqlite3-binary` (vendors SQLite with `enable_load_extension` enabled).

**Recommendation: Skip.** This option was relevant when stdlib lacked `enable_load_extension`. That concern is obsolete here. Adding `pysqlite3-binary` introduces a third SQLite version (after apsw's bundled SQLite and Python 3.12 stdlib SQLite) and solves a problem that doesn't exist on this platform.

---

### Option B: Drop sqlite-vec, numpy KNN (RECOMMENDED)

**Insight**: Remove both `apsw` AND `sqlite-vec`. Store embeddings as BLOBs in a Peewee table. KNN in Python with numpy.

**Performance budget**:

| Metric | Value |
|---|---|
| Current memory count | ~1,000 |
| Design target | 10,000 |
| Embedding dim | 512 float32 = 2,048 bytes |
| Full matrix at 10K | ~20 MB |
| numpy brute-force KNN at 10K | ~5ms |
| Bedrock API embed latency | ~200ms |
| UserPromptSubmit budget | 500ms |

Total hybrid retrieval: 200ms (embed) + 5ms (KNN) + 1ms (FTS) ≈ 220ms. Well under budget. sqlite-vec's vec0 KNN advantage is real but irrelevant at this scale.

**Schema change**: Replace `vec_memories` virtual table + `vec_embedding_meta` companion with one regular table:

```python
class MemoryEmbedding(BaseModel):
    memory_id = TextField(primary_key=True)
    embedding = BlobField()
    embedding_model = TextField(default='')
    embedding_version = TextField(default='')
    embedding_dim = IntegerField(default=0)
    updated_at = DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = "memory_embeddings"
```

**`VecStore` becomes numpy-backed** — no extension loading:

```python
class VecStore:
    def __init__(self, db_path: Path):
        from .embeddings import DEFAULT_DIMENSIONS, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_VERSION
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._embedding_version = DEFAULT_EMBEDDING_VERSION
        self._embedding_dim = DEFAULT_DIMENSIONS
        self._available = True
        try:
            self._sync_system_table()
            self._backfill_metadata()
        except Exception as e:
            logger.warning("VecStore init: %s", e)

    def store_embedding(self, memory_id: str, embedding: bytes) -> None:
        expected = self._embedding_dim * 4
        if len(embedding) != expected:
            raise ValueError(f"dim mismatch: expected {expected} bytes, got {len(embedding)}")
        with db.atomic():
            MemoryEmbedding.insert(
                memory_id=memory_id, embedding=embedding,
                embedding_model=self._embedding_model,
                embedding_version=self._embedding_version,
                embedding_dim=self._embedding_dim,
            ).on_conflict(
                conflict_target=[MemoryEmbedding.memory_id],
                update={
                    MemoryEmbedding.embedding: embedding,
                    MemoryEmbedding.embedding_model: self._embedding_model,
                    MemoryEmbedding.embedding_version: self._embedding_version,
                    MemoryEmbedding.embedding_dim: self._embedding_dim,
                },
            ).execute()

    def search_vector(self, query_embedding: bytes, k: int = 10, exclude_ids: set = None) -> list[dict]:
        if query_embedding is None:
            return []
        rows = list(
            MemoryEmbedding.select(MemoryEmbedding.memory_id, MemoryEmbedding.embedding)
            .where(MemoryEmbedding.embedding_dim == self._embedding_dim)
        )
        if not rows:
            return []
        ids = [r.memory_id for r in rows]
        matrix = np.frombuffer(
            b"".join(bytes(r.embedding) for r in rows), dtype=np.float32,
        ).reshape(len(ids), self._embedding_dim)
        query = np.frombuffer(query_embedding, dtype=np.float32)
        # Bedrock Titan returns unit-normalized vectors → cos_sim = dot product
        sims = matrix @ query
        dists = 1.0 - sims
        order = np.argsort(dists)
        results = []
        for idx in order:
            mid = ids[idx]
            if exclude_ids and mid in exclude_ids:
                continue
            results.append({"memory_id": mid, "distance": float(dists[idx])})
            if len(results) >= k:
                break
        return results
```

**`SessionVecStore` becomes in-memory**:

```python
class SessionVecStore:
    def __init__(self, db_path: Path, session_id: str):
        self._embeddings: dict[int, bytes] = {}
        self._available = True

    def add(self, obs_idx: int, embedding: bytes) -> bool:
        if embedding is None or len(embedding) != _EMBEDDING_DIM * 4:
            return False
        self._embeddings[obs_idx] = embedding
        return True

    def query_similar(self, query_embedding: bytes, k: int = 3) -> list[int]:
        if not self._embeddings or query_embedding is None:
            return []
        ids = list(self._embeddings.keys())
        matrix = np.frombuffer(
            b"".join(self._embeddings[i] for i in ids), dtype=np.float32,
        ).reshape(len(ids), _EMBEDDING_DIM)
        sims = matrix @ np.frombuffer(query_embedding, dtype=np.float32)
        return [ids[i] for i in np.argsort(-sims)[:k]]

    def drop(self) -> None:
        self._embeddings.clear()
        self._available = False
```

`db_path` ignored. Constructor signature stays compatible. No DB tables, no extension loading, no DROP TABLE cleanup. State is GC'd when extraction call ends.

**Dependency delta**:

```toml
# Remove:
"sqlite-vec>=0.1.6",
"apsw>=3.46",

# Add (already transitive via scikit-learn; make explicit):
"numpy>=1.24",
```

**`models.py` change**: Remove `VecSqliteDatabase` entirely. Replace with `SqliteDatabase`. In `Memory.hard_delete()`, swap raw SQL for `MemoryEmbedding.delete().where(...).execute()`.

**Data migration** (`core/migrations/sql/YYYYMMDD_NNNN_remove_vec_virtual_table.py`): copy from `vec_memories` (virtual table, requires sqlite_vec loaded) + `vec_embedding_meta` into `memory_embeddings`, then `DROP TABLE vec_memories` and `DROP TABLE vec_embedding_meta`. Must run while `sqlite_vec` is still importable.

---

### Option C: External vector library (usearch / faiss-cpu / chromadb)

Out of scope. At 10K × 512, numpy brute force is ~5ms with zero new deps. Revisit if scale grows to 100K+.

---

## 4. Recommendation: Option B

### Rationale

**Dependency surface**: Removes two C extension deps. Each is a platform-specific wheel that breaks on Python minor bumps, macOS updates, ARM transitions. numpy is already implicitly present.

**Latency**: 5ms KNN at 10K × 512. 200ms Bedrock dominates. KNN is not the bottleneck.

**Single driver**: After Option B, exactly one connection pattern touches `index.db`: Peewee `SqliteDatabase` with WAL pragmas. No extension loading. No `VecSqliteDatabase` subclass. CLAUDE.md's warning becomes trivially true.

**SessionVecStore simplification**: In-memory variant removes per-session `vec_session_{slug}` virtual tables. No DROP TABLE races with concurrent operations.

**Why not A**: Lower diff but leaves sqlite-vec in place. sqlite-vec is the reason apsw existed; keeping it means any future Python/platform that disables `enable_load_extension` reintroduces the problem. Leaves `VecSqliteDatabase` as permanent tech debt.

**Why not A2**: pysqlite3-binary solves a problem that doesn't exist on this platform.

---

## 5. Migration Plan (Option B, single PR)

1. **Write migration**: `core/migrations/sql/YYYYMMDD_NNNN_remove_vec_virtual_table.py` — creates `memory_embeddings`, copies from `vec_memories` + `vec_embedding_meta`, drops old tables. Must run while sqlite_vec still importable.
2. **Add `MemoryEmbedding`** to `core/models.py` and `ALL_TABLES` in `core/database.py`.
3. **Rewrite `core/vec.py`** with numpy-backed `VecStore` (Section 3 Option B).
4. **Rewrite `core/session_vec.py`** with in-memory variant.
5. **Update `core/models.py`**: remove `VecSqliteDatabase`, replace with `SqliteDatabase`. Update `Memory.hard_delete()` to use `MemoryEmbedding.delete()`.
6. **Update `pyproject.toml`**: remove apsw + sqlite-vec; add numpy explicitly.
7. **Rewrite `tests/test_vec.py`**: replace 11 `apsw.Connection` uses with stdlib `sqlite3` or Peewee. Add KNN result tests (none currently exist).
8. **Update `scripts/db_check.py` and `scripts/install-deps.sh`**: remove apsw comments.
9. **Update `CLAUDE.md`** Rule 1:

   ```markdown
   CRITICAL: All DB access must go through `init_db()`, Peewee models, or `db.execute_sql()`.
   Do not open separate `sqlite3.connect()` connections to `index.db` — the Peewee connection
   manages WAL mode and busy_timeout; bypassing it creates concurrent-writer races.
   ```

10. **Fix stale embeddings bug** (CONCERNS.md item) in `Memory.save()` — call `store_embedding()` when `content_hash` changes. Same code path, ship together.
11. **Bump `SEED_THRESHOLD`** in `core/migrations/__init__.py` to 3.

---

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | Migration on DB without `vec_memories` (fresh install) | Migration's try/except handles; `memory_embeddings` created regardless |
| 2 | `vec_embedding_meta` rows orphaned (no matching embedding) | Skip stubs; metadata-only entries can't be retrieved by KNN anyway |
| 3 | `_system` table sync | Regular table, survives migration unchanged |
| 4 | numpy missing from venv | Wrap import in `_NUMPY_AVAILABLE` flag; degrade to FTS-only |
| 5 | SessionVecStore in-memory not cross-process | Document as tradeoff; current REFRAME_A use is single-process |
| 6 | float32 BLOB round-trip via Peewee | `bytes(row.embedding)` ensures clean bytes; add round-trip test |
| 7 | `Memory.hard_delete` three-way atomicity | All three deletes in same `db.atomic()` — improves on current (apsw escapes Peewee transaction) |
| 8 | Plugin cache staleness | Pre-existing tech debt; reinstall after PR |

---

## 7. Open Questions

**Q1: Migration vs. full reindex**

The migration must run while `sqlite_vec` is importable to SELECT from `vec_memories`. Alternative: skip data copy, recompute all embeddings from memory text (~1000 × 200ms = 200s one-time). Acceptable?

**Q2: numpy as explicit dep**

Currently transitive via scikit-learn. Make explicit (`numpy>=1.24`)?

**Q3: Drop `vec_embedding_meta` in migration?**

Recommend yes — dead tables in production schemas accumulate confusion.

**Q4: `VecStore.available` semantics**

After Option B, always `True`. Keep as property (preserves call sites in retrieval/reconsolidation/graph) or remove?

**Q5: Session-vec crash-recovery**

Current apsw implementation persists session embeddings to `index.db`; in-memory variant loses them on restart. Acceptable to lose session dedup state (some observations may be re-extracted)?

---

## 8. Affected Files

| File | Change |
|---|---|
| `core/vec.py` | Full rewrite: numpy KNN |
| `core/session_vec.py` | Full rewrite: in-memory |
| `core/models.py` | Remove VecSqliteDatabase, add MemoryEmbedding, update hard_delete |
| `core/database.py` | Add MemoryEmbedding to ALL_TABLES |
| `tests/test_vec.py` | Rewrite: remove 11 apsw uses, add KNN tests |
| `pyproject.toml` | -apsw, -sqlite-vec, +numpy explicit |
| `CLAUDE.md` | Update Rule 1 WAL warning |
| `scripts/db_check.py` | Remove apsw comment |
| `scripts/install-deps.sh` | Remove apsw comment |
| `core/migrations/sql/*_remove_vec_virtual_table.py` | New migration |
| `core/migrations/__init__.py` | Bump SEED_THRESHOLD |
