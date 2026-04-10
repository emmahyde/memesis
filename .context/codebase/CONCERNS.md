# Concerns & Technical Debt

## Known Issues

- **Stale embeddings after content update (correctness bug)**: `Memory.save()` in `core/models.py` recomputes `content_hash` when content changes but does not trigger re-embedding. When reconsolidation refines a memory (appending to `content`) or contradiction resolution rewrites it, the vector in `vec_memories` reflects the old content. KNN queries rank that memory by its stale embedding position, producing incorrect hybrid RRF results. Every `_crystallize_group` in `core/crystallizer.py` and every "refined" action in `core/reconsolidation.py` leaves a stale embedding behind. Source: `core/models.py:184-197`, `core/reconsolidation.py:150-153`, `core/crystallizer.py:359-374`.

- **No busy timeout on apsw connections**: `VecStore._connect()` in `core/vec.py:54-59` opens `apsw.Connection(self._db_path)` without calling `conn.setbusytimeout()`. The peewee connection sets `busy_timeout: 5000` via pragmas (`core/database.py:83`), but apsw connections get the SQLite default of 0ms. If peewee holds a write lock during `db.atomic()` in `Memory.save()`, a concurrent `store_embedding` call raises `apsw.BusyError` immediately. Source: ecosystem-pitfalls research, `core/vec.py:54-59`.

- **No embedding dimension validation**: `VecStore.store_embedding()` and `search_vector()` in `core/vec.py` accept arbitrary `bytes` with no length check. The vec0 table is declared `float[512]` (2048 bytes). If `DEFAULT_DIMENSIONS` in `core/embeddings.py:20` ever changes, or a differently-sized embedding blob is inserted, sqlite-vec returns garbage distances silently -- no exception raised. Source: `core/vec.py:65-76`, `core/embeddings.py:20`, ecosystem-stack research.

- **`consolidation_log` migration not atomic**: `_run_migrations()` in `core/database.py:211-244` rebuilds the `consolidation_log` table by executing `DROP TABLE`, `CREATE TABLE`, then a Python loop of `INSERT` statements -- all outside any transaction wrapper. A process kill between `DROP` and loop completion permanently destroys the consolidation log with no recovery path. Source: `core/database.py:222-244`.

- **Naive vs UTC datetime mixing**: The codebase uses `datetime.now().isoformat()` (naive, no timezone) in 30+ locations across `core/`, but `core/retrieval.py:382` uses `datetime.now(timezone.utc).isoformat()` (UTC-aware, produces `+00:00` suffix). Mixed formats break `datetime.fromisoformat()` parsing and lexicographic ordering in provenance batch logic (`_compute_provenance_batch`). The relevance engine's `_days_since_last_activity` compares naive `now` against stored timestamps that may be either format. Source: grep across `core/` confirms 30+ naive calls vs 1 UTC-aware call.

- **WAL checkpoint failure silently swallowed**: `close_db()` in `core/database.py:119-128` executes `PRAGMA wal_checkpoint(TRUNCATE)` inside a bare `except Exception: pass`. If the checkpoint fails (e.g., a reader holds an open transaction), the WAL grows unbounded with no warning logged. The checkpoint returns `(busy, log, checkpointed)` columns that are never inspected. Source: `core/database.py:121-124`.

- **Double-logging injections for tier1+tier2 overlap**: `inject_for_session()` in `core/retrieval.py:97-98` calls `_record_injection` for every memory in `tier1 + tier2` without deduplication. An instinctive memory that also scores high in hybrid search appears in both lists. Its `injection_count` is incremented twice, distorting SM-2 scheduling and Thompson sampling parameters. Source: `core/retrieval.py:88-98`.

- **`_session_usage` private attribute accessed externally**: `hooks/pre_compact.py:231` accesses `feedback._session_usage` directly to count used memories for the summary string. This is fragile coupling to `FeedbackLoop`'s internal state. Source: `hooks/pre_compact.py:231`, `core/feedback.py:63`.

## Technical Debt

| Area | Description | Files | Severity |
|---|---|---|---|
| Stale embeddings | `Memory.save()` does not re-embed when `content_hash` changes; KNN results are wrong for updated memories | `core/models.py:184-197` | high |
| apsw busy timeout | VecStore connections have 0ms timeout vs peewee's 5000ms; concurrent writes raise `BusyError` | `core/vec.py:54-59` | high |
| Embedding dimension validation | No runtime check that embedding blob matches vec0 table dimensions; silent garbage on mismatch | `core/vec.py:65-76`, `core/embeddings.py:20` | high |
| Migration atomicity | `consolidation_log` rebuild not wrapped in `db.atomic()`; crash = permanent data loss | `core/database.py:222-244` | high |
| Datetime format mixing | 30+ naive `datetime.now()` calls vs 1 UTC-aware call; breaks ordering and parsing | `core/retrieval.py:31,382`, `core/models.py:186`, many others | medium |
| WAL checkpoint silent failure | `close_db()` swallows checkpoint errors; WAL can grow unbounded | `core/database.py:121-124` | medium |
| `_pk_exists()` N+1 SELECT | `Memory.save()` calls `get_by_id()` before every save to detect insert vs update; doubles query count in batch operations | `core/models.py:190,206-212` | medium |
| Per-call apsw connection + extension load | `VecStore._connect()` opens a new connection and loads `sqlite_vec` extension on every operation; multiplied in batch embedding loops | `core/vec.py:54-59`, `hooks/pre_compact.py:158-169` | medium |
| Stopword set duplication | Hardcoded `_STOP_WORDS` in `core/models.py:137-150` and NLTK `stopwords.words('english')` in `core/feedback.py` / `core/relevance.py` are not identical; keyword behavior diverges | `core/models.py:137`, `core/feedback.py:45`, `core/relevance.py:29` | medium |
| `rrf_k=60` duplication | RRF constant defined as default arg in `hybrid_search` and as `_RRF_K = 60` in `_crystallized_hybrid`; can drift independently | `core/retrieval.py:236,733` | low |
| Embedding dimension constant duplication | `DEFAULT_DIMENSIONS = 512` in `core/embeddings.py` and `float[512]` hardcoded in vec0 DDL in `core/vec.py`; must stay in sync manually | `core/embeddings.py:20`, `core/vec.py:43` | low |
| No LLM usage token logging | `call_llm()` in `core/llm.py` discards `response.usage`; no cost attribution or budget monitoring | `core/llm.py:93-100` | low |
| No custom timeout/retry on Anthropic client | SDK defaults (2 retries, default timeout); long consolidation loops can hang silently | `core/llm.py:85-98` | low |

## Security Considerations

- **AWS credentials defaulting**: `core/embeddings.py:37-38` defaults `AWS_PROFILE` to `"bedrock-users"` and `AWS_REGION` to `"us-west-2"` via `os.environ.get()`. The `hooks/consolidate_cron.py` sets `CLAUDE_CODE_USE_BEDROCK` as a process-global default. A misconfigured AWS profile silently uses unexpected credentials.

- **Content hash uses MD5**: `Memory.compute_hash()` in `core/models.py:178-180` uses MD5 for deduplication. MD5 is not collision-resistant. A crafted input could produce a collision that falsely triggers duplicate rejection, preventing memory creation. SHA-256 is negligibly more expensive.

- **FTS5 operator injection mitigated**: `Memory.sanitize_fts_term()` (`core/models.py:117-126`) wraps terms in double-quotes to neutralize FTS5 operators. `tokenize_fts_query()` (`core/models.py:129-160`) applies this to all query terms. `hooks/user_prompt_inject.py` and `core/relevance.py` both use `sanitize_fts_term()`. This concern from the previous CONCERNS.md has been addressed.

- **`HOME` mutation in test fixtures**: If `tests/conftest.py` mutates `os.environ['HOME']` for test isolation, this is process-global and unsafe under parallel test execution (`pytest-xdist`). Any test reading `Path.home()` during the mutation window gets the wrong path.

## Performance

- **N+1 `get_embedding()` calls in reconsolidation**: `_rank_by_similarity()` in `core/reconsolidation.py:403-424` calls `vec_store.get_embedding()` individually for the source and each candidate. Each call opens a new apsw connection, loads the sqlite-vec extension, executes the query, and closes. For 3 candidates this is 4 connection cycles. The same pattern appears in `_centroid_similarities()` in `core/graph.py:158-195` (once per seed + once per target). Source: `core/reconsolidation.py:403-424`, `core/graph.py:158-195`.

- **`compute_relevance()` per-memory queries for integration factor**: When `integration_factor` flag is enabled, `RelevanceEngine.compute_relevance()` in `core/relevance.py:122-138` calls up to 4 separate `.exists()` queries per memory (thread membership, tag overlap, causal edges, contradiction edges). For `score_all()` processing every active memory, this is 4N queries. The `_has_tag_overlap()` method (`core/relevance.py:168-202`) iterates each non-meta tag and runs a separate query for each. Source: `core/relevance.py:122-138,168-202`.

- **Consolidation `_build_manifest_summary()` loads all memories**: `Consolidator._build_manifest_summary()` in `core/consolidator.py:167-186` calls `Memory.by_stage()` for each of three stages, loading all non-ephemeral memories into Python to build a text summary for the consolidation prompt. With a growing memory store this becomes increasingly expensive for every consolidation run.

- **Tag co-occurrence `compute_edges()` quadratic check**: `core/graph.py:76-91` iterates all `combinations(mids, 2)` for each tag, and for each pair runs an `.exists()` query to check for duplicate edges. For a tag shared by 20 memories, this is C(20,2) = 190 existence checks, each a separate SQL query. Source: `core/graph.py:76-91`.

- **Token budget approximation**: `CONTEXT_WINDOW_CHARS = 200_000 * 4` in `core/retrieval.py:23` assumes 4 chars/token. The Anthropic SDK provides `client.messages.count_tokens()` for exact counting. The approximation is adequate for English prose but may over-/under-estimate for non-ASCII content or structured data.

## Fragile Areas

- **FTS5 external content sync on bulk operations**: `Memory.save()` overrides keep FTS in sync via `_fts_delete_from_db()` + `_fts_insert()` inside `db.atomic()`. But `Memory.insert_many()`, `bulk_create()`, or raw `INSERT INTO memories` SQL bypass `save()` entirely, leaving `memories_fts` desynced. Currently no code path uses bulk inserts, but adding one without knowing about this constraint would silently break full-text search. Recovery: `INSERT INTO memories_fts(memories_fts) VALUES('rebuild')`. Source: `core/models.py:184-197`.

- **FTS desync on SIGKILL**: The FTS sync sequence in `Memory.save()` (DELETE fts row, INSERT memories row, INSERT fts row) is wrapped in `db.atomic()`, which protects against normal exceptions. But `SIGKILL` or OOM between the DELETE and INSERT steps leaves the FTS index missing a row. No startup integrity check or `rebuild_fts()` utility exists. Source: `core/models.py:192-196`.

- **Crystallizer/rehydration tension**: The crystallizer archives source memories after synthesis (`core/crystallizer.py:359-374`). The relevance engine immediately considers rehydrating them based on relevance score (`core/relevance.py:297-358`). Net effect is additive (crystal + potentially rehydrated sources), not the intended compression. Over time this inflates the active memory store. Source: documented in `consolidated/native/crystallization_rehydration_tension.md`.

- **Lock file coordination between hooks**: `hooks/pre_compact.py` and `hooks/consolidate_cron.py` both use `fcntl.flock()` on `ephemeral/.lock`. Both write a snapshot to `.processing-{ephemeral_path.name}`. If both run simultaneously against the same buffer, they overwrite each other's snapshot. The lock protects the read+clear of the original buffer but not the snapshot file. Source: `hooks/pre_compact.py:93-103`.

- **NLTK network download on first call**: `core/relevance.py` and `core/feedback.py` guard `nltk.data.find('corpora/stopwords')` with `try/except LookupError`, falling back to `nltk.download('stopwords', quiet=True)`. In airgapped environments the download fails silently (returns `False`), and keyword extraction degrades to no stopword filtering. Additionally, `nltk.download()` raises `OSError` if `~/nltk_data/` is read-only (containers) -- not caught by the `LookupError` handler. Source: `core/feedback.py:24-32`, `core/relevance.py:370-376`.

- **Thompson sampling non-determinism in tests**: `_thompson_rerank()` in `core/retrieval.py:693-714` uses `random.betavariate` without seeding. Any test asserting retrieval order will produce non-deterministic results. Source: `core/retrieval.py:705-711`.

- **Feature flag cache never invalidated during a session**: `core/flags.py:44-68` caches flags on first `get_flag()` call for the lifetime of the process. If `flags.json` is modified while a hook is running (e.g., during a long pre_compact run), the change is invisible until the next process invocation. `reload()` exists but is never called except explicitly. Source: `core/flags.py:44-74`.

- **`_increment_consolidation_count()` not atomic**: `hooks/pre_compact.py:48-53` reads a JSON counter file, increments in Python, and writes back. A read-modify-write race is possible under parallel test workers or unusual hook invocation. This counter controls self-reflection frequency. Source: `hooks/pre_compact.py:48-53`.
