# Research: Python 3.10+ Ecosystem Pitfalls

**Mode:** ecosystem
**Confidence:** HIGH (peewee, sqlite-vec, Anthropic SDK, WAL — Context7 + official docs); MEDIUM (NLTK, apsw, scikit-learn — official doc access partially blocked by Cloudflare; characterizations based on known behavior and codebase review)

**Sources:**
- Context7 /anthropics/anthropic-sdk-python (reputation: High) — fetched 2026-03-30
- Context7 /coleifer/peewee (reputation: High) — fetched 2026-03-30
- Context7 /asg017/sqlite-vec (reputation: High) — fetched 2026-03-30
- SQLite WAL official documentation — https://www.sqlite.org/wal.html (fetched 2026-03-30)
- Codebase audit: core/database.py, core/models.py, core/vec.py, core/embeddings.py,
  core/retrieval.py, core/relevance.py, core/feedback.py, hooks/pre_compact.py, scripts/reduce.py

---

## Current Landscape

This project combines six layers that each have distinct failure modes:

| Layer | Technology | Role in codebase |
|-------|-----------|-----------------|
| LLM calls | Anthropic SDK (sync) | Consolidation, crystallization, reconsolidation |
| Embeddings | AWS Bedrock Titan v2 (boto3) | Float32 vectors for KNN |
| ORM | Peewee + deferred SqliteDatabase | All relational reads/writes |
| Vector store | sqlite-vec via apsw | KNN over memory embeddings |
| FTS | SQLite FTS5 (raw SQL via Peewee) | Text leg of hybrid RRF |
| Text processing | NLTK (stopwords, PorterStemmer) | Relevance scoring, feedback loop |
| Dedup/clustering | scikit-learn TF-IDF + cosine | scripts/reduce.py, scripts/consolidate.py |

The hook runtime (`pre_compact.py`) is single-process and sequential, which is the primary concurrency mitigation. The dual-connection architecture (peewee + apsw to the same `.db` file) is the primary structural risk.

---

## 1. SQLite Concurrency and WAL Mode

### WAL-reset race condition — CVE-level severity

SQLite versions 3.7.0–3.51.2 contain a data race in WAL mode: when two connections checkpoint concurrently while a third commits, the WAL-index header can be left in an incorrect state, causing a later checkpoint to silently skip committed transactions. The result is **silent database corruption with no error raised**. This was fixed in SQLite 3.51.3 (released 2026-03-13).

macOS ships its own system SQLite (typically 3.43.x on Sequoia), which apsw and the stdlib sqlite3 module both link against unless overridden. Both connections in this codebase (peewee and apsw/VecStore) are in the vulnerable range on a default macOS install.

*Check at startup:*
```python
import sqlite3, apsw
print(sqlite3.sqlite_version)      # peewee/FTS5 connection
print(apsw.sqlitelibversion())      # vec_memories connection
```

On macOS, upgrade to a Homebrew-linked SQLite >= 3.51.3 and set `DYLD_LIBRARY_PATH` or use `pysqlite3-binary`. The concurrent-write risk is low for the current sequential hook design, but any future cron overlap makes it real.

### Dual-connection architecture — split-brain risk

`core/vec.py` opens a new apsw `Connection` for every `store_embedding`, `search_vector`, and `get_embedding` call. `core/database.py` holds a persistent peewee connection. Both write to the same WAL file. This is architecturally sound but creates two concrete risks:

**1. apsw connections have no busy timeout.** The `VecStore._connect()` method opens `apsw.Connection(self._db_path)` with no `setbusytimeout()` call. If the peewee connection holds a write lock (e.g., during the `db.atomic()` in `Memory.save()`), any concurrent `store_embedding` call will raise `apsw.BusyError` immediately instead of waiting. The peewee connection has `busy_timeout: 5000` set via pragmas; apsw does not.

*Fix:*
```python
def _connect(self):
    conn = apsw.Connection(self._db_path)
    conn.setbusytimeout(5000)   # match peewee's 5-second timeout
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    return conn
```

**2. Extension loaded on every connection open.** `sqlite_vec.loadable_path()` resolves the `.dylib`/`.so` path and `load_extension` initializes it on every call. In the pre-compact loop that embeds all newly-kept memories (`for memory_id in result.get("kept", []):`) this multiplies load overhead. A single apsw connection held for the duration of a batch write loop is faster:

```python
conn = self._connect()
try:
    for memory_id, embedding in batch:
        conn.execute("INSERT OR REPLACE ...", (memory_id, embedding))
finally:
    conn.close()
```

### WAL checkpoint starvation and silent failure

`close_db()` calls `PRAGMA wal_checkpoint(TRUNCATE)` but does not inspect the result. `TRUNCATE` returns `(busy, log, checkpointed)` — if `busy > 0`, the checkpoint was incomplete (a reader had an open transaction). Under continuous use, this causes unbounded WAL growth and degraded read performance.

*Fix — log the result:*
```python
cursor = db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE)")
result = cursor.fetchone()
if result and result[0] > 0:  # busy > 0
    logger.warning("WAL checkpoint incomplete: %s busy frames", result[1])
```

### `synchronous=NORMAL` is correct for WAL mode

The codebase uses `synchronous: normal` which is the right setting for WAL. Do not change to `synchronous=OFF` — that sacrifices crash durability entirely. `FULL` is not needed with WAL.

### FTS5 content-table desync on process kill

`memories_fts` is a content FTS5 table backed by `memories` with manual sync (no triggers). The sequence in `Memory.save()` is: SELECT rowid → DELETE fts row → INSERT memories row → INSERT fts row, wrapped in `db.atomic()`. The atomic wrapper protects against normal exceptions, but a process kill (SIGKILL) or OOM between the DELETE and INSERT steps leaves the FTS index with a deleted-but-not-re-inserted row.

Recovery: `INSERT INTO memories_fts(memories_fts) VALUES('rebuild')` — expensive but correct. This should be part of a startup integrity check, not a routine operation.

**FTS bulk insert bypass.** If any code path uses peewee's `Model.insert_many()` or `bulk_create()`, the `Memory.save()` override is bypassed and FTS is not updated. Search results will silently miss those records.

---

## 2. Peewee ORM

### Deferred database — initialization ordering traps

`db = SqliteDatabase(None)` is module-level in `models.py`. Any import that eagerly queries a model (e.g., a module-level `Memory.select()`) before `init_db()` is called raises `OperationalError: database path is not set` or `no such table`. This is safe in the current codebase but easy to break when adding new entry points.

### Thread-local connections vs. asyncio

Peewee's `SqliteDatabase` stores connection state in thread-local storage. This is safe across OS threads but incompatible with asyncio: if any async coroutine awaits across a peewee call, the wrong thread's connection state is used. The codebase is currently synchronous. If streaming Anthropic responses via `AsyncAnthropic` are ever added, peewee calls must be wrapped with `asyncio.to_thread()` or replaced with an async ORM.

### N+1 in `_pk_exists()`

`Memory.save()` calls `_pk_exists()` which issues `Memory.get_by_id(self.id)` before every save. This is one extra SELECT per save to detect insert vs. update. In the consolidation loop that creates many memories, this doubles query count. Peewee's `on_conflict` or `force_insert` parameter can eliminate this check.

### Naive vs. UTC datetime mixing

All timestamp columns are `TextField(null=True)` containing ISO strings. Lexicographic ordering works only if format is consistent. The codebase mixes:
- `datetime.now().isoformat()` — naive, no timezone (e.g., `"2026-03-30T14:00:00.123456"`)
- `datetime.now(timezone.utc).isoformat()` — UTC-aware (e.g., `"2026-03-30T14:00:00.123456+00:00"`)

`retrieval.py` uses UTC-aware in `_get_thread_narratives` (line ~384) but naive datetimes in `_record_injection`. Mixed formats break ordering comparisons and `datetime.fromisoformat()` parsing in the provenance batch logic.

*Fix:* Pick `datetime.now(timezone.utc).isoformat()` everywhere. Add a one-time migration to normalize existing rows.

### `consolidation_log` migration is not atomic

The migration in `_run_migrations()` that rebuilds `consolidation_log` to add the `'subsumed'` CHECK constraint does: `DROP TABLE` → `CREATE TABLE` → Python loop of `INSERT`. If the process is killed between `DROP` and the loop completing, the log is permanently lost. Wrap in `db.atomic()`:

```python
with db.atomic():
    db.execute_sql("DROP TABLE consolidation_log")
    db.execute_sql("CREATE TABLE consolidation_log (...)")
    for r in rows:
        db.execute_sql("INSERT INTO consolidation_log ...", list(r))
```

### Stale `_vec_store` singleton after `close_db()`

`close_db()` closes the peewee connection but does not reset `_vec_store = None`. A subsequent `init_db()` with a different `db_path` creates a new `VecStore` and overwrites `_vec_store`, but any caller that cached `get_vec_store()` at module import time holds the stale reference. Always call `get_vec_store()` at use time, not once at startup.

### `ThreadSafeDatabaseMetadata` not used

The base `BaseModel.Meta` does not use `ThreadSafeDatabaseMetadata` from `playhouse.shortcuts`. This is fine for the current single-process design, but if `db.bind()` is ever called at runtime (e.g., to switch to a test database), the swap is not thread-safe. For future safety:

```python
from playhouse.shortcuts import ThreadSafeDatabaseMetadata

class BaseModel(Model):
    class Meta:
        database = db
        model_metadata_class = ThreadSafeDatabaseMetadata
```

---

## 3. sqlite-vec via apsw

### Dimension mismatch — silent wrong answers

The vec0 table is declared `float[512]` but the INSERT accepts any blob length. If `DEFAULT_DIMENSIONS` changes (e.g., from 512 to 1024) or if a differently-dimensioned embedding is inserted, sqlite-vec returns garbage distances — not an exception. There is no runtime enforcement.

*Fix — assert before every INSERT and query:*
```python
EXPECTED_BYTES = DEFAULT_DIMENSIONS * 4   # 512 * 4 = 2048

def store_embedding(self, memory_id: str, embedding: bytes) -> None:
    assert len(embedding) == EXPECTED_BYTES, (
        f"Embedding size mismatch: expected {EXPECTED_BYTES} bytes, got {len(embedding)}"
    )
    ...
```

### Metadata column constraints (for future schema additions)

If metadata columns are ever added to `vec_memories`:
- Maximum 16 metadata columns per vec0 table
- Only TEXT, INTEGER, FLOAT, BOOLEAN types supported
- UNIQUE and NOT NULL constraints are not supported
- KNN WHERE filters accept only `=`, `!=`, `>`, `>=`, `<`, `<=` — no `LIKE`, `IN`, or scalar functions

### apsw `enable_load_extension` availability

On some Linux distributions and CI environments, apsw is compiled without `enable_load_extension` support. If present but not compiled in, `conn.enable_load_extension(True)` raises `AttributeError` (not `ImportError`). The current `try/except ImportError` in `__init__` does not catch this. Wrap `_connect()` in a broader except:

```python
try:
    conn = self._connect()
    ...
    self._available = True
except (Exception,) as e:
    logger.warning("sqlite-vec unavailable: %s", e)
```

### `INSERT OR REPLACE` on TEXT PRIMARY KEY behavior

The `memory_id TEXT PRIMARY KEY` in vec0 uses user-defined rowids. `INSERT OR REPLACE` works correctly for upserts (deletes the old row, inserts the new). This is the right pattern for `store_embedding`. Do not use `INSERT OR IGNORE` — it would silently skip re-embedding on content updates.

---

## 4. Anthropic Python SDK

### Stale embeddings after memory content update

`Memory.save()` updates `content_hash` when content changes, but does not trigger re-embedding. If a memory's title, summary, or content changes during reconsolidation (e.g., `refined` action), the vector in `vec_memories` is stale. The next KNN query ranks that memory by its old embedding position. This is a correctness bug, not just a performance issue — the hybrid RRF result for semantically-changed memories will be wrong.

*Fix:* In `Memory.save()`, detect content_hash change and enqueue re-embedding:
```python
old_hash = self.content_hash
result = super().save(...)
if self.content_hash != old_hash:
    from .database import get_vec_store
    vs = get_vec_store()
    if vs:
        from .embeddings import embed_for_memory
        embedding = embed_for_memory(self.title or "", self.summary or "", self.content or "")
        if embedding:
            vs.store_embedding(self.id, embedding)
```

### `max_tokens` is required — no SDK default

The Anthropic API requires `max_tokens` on every request. The SDK does not supply a default. Any call site that constructs a request dynamically and omits `max_tokens` raises `BadRequestError` at runtime, not at import time.

### Model string staleness and centralization

Model IDs like `"claude-3-5-sonnet-latest"` resolve to whatever Anthropic designates as latest at call time. Pinned dated IDs (e.g., `"claude-3-5-sonnet-20241022"`) are reproducible but will eventually 404 when deprecated. The codebase should define a single module-level constant:

```python
# core/llm.py
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
```

### Synchronous client and async mixing

The codebase uses `Anthropic()` (synchronous) throughout. If `AsyncAnthropic` is ever introduced for streaming, do not mix sync calls inside an async event loop — they block the loop's thread. Use `AsyncAnthropic` exclusively once async is adopted.

### No custom timeout or retry budget

The SDK defaults to `max_retries=2` with exponential backoff. For the consolidation loop that may call the LLM for each of dozens of memories in sequence, a rate limit event will retry but consume wall-clock time silently. Configure explicitly:

```python
from anthropic import Anthropic, DefaultHttpxClient
import httpx

client = Anthropic(
    http_client=DefaultHttpxClient(
        timeout=httpx.Timeout(60.0, connect=5.0)
    ),
    max_retries=3,
)
```

### Token usage not logged

`message.usage.input_tokens` and `message.usage.output_tokens` are available on every non-streaming response. The codebase does not log them. Without this, cost attribution and budget anomaly detection are impossible. When prompt caching is active, `input_tokens` alone undercounts — total is:

```python
total_input = (
    message.usage.input_tokens
    + getattr(message.usage, 'cache_creation_input_tokens', 0)
    + getattr(message.usage, 'cache_read_input_tokens', 0)
)
```

### Token counting before large prompts

`client.messages.count_tokens()` can check whether a constructed prompt fits the context window before making the full request. This is relevant in `pre_compact.py` where `conversation_text` (the full Claude session) is passed to reconsolidation and consolidation prompts. Very long sessions can exceed the 200K-token context window.

### Rate limiting is token-bucket, not per-minute reset

The API enforces three independent dimensions: RPM, ITPM, OTPM. A burst of simultaneous requests will hit per-second enforcement even if the per-minute budget has headroom. The pre-compact loop is sequential, so this is currently low risk.

---

## 5. NLTK

### Network download on first call — airgap failure

`core/relevance.py` and `core/feedback.py` both import nltk at module level and call `nltk.data.find('corpora/stopwords')` inside functions, with a `try/except LookupError` that triggers `nltk.download('stopwords', quiet=True)`. This means:

- On a cold machine, the first call to any relevance/feedback function blocks on a network download.
- In environments without internet access (CI, containers, deployment boxes without outbound HTTP), `nltk.download` fails silently (returns `False`), and the code falls back to empty stopwords — which defeats the entire text normalization step without any log warning.

*Fix:* Move `nltk.download` calls to a one-time setup step with explicit failure logging:
```python
import nltk, sys
for corpus in ['stopwords']:
    try:
        nltk.data.find(f'corpora/{corpus}')
    except LookupError:
        ok = nltk.download(corpus, quiet=False)
        if not ok:
            print(f"WARNING: NLTK corpus '{corpus}' unavailable", file=sys.stderr)
```

Or bundle the corpora and set `NLTK_DATA=/path/to/bundled` in the environment.

### `OSError` from download not caught

`nltk.download()` raises `OSError` if `~/nltk_data/` is read-only (common in containerized environments). The existing `try/except LookupError` does not catch `OSError`. Add it:

```python
try:
    nltk.download('stopwords', quiet=True)
except OSError as e:
    logger.warning("Could not download NLTK stopwords: %s", e)
```

### PorterStemmer instantiation pattern

The codebase instantiates `PorterStemmer()` inside functions (new instance per call). This is safe. A module-level `_stemmer = PorterStemmer()` would be a performance improvement but is safe to share across calls (PorterStemmer is stateless). Do not share across threads without validation against the installed NLTK version.

### Stopword list is English-only

`nltk_stopwords.words('english')` is hardcoded. If non-English content enters the memory store, stop-word filtering will be ineffective and keywords from other languages will inflate relevance scores.

---

## 6. scikit-learn TF-IDF

### Lazy import pattern is correct but needs broad except

`scripts/reduce.py` and `scripts/consolidate.py` import sklearn inside functions with a guard:
```python
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    return []
```

This is intentional (sklearn is optional). The risk: a partial sklearn install (e.g., missing native C extensions on ARM, incomplete conda environment) may raise `ImportError` on a submodule rather than the top-level package. The outer `except ImportError` catches it correctly in Python 3.10+ (submodule ImportErrors propagate as ImportError), but verify the `except` is at the right scope.

### Dense matrix O(n²) memory scaling

`cosine_similarity(matrix)` with a sparse TF-IDF matrix materializes an `(n, n)` float64 dense array. For n=1000 observations that is 8MB; for n=10,000 it is 800MB. The current use is batch dedup at script time (not live pipeline), so risk is low now but grows with the observation store. Use chunked comparison for large batches:

```python
# Instead of full dense cosine_similarity(matrix):
from sklearn.metrics.pairwise import cosine_similarity
CHUNK = 100
for i in range(0, len(corpus), CHUNK):
    chunk_sims = cosine_similarity(matrix[i:i+CHUNK], matrix)
    # process chunk_sims
```

Do not call `.toarray()` on the TF-IDF sparse matrix before passing to `cosine_similarity` — sparse input is more memory-efficient and equally correct.

---

## 7. Memory Lifecycle Anti-Patterns

These patterns are either present in the codebase or easy to introduce given the architecture.

### Stale embeddings after content change (critical)

Covered in section 4 above. `Memory.save()` does not re-embed when `content_hash` changes. KNN results for updated memories are wrong until the next full re-embed run.

### FTS5 index drift on bulk operations

`Memory.insert_many()`, `bulk_create()`, or raw `INSERT INTO memories` SQL bypass `Memory.save()` and do not update `memories_fts`. Any code added outside the `save()` override will silently diverge FTS.

### Double-logging injections for tier1+tier2 overlap

`inject_for_session` logs every memory in `tier1 + tier2` via `_record_injection`. A memory that is `instinctive` stage and also scores high on hybrid search (e.g., if it was recently crystallized from instinctive) could appear in both lists. `injection_count` would be overcounted. Add a `seen_ids` guard:

```python
seen = set()
for memory in tier1 + tier2:
    if memory.id not in seen:
        _record_injection(memory.id, session_id, project_context=project_context)
        seen.add(memory.id)
```

### Thompson sampling non-determinism in tests

`_thompson_rerank` uses `random.betavariate` without seeding. Tests that depend on retrieval order will produce non-deterministic results. The existing tests do not test ranked order directly, but any test that asserts "first result is X" will flap. Seed the RNG in test fixtures or accept that rank-order tests require explicit mocking.

### Crystallizer/rehydration tension (known)

The crystallizer archives source memories after synthesis. The relevance engine immediately considers rehydrating them. Net effect is additive (crystal + potentially rehydrated sources), not the intended compression. Over time this inflates the store. Mitigation: unconditionally archive sources on crystallization and only rehydrate on explicit strong relevance signal (relevance > rehydrate_threshold + margin).

### `consolidation_log` migration not atomic (critical)

The `_run_migrations()` migration that rebuilds `consolidation_log` to add `'subsumed'` to the CHECK constraint does DROP TABLE + CREATE TABLE + Python INSERT loop without a wrapping transaction. A process kill during the loop permanently destroys the log.

*Fix:*
```python
with db.atomic():
    db.execute_sql("DROP TABLE consolidation_log")
    db.execute_sql("CREATE TABLE consolidation_log (...)")
    for r in rows:
        db.execute_sql("INSERT INTO consolidation_log ...", list(r))
```

---

## Recommended Actions by Priority

| Priority | Item | File(s) |
|----------|------|---------|
| HIGH | Add `conn.setbusytimeout(5000)` to `VecStore._connect()` | core/vec.py |
| HIGH | Assert embedding dimension before INSERT and KNN | core/vec.py |
| HIGH | Re-embed on content_hash change in `Memory.save()` | core/models.py |
| HIGH | Wrap `consolidation_log` migration in `db.atomic()` | core/database.py |
| HIGH | Verify system SQLite >= 3.51.3 at startup | core/database.py |
| MEDIUM | Log WAL checkpoint result in `close_db()` | core/database.py |
| MEDIUM | Standardize all datetimes to UTC-aware ISO | core/*.py |
| MEDIUM | Move NLTK download to init; catch `OSError` | core/feedback.py, core/relevance.py |
| MEDIUM | Log `message.usage` tokens in all LLM calls | core/llm.py and callers |
| MEDIUM | Add `seen` guard against double-logging injections | core/retrieval.py |
| LOW | Pool apsw connection for batch embedding loops | core/vec.py, hooks/pre_compact.py |
| LOW | Add `ThreadSafeDatabaseMetadata` to `BaseModel.Meta` | core/models.py |
| LOW | Centralize `CLAUDE_MODEL` constant | core/llm.py |
| LOW | Guard against dense matrix in large-n TF-IDF | scripts/reduce.py, scripts/consolidate.py |

---

## Pitfalls Summary Table

| Component | Pitfall | Severity | Mitigated in codebase? |
|-----------|---------|----------|----------------------|
| SQLite WAL | WAL-reset corruption bug (< 3.51.3) | HIGH | No — system SQLite not checked |
| apsw VecStore | No busy timeout | HIGH | No |
| sqlite-vec | Dimension mismatch silent wrong answers | HIGH | No |
| Memory lifecycle | Stale embeddings after content update | HIGH | No |
| peewee | `consolidation_log` migration not atomic | HIGH | No |
| SQLite WAL | Checkpoint failure not logged | MEDIUM | No |
| peewee | Naive vs. UTC datetime mixing | MEDIUM | No — mixed in retrieval.py |
| peewee | `bulk_create` bypasses FTS sync | MEDIUM | Latent risk — not used yet |
| Anthropic SDK | No usage token logging | MEDIUM | No |
| Anthropic SDK | No custom timeout/retry config | MEDIUM | No |
| NLTK | Network download blocks on first call | MEDIUM | Partial (quiet=True, not airgap-safe) |
| NLTK | `OSError` from download not caught | MEDIUM | No |
| Memory lifecycle | Double-logging tier1+tier2 overlap | LOW | No |
| Memory lifecycle | Thompson sampling test non-determinism | LOW | No |
| apsw VecStore | Extension loaded per-call (perf) | LOW | No |
| apsw VecStore | `enable_load_extension` not available (some builds) | LOW | Partial (ImportError caught, not AttributeError) |
| peewee | `_pk_exists()` N+1 SELECT on every save | LOW | No |
| peewee | Stale `_vec_store` singleton after re-init | LOW | Avoidable at call sites |
| sklearn | Dense matrix O(n²) at large n | LOW | Low risk at current store size |

---

**Gaps:**
- apsw official documentation (readthedocs.io) was blocked by Cloudflare; apsw-specific threading guarantees beyond SQLite's thread-safety model were not verified from the official source. Thread-safety characterizations are from known SQLite behavior.
- NLTK thread-safety across versions was not confirmed from official docs; `PorterStemmer` statelessness assessment is from known implementation behavior.
- The macOS Sequoia system SQLite version was not confirmed in this session; `sqlite3.sqlite_version` at runtime is the authoritative check.
- scikit-learn sparse matrix handling with `cosine_similarity` confirmed from sklearn API docs (accessed via prior Context7 checks) but not re-verified in this session.
