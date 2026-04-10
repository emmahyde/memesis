# Research: Python AI Memory Agent Ecosystem Stack

> This file was originally created 2026-03-28 covering SQLite WAL, FTS5, Anthropic SDK async patterns, and pytest.
> Updated 2026-03-30 to cover the full stack as deployed: Anthropic SDK, peewee ORM, sqlite-vec, apsw, scikit-learn, NLTK, and cross-cutting patterns for memory/AI agent systems, spaced repetition, graph-based knowledge, and reconsolidation.

---

## Part 1: Foundational Stack (2026-03-28)

**Confidence:** HIGH (SQLite/WAL: official docs; FTS5: official docs; Anthropic SDK: Context7 + official; pytest: Context7 + official; atomic writes: official Python docs + codebase observation)

**Sources:**
- https://www.sqlite.org/wal.html
- https://www.sqlite.org/fts5.html
- Context7: `/anthropics/anthropic-sdk-python` (High reputation)
- Context7: `/pytest-dev/pytest` (High reputation)
- Codebase: `core/database.py`, `core/models.py`, `core/vec.py`

### 1. SQLite WAL Mode

WAL mode is persistent once set. The codebase correctly uses:
```python
db.init(str(dp), pragmas={
    "journal_mode": "wal",
    "synchronous": "normal",
    "busy_timeout": 5000,
})
```

`synchronous=NORMAL` is safe with WAL. `busy_timeout=5000` prevents immediate `OperationalError` on concurrent access. Shutdown pattern: `PRAGMA wal_checkpoint(TRUNCATE)` before close (implemented in `close_db()`).

**Concurrency model:**
- Multiple simultaneous readers: fully concurrent
- Reader + writer: concurrent
- Two simultaneous writers: second blocks (single WAL)

**Limitations:** No network filesystems (requires `-shm` file). Not a concern for local `~/.claude/memory/index.db`.

### 2. SQLite FTS5 External Content Tables

The `memories_fts` table uses `content='memories', content_rowid='rowid'` — the FTS index stores only the inverted index; text is read from the content table on demand. This is the correct pattern for keeping DB compact.

**Tokenizer choice:** `unicode61` (default) is correct for English markdown. Adding `porter` as a wrapper gives stemmed recall (`running` matches `run`):
```sql
tokenize='porter unicode61 remove_diacritics 1'
```

**Column weighting** for relevance tuning:
```sql
ORDER BY bm25(memories_fts, 10.0, 5.0, 3.0, 1.0)
-- weights:              title  summary  tags  content
```

**FTS sync via Python vs triggers:** The codebase syncs manually in `Memory.save()` and `delete_instance()`. The SQLite FTS5 docs recommend SQL triggers (atomic with row writes, cannot be skipped by direct SQL). The risk: any `Memory.update(...).execute()` bulk call that touches FTS-indexed columns will desync the index. Currently safe because bulk updates only touch `last_injected_at`, `injection_count`, etc. (non-indexed). Add a `rebuild_fts()` utility for post-migration safety:
```python
def rebuild_fts():
    db.execute_sql("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
```

**detail level tradeoff:**
| detail | Index size | Phrase queries |
|--------|-----------|----------------|
| `full` (current default) | 100% | Yes |
| `column` | ~50% | No |
| `none` | ~18% | No |

`detail=column` is a worthwhile optimization if index size becomes a concern. For OR-joined keyword queries (current pattern), `column` is sufficient.

### 3. Anthropic SDK — Sync vs Async, Error Handling

The codebase uses synchronous `Anthropic()` in scripts and hooks — correct for sequential, non-event-loop contexts.

**Error hierarchy:**
```python
from anthropic import (
    APIConnectionError,  # Network; SDK auto-retries
    RateLimitError,      # 429; SDK auto-retries
    AuthenticationError, # 401; don't retry
    BadRequestError,     # 400; prompt too long, bad params
    APIStatusError,      # Other 4xx/5xx
)
```

SDK default: 2 retries with exponential backoff for transient errors. Configure: `Anthropic(max_retries=3)`.

**JSON extraction pattern** (used in `reconsolidation.py`, `consolidator.py`):
```python
import re, json

def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```$", "", text.strip())
    return json.loads(text)
```

### 4. pytest — Fixtures and Database Testing

The codebase uses `function`-scoped fixtures with `tmp_path` — correct for per-test isolation. Always call `close_db()` in teardown to checkpoint WAL and release file handles.

Key: `playhouse.migrate` and `SqliteExtDatabase` tests should mock `db.init()` to a tmp path, not use a real `~/.claude` path.

---

## Part 2: Full Stack Ecosystem (2026-03-30)

**Confidence:** HIGH for library APIs (Context7, official docs) / MEDIUM for cross-cutting agent patterns
**Sources:**
- Context7: `/anthropics/anthropic-sdk-python` (127 snippets, High reputation)
- Context7: `/asg017/sqlite-vec` (294 snippets, High reputation)
- Context7: `/coleifer/peewee` (2199 snippets, High reputation)
- Official SQLite FTS5 docs: https://www.sqlite.org/fts5.html
- Python 3.10 changelog: https://docs.python.org/3.10/whatsnew/3.10.html
- Direct codebase analysis: all `core/*.py`, `scripts/reduce.py`, `scripts/consolidate.py`, `requirements.txt`

---

## Current Landscape

### Python 3.10+ Feature Adoption in This Codebase

| Feature | Status | Notes |
|---------|--------|-------|
| `X \| Y` union types | Partially adopted | Newer files use `bytes \| None`; older use `Optional[bytes]` |
| `match`/`case` | Not used | Would clean up action routing in `reconsolidation.py` |
| `@dataclass(slots=True)` | Not used | No dataclasses in codebase; Peewee Models used instead |
| `tuple[A, B]` return hints | Adopted in newer files | `core/database.py` uses `tuple[Path, Path]` |
| Parenthesized context managers | Not used | Single `with` blocks throughout |

**Recommended adoption:** Use `match` for the `confirmed|contradicted|refined|unmentioned` action routing in `reconsolidation.py`. Use `X | Y` union syntax consistently across all new code.

---

## Library-by-Library Best Practices

### Anthropic SDK (>=0.40.0)

**Confidence:** HIGH (Context7, 127 snippets)

**1. Tool runner loop (not yet used)**

The SDK's `client.beta.messages.tool_runner` handles agentic tool loops automatically, with optional `compaction_control` for long-running agents:
```python
runner = client.beta.messages.tool_runner(
    model="claude-sonnet-4-5-20250929",
    max_tokens=4096,
    tools=[search, done],
    messages=[{"role": "user", "content": "..."}],
    compaction_control={"enabled": True, "context_token_threshold": 5000}
)
for message in runner:
    print(f"Stop reason: {message.stop_reason}")
```
This is the recommended upgrade path if memesis grows an agentic pipeline.

**2. Token counting before injection**

Pre-flight injection string size before sending to the model:
```python
token_count = client.messages.count_tokens(
    model="claude-sonnet-4-5-20250929",
    messages=[{"role": "user", "content": injection_string}],
    system="..."
)
```
Currently the codebase approximates `1 token ≈ 4 chars`. The SDK's exact counter is more reliable, especially for non-English content.

**3. Extended thinking for reconsolidation**

For complex contradiction resolution (deciding whether to merge vs. flag), `thinking={"type": "enabled", "budget_tokens": 2000}` gives the model space to reason before committing to a JSON decision array. Budget tokens are consumed from `max_tokens`. Minimum useful budget: ~2000 tokens.

**4. Structured outputs via Pydantic**

The SDK integrates Pydantic for typed JSON extraction, which would harden `reconsolidation.py`'s `json.loads(strip_markdown_fences(raw))` pattern. At the cost of a Pydantic dependency.

**5. Model naming**

Current usage: `claude-sonnet-4-6`. Explicit date-versioned IDs (`claude-sonnet-4-5-20250929`) are more stable for reproducibility in CI/test environments. The codebase's alias is valid for production.

---

### Peewee ORM (>=3.17)

**Confidence:** HIGH (Context7, 2199 snippets)

**1. Deferred database — correct pattern**

`SqliteDatabase(None)` + `db.init(path, pragmas={...})` is canonical for library code. The codebase implements this correctly in `core/models.py` + `core/database.py`.

**2. WAL pragmas — complete set**

Current pragmas are good. The full recommended set for production:
```python
db.init(str(dp), pragmas={
    "journal_mode": "wal",
    "synchronous": "normal",
    "busy_timeout": 5000,
    "cache_size": -64000,   # 64MB — not currently set
    "foreign_keys": 1,      # Not set; codebase uses TextField FKs intentionally
})
```
`cache_size` omission is a minor performance gap. `foreign_keys` omission is intentional (TextField FKs bypass enforcement).

**3. Schema migrations via `playhouse.migrate`**

The codebase uses raw `ALTER TABLE` in `_run_migrations()`. The `playhouse.migrate.SqliteMigrator` is the canonical approach:
```python
from playhouse.migrate import SqliteMigrator, migrate
migrator = SqliteMigrator(db)
with db.atomic():
    migrate(
        migrator.add_column('memories', 'new_field', TextField(null=True)),
    )
```
The manual approach works but is harder to audit. The playhouse version is self-documenting and wraps atomically.

**4. `playhouse.sqlite_ext.SqliteExtDatabase`**

Extends `SqliteDatabase` with BM25 rank functions, `REGEXP`, and `json_contains()`. Since FTS5 is managed manually (for control over the content table pattern), upgrading is optional. The BM25 rank functions become useful if peewee ORM queries need to order by FTS rank without raw SQL.

**5. N+1 prevention — current code is correct**

The retrieval engine uses `.in_(ids)` batch queries and `{m.id: m for m in ...}` dict lookup. Peewee's `prefetch()` helper is an alternative for nested join hydration patterns.

**6. `Memory._pk_exists()` cost**

Called on every `save()` to determine insert vs update:
```python
# Current (2 queries per save):
is_update = not force_insert and self.id and self._pk_exists()

# Better (1 query, lighter):
is_update = not force_insert and self.id and Memory.select(Memory.id).where(Memory.id == self.id).exists()
```
For high-frequency saves (batch reconsolidation), this matters.

**7. DateTime as `TextField` — known tradeoff**

All timestamps stored as ISO strings in `TextField`. Works correctly for ISO format string comparison. Loses peewee `DateTimeField` helpers (`.year`, `.month`, range queries). Not worth changing retroactively.

**8. `playhouse.apsw_ext.APSWDatabase`**

Peewee ships a playhouse adapter for apsw. The codebase separates apsw (for vec) from peewee (for relational) rather than unifying. This is the cleaner design given the extension-loading requirement.

---

### sqlite-vec (>=0.1.6) + apsw (>=3.46)

**Confidence:** HIGH (Context7, 294 snippets)

**Why apsw over stdlib sqlite3:**
On macOS, the system Python ships `sqlite3` without `enable_load_extension`. `apsw` bundles its own SQLite amalgamation and always enables extension loading. The codebase correctly routes all vec operations through apsw (`core/vec.py`).

**1. Extension loading — correct pattern**
```python
conn = apsw.Connection(path)
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())
```
`sqlite_vec.loadable_path()` returns the OS-correct path (`.so`/`.dylib`/`.dll`).

**2. `serialize_float32` idiom**

The codebase uses `struct.pack(f"{n}f", *vec)` manually. `sqlite_vec.serialize_float32(list)` is the idiomatic equivalent and slightly more readable:
```python
from sqlite_vec import serialize_float32
embedding_bytes = serialize_float32([0.1, 0.2, 0.3, ...])
```

**3. Metadata columns in vec0 — gap in current schema**

The current vec0 table:
```sql
CREATE VIRTUAL TABLE vec_memories USING vec0(
    memory_id TEXT PRIMARY KEY,
    embedding float[512]
)
```
Filtered KNN requires a post-query JOIN to the `memories` table. Adding metadata columns enables in-scan filtering:
```sql
CREATE VIRTUAL TABLE vec_memories USING vec0(
    memory_id TEXT PRIMARY KEY,
    embedding float[512],
    stage TEXT,
    importance FLOAT
);

-- Then filtered KNN:
SELECT memory_id, distance FROM vec_memories
WHERE embedding MATCH ? AND k = 20 AND stage = 'crystallized'
ORDER BY distance;
```
This would eliminate the Python-side filtering in `_crystallized_hybrid`. **Migration caveat:** vec0 tables cannot be `ALTER TABLE`'d; a new table + data copy is required.

**4. Hybrid FTS + vector in pure SQL (alternative to Python RRF)**

The standard SQL CTE pattern for hybrid search:
```sql
WITH fts_results AS (
    SELECT rowid, rank FROM memories_fts WHERE memories_fts MATCH ?
),
vec_results AS (
    SELECT memory_id, distance FROM vec_memories WHERE embedding MATCH ? AND k = 20
),
combined AS (
    SELECT rowid AS id FROM fts_results
    UNION ALL
    SELECT memory_id AS id FROM vec_results
)
SELECT id FROM combined GROUP BY id;
```
The codebase implements RRF in Python. Both approaches are valid; Python RRF is more testable and debuggable.

**5. Connection-per-operation**

`VecStore` opens and closes an apsw connection per call. For the current use case (low-frequency memory lifecycle operations), this is correct and simpler than a pool. For higher-throughput scenarios, a persistent connection with a mutex would help.

**6. `INSERT OR REPLACE` for upsert**

The codebase uses `INSERT OR REPLACE INTO vec_memories` — correct for vec0 tables with a PRIMARY KEY column. Standard `ON CONFLICT DO UPDATE` syntax is not supported by vec0.

---

### scikit-learn (>=1.4)

**Confidence:** HIGH for APIs, MEDIUM for optimization guidance

**How it is used:**
- `scripts/reduce.py`: `TfidfVectorizer` + `cosine_similarity` for near-duplicate detection within a batch before LLM review.
- `scripts/consolidate.py`: Same TF-IDF clustering to group observations thematically before the consolidation LLM call.

**1. Lazy import with graceful degradation — correct pattern**
```python
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    return []  # or {}
```
sklearn is a soft quality-of-life dependency. The system degrades gracefully without it.

**2. `TfidfVectorizer` parameters for small corpora**

Current: `TfidfVectorizer(min_df=1, stop_words='english')` — correct for batches of tens to low hundreds. At scale: `min_df=2` reduces noise; `max_features=10000` caps memory.

**3. Near-duplicate threshold**

Standard thresholds for short-text (observation/memory) dedup:
- `>= 0.92`: near-identical phrasing
- `>= 0.85`: same idea, different words
- `>= 0.75`: thematically related (not a duplicate)

The 0.85–0.90 range is typical for dedup before LLM clustering.

**4. Alternative: embedding-based dedup**

Once vec embeddings exist in `VecStore`, TF-IDF can be replaced with cosine similarity over Titan v2 embeddings. This is semantically richer and reuses existing infrastructure:
```python
# Pseudo-code: compute pairwise cosine over stored embeddings
embeddings = [vec_store.get_embedding(mid) for mid in batch_ids]
# then struct.unpack + numpy or manual dot products
```

**5. Alternative clustering: `AgglomerativeClustering`**

For thematic grouping before consolidation, agglomerative clustering produces cleaner topic clusters than threshold-based grouping when cluster count is unknown:
```python
from sklearn.cluster import AgglomerativeClustering
clustering = AgglomerativeClustering(
    n_clusters=None,
    distance_threshold=0.3,
    metric='cosine',
    linkage='average'
)
labels = clustering.fit_predict(tfidf_matrix.toarray())
```

**6. `linear_kernel` vs `cosine_similarity` for sparse matrices**

`cosine_similarity(tfidf)` computes on dense output. For sparse TF-IDF matrices, `linear_kernel` (assumes L2-normalized input) is faster:
```python
from sklearn.metrics.pairwise import linear_kernel
tfidf_normalized = TfidfVectorizer(norm='l2').fit_transform(texts)
sims = linear_kernel(tfidf_normalized)
```

---

### NLTK (>=3.8)

**Confidence:** HIGH for APIs

**How it is used:**
- `core/feedback.py`: Stopword removal + Porter stemming for keyword extraction from feedback text.
- `core/relevance.py`: Same — for relevance/rehydration term matching.

**1. Lazy download guard — correct pattern**
```python
try:
    nltk.data.find('corpora/stopwords')
    stop = set(nltk_stopwords.words('english'))
except LookupError:
    nltk.download('stopwords', quiet=True)
    stop = set(nltk_stopwords.words('english'))
```
Do not call `download()` unconditionally in module bodies — it hits the network on every import in fresh environments.

**2. `LookupError` fallback — correct**

Both `core/feedback.py` and `core/relevance.py` handle `LookupError` (NLTK data unavailable) by degrading gracefully. This is the right pattern for environments with no internet access.

**3. Stopword duplication**

The codebase has two separate stopword sets:
- NLTK `stopwords.words('english')` in `core/feedback.py` and `core/relevance.py`
- Hardcoded `_STOP_WORDS` set in `core/models.py` (`Memory.tokenize_fts_query`)

These are not identical. A single shared source (e.g., exporting from `core/feedback.py` or a `core/nlp.py` utility) would reduce drift risk.

**4. `PorterStemmer` vs `SnowballStemmer`**

`PorterStemmer` (current) is the original 1980 algorithm. `SnowballStemmer('english')` is its successor and produces more linguistically accurate stems. For keyword matching the difference is minor; either is acceptable.

**5. `word_tokenize` vs `re.findall`**

`core/models.py` uses `re.findall(r"[a-zA-Z0-9_'-]+", ...)`. NLTK's `word_tokenize` handles contractions more correctly (`"don't"` → `["do", "n't"]`) but requires the `punkt_tab` corpus download. For keyword extraction, the regex is faster and sufficient.

**6. If sentence tokenization is added**

`nltk.sent_tokenize` requires the `punkt_tab` corpus. Add to the download guard if this is introduced:
```python
nltk.download('punkt_tab', quiet=True)
```

---

## Memory/AI Agent System Patterns

### SM-2 Spaced Repetition (`core/spaced.py`)

**Confidence:** HIGH (implementation confirmed; algorithm literature verified)

The SM-2 algorithm is implemented correctly:
- `injection_ease_factor` starts at 2.5 (standard)
- `injection_interval_days` grows multiplicatively
- Ease factor clamped `>= 1.3` (standard)
- Binary feedback: "used" (q=5) and "not used" (q=1)

**Algorithm comparison:**

| Algorithm | Fit | Key difference | Effort to adopt |
|-----------|-----|----------------|-----------------|
| SM-2 (current) | Good | Proven, simple | — |
| FSRS | Better | Probabilistic; models stability + retrievability separately | Medium |
| Leitner boxes | Too simple | Categorical, not continuous | N/A |
| Thompson sampling (current, in retrieval) | Complementary | Bandit-based explore/exploit; already implemented | — |

**FSRS as upgrade path:** FSRS models `stability` (≈ `injection_interval_days`) and `retrievability` (`e^(-t/S)`) as separate parameters. Better calibrated for irregular review schedules. Four parameters per memory vs two for SM-2. Python implementation: search PyPI for `fsrs` or `py-fsrs` — not verified during this research.

**SM-2 correctness note:** The current "refined" action in reconsolidation does not update the SM-2 schedule. A refinement is evidence the memory was actively processed — this could be treated as a successful recall (bump interval) rather than leaving the schedule untouched.

---

### Graph-Based Knowledge (`core/graph.py`, `core/models.py`)

**Confidence:** HIGH (direct codebase analysis)

**Current edge topology:**

| Edge type | Direction | How created | Preserved across rebuild |
|-----------|-----------|-------------|--------------------------|
| `thread_neighbor` | bidirectional | `compute_edges()` | No (recomputable) |
| `tag_cooccurrence` | bidirectional | `compute_edges()` | No (recomputable) |
| `caused_by` | directed | reconsolidation | Yes (incremental) |
| `refined_from` | directed | reconsolidation | Yes (incremental) |
| `subsumed_into` | directed | crystallizer | Yes (incremental) |
| `contradicts` | bidirectional | reconsolidation | Yes (incremental) |
| `echo` | TBD | not yet wired | Yes (incremental) |

**Established patterns this codebase follows correctly:**

1. **Separation of recomputable vs incremental edges** — `RECOMPUTABLE_TYPES` is the right design. Structural edges (thread/tag) can be rebuilt; causal/semantic edges accumulate evidence.

2. **Priority-tiered 1-hop expansion** — `_EDGE_PRIORITY` gives causal edges priority over structural. Matches "semantic neighborhood" pattern from GraphRAG literature.

3. **Centroid similarity for neighbor re-ranking** — Computing centroid of seed embeddings then scoring neighbors is standard for "more like this set" retrieval. The manual implementation in `_centroid_similarities` is correct.

4. **Contradiction resolution lifecycle** — `resolved`/`resolution` metadata on `contradicts` edges with `"superseded"` as a resolution type mirrors epistemic status tracking in knowledge management systems.

**Patterns not yet implemented:**

| Pattern | Description | Effort |
|---------|-------------|--------|
| Transitive closure (2-hop) | 1-hop currently; 2-hop increases recall for distant causal chains | Medium |
| Edge weight decay | Static `weight` from creation time; decaying by age would improve neighbor ranking | Low |
| Confidence decay on contradiction | Each contradiction flag decrements `importance` by a small delta | Low |
| Resolution prompt | When tension unresolved for N sessions, trigger LLM to propose merged position | Medium |

**Tag co-occurrence limit:** `if len(mids) < 2 or len(mids) > 20` skips singletons and overly-common tags. The upper bound of 20 prevents the `combinations(mids, 2)` from generating O(400) edges per popular tag. This is a correct optimization.

---

### Reconsolidation Pattern (`core/reconsolidation.py`)

**Confidence:** HIGH (direct analysis + literature alignment)

This is the most architecturally distinctive part of the system. It maps to neuroscience reconsolidation: retrieved memories are compared against new evidence and updated or flagged.

**Design decisions that are correct:**

1. **Batched LLM call per session** — One call for all injected memories. Correct cost/quality tradeoff.

2. **Pre-flagged vs first-time contradictions** — The `pre_flagged_ids` snapshot before the main loop correctly distinguishes first-time contradictions (`resolved=False`) from repeat ones (`resolved=True, resolution="superseded"`). This prevents stale tensions accumulating indefinitely.

3. **Causal edges + contradiction edges as complementary systems** — `causal_edges` tracks *why* a memory changed (directed, toward semantic neighbors); `contradiction_tensors` tracks *what it conflicts with* (bidirectional, surfaced in Tier 2.6).

4. **Cosine similarity fallback** — When embeddings are unavailable, `_rank_by_similarity` falls back to `[(cid, 0.5) for cid in candidates[:limit]]`. Correct graceful degradation.

**Gaps / improvement opportunities:**

| Pattern | Description | Effort |
|---------|-------------|--------|
| Refined memories update SM-2 | Refinement should count as successful recall — bump interval | Low |
| Confidence decay on contradiction | `importance -= 0.05` on contradiction flag | Low |
| Resolution LLM prompt | After N sessions of unresolved tension, propose merged position | Medium |
| Multi-session contradiction evidence | Track how many sessions contradict a memory before auto-deprecating | Medium |

---

### Hybrid Retrieval (`core/retrieval.py`)

**Confidence:** HIGH (direct analysis + algorithm literature)

**RRF constant `rrf_k=60`:** From the original RRF paper (Cormack et al., 2009). Values 20–80 produce similar rankings; 60 is the de-facto standard. Correct.

**`rrf_k` duplication:** The constant is defined as a default argument in `hybrid_search` and re-defined as `_RRF_K = 60` in `_crystallized_hybrid`. A module-level constant would prevent drift.

**Thompson sampling** (`_thompson_rerank`): The `Beta(usage_count+1, max(injection-usage,0)+1)` formulation is correct. `Beta(1,1)` is uniform for cold-start memories — this gives unproven memories a fair chance of exploration. The `max(..., 0)+1` guard handles data anomalies.

**Token budget:** `200_000 * 4` approximates 200K-token context at 4 chars/token. This is appropriate for English prose. Should be updated if the deployed model's context window changes.

**Three-tier architecture (Tier 1/2/2.5/2.6):**
| Tier | Content | Decision |
|------|---------|----------|
| 1 | Instinctive (behavioral guidelines) | Always inject, no filter |
| 2 | Crystallized (context knowledge) | Hybrid RRF + SM-2 + Thompson sampling + budget |
| 2.5 | Narrative threads | Thread arcs for injected memories, affect-aware ordering |
| 2.6 | Active tensions | Unresolved contradiction edges, greedy budget packed |

The affective reordering in Tier 2.5 (frustration > 0.3 → prioritize `frustration_to_mastery` arcs) is a novel pattern with no direct literature analog — it is a reasonable heuristic grounded in motivational psychology.

---

## Recommended Stack

| Technology | Purpose | Why | Confidence |
|------------|---------|-----|------------|
| `anthropic>=0.40.0` | LLM calls | Standard, type-safe, streaming; `tool_runner` for future agentic pipelines | HIGH |
| `peewee>=3.17` + `SqliteDatabase(None)` | Relational storage | Deferred init, `db.atomic()` for FTS sync, `playhouse.migrate` for migrations | HIGH |
| `playhouse.migrate.SqliteMigrator` | Schema migrations | Replace raw `ALTER TABLE` with typed, transactional migration helpers | HIGH |
| `sqlite-vec>=0.1.6` + `apsw>=3.46` | Vector search | Required combo on macOS; `loadable_path()` for cross-platform extension loading | HIGH |
| `struct.pack` / `sqlite_vec.serialize_float32` | Embedding serialization | Either works; `serialize_float32` is slightly more idiomatic | HIGH |
| SM-2 (current) or FSRS | Spaced injection scheduling | SM-2 is proven for binary feedback; FSRS for richer stability modeling | MEDIUM |
| `sklearn.TfidfVectorizer` + `cosine_similarity` | Pre-LLM dedup + clustering | Lazy import + graceful degradation; correct for small batches | HIGH |
| `nltk.corpus.stopwords` + `PorterStemmer` | Keyword extraction | Lazy download + `LookupError` fallback is the right pattern | HIGH |
| Python `match`/`case` (3.10+) | Action routing | Not yet adopted; would clean up `if action == "confirmed"` chains in reconsolidation | MEDIUM |

---

## Patterns to Follow

### FTS5 External Content Table (confirmed correct)

Manual sync via `_fts_insert`/`_fts_delete`/`_fts_delete_from_db` in overridden `save()` and `delete_instance()`. The key constraint: bulk `Memory.update(...).execute()` bypasses sync. Currently safe; add a `rebuild_fts()` for post-migration recovery.

### Deferred Singleton Database

`SqliteDatabase(None)` + `db.init(path, pragmas)` is canonical for library/tool code. Never initialize a real database at module import time.

### Feature Flag Guards

`from .flags import get_flag` before optional behavior is a clean, low-overhead toggle system. All new optional behaviors should follow this pattern.

### Embedding Availability Guard

`VecStore.available` as a property checked before any vector operation. `VecStore.__init__` sets `self._available = False` on any failure. This makes the vector subsystem an optional enhancement with no hard runtime dependency.

### Graceful Degradation for Optional Dependencies

All optional imports (`sklearn`, `sqlite_vec`, `boto3`) follow try/except + capability flag or return-early patterns. This is the correct approach for a system that must function in constrained environments.

---

## Pitfalls

1. **apsw connection leak on exception** — Requires `try/finally: conn.close()`. The codebase implements this correctly. Omitting `finally` would leak connections.

2. **FTS desync on bulk writes** — `Memory.update(...).execute()` bypasses `save()` override. Safe today (bulk updates touch non-FTS fields). A footgun if future bulk updates touch `title`, `summary`, `tags`, or `content`.

3. **NLTK `punkt_tab` not downloaded** — `stopwords` is guarded. If `word_tokenize` or `sent_tokenize` is added, `punkt_tab` must also be downloaded separately.

4. **sklearn TF-IDF on empty corpus** — `TfidfVectorizer.fit_transform([])` raises `ValueError`. The codebase wraps in try/except and returns `[]`/`{}`. Correct.

5. **`Memory._pk_exists()` on every save** — One extra `SELECT` per save. For batch reconsolidation, this adds query overhead. Optimize with `.exists()` query.

6. **ISO datetime string comparison** — All timestamps are `TextField`. ISO format string ordering (`"2026-01-01" < "2026-02-01"`) happens to be correct for UTC strings. Fragile if format inconsistencies are introduced (timezone suffixes, etc.).

7. **vec0 metadata columns not used for filtering** — All KNN results fetched then post-filtered in Python (`exclude_ids`). Adding `stage` and `importance` as metadata columns would push filtering into the KNN scan, reducing result set before Python processing.

8. **`rrf_k=60` in two places** — `hybrid_search` default and `_crystallized_hybrid`'s `_RRF_K = 60`. Extract to module-level constant.

9. **Stopword set duplication** — Hardcoded `_STOP_WORDS` in `models.py` and NLTK `stopwords.words('english')` in `feedback.py`/`relevance.py` are not identical. Consolidate into a shared utility.

10. **Titan v2 embedding dimensions** — `DEFAULT_DIMENSIONS = 512` in `embeddings.py`, hardcoded `embedding float[512]` in `VecStore`. These must stay in sync. A shared constant or config value would prevent drift.

---

## Gaps

- **FSRS Python library:** No specific PyPI package (`py-fsrs`, `fsrs`) was verified as actively maintained. Would need direct evaluation before recommending.
- **apsw 3.46 changelog:** readthedocs was blocked by Cloudflare during research. apsw best practices inferred from sqlite-vec docs and codebase inspection.
- **scikit-learn 1.4+ new features:** The 1.4 release introduced `set_output` API and metadata routing. Not relevant to current usage patterns; stable TF-IDF/cosine_similarity APIs verified.
- **NLTK 3.8+ changelog:** No specific new features verified. Usage is stable API surface (`stopwords`, `PorterStemmer`).
- **RRF empirical tuning:** The `rrf_k=60` default has not been empirically evaluated against this codebase's actual memory corpus. A small evaluation over retrieval logs could determine if a different constant improves precision.
