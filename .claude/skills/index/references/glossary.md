# Glossary — Algorithms, Packages, Concepts, Structures, Patterns

All terms used in the memesis codebase and dev skills, with precise definitions and where they appear.

---

## Algorithms

### Ebbinghaus Forgetting Curve
Exponential model of memory decay: R = e^(-t/S) where t is elapsed time and S is stability. Used in `core/observability.py` activation formula. Reference: Zhong et al. 2023 (MemoryBank, arXiv 2305.10250).

### SM-2 (Spaced Repetition)
Algorithm for scheduling memory injections with increasing intervals. Fields: `injection_ease_factor` (default 2.5) and `injection_interval_days` (default 1.0) in `Memory`. Controls `next_injection_due`. From Wozniak 1990. Implemented in `core/spaced.py`.

### Activation Formula (`core/observability.py`)
```
activation = importance × exp(-age_hrs / τ) × (1 + log(1 + access_count))
```
- **τ** — time constant (NOT half-life; half-life = τ × ln(2))
- **Sub-linear access boost** — prevents Matthew Effect (rich-get-richer runaway)
- NOT ACT-R base-level activation — do not conflate them

### Cosine Similarity
Normalized dot product measuring semantic angle between embedding vectors. Used in `core/linking.py`. Score range [0, 1]; higher = more similar. Threshold: 0.72 for linking, 0.85 for auto-promote dedup.

### FTS5 (Full-Text Search 5)
SQLite built-in full-text search engine. Used in `Memory.search_fts()`. Returns negative rank values (closer to 0 = better match). Normalized: `score = 1.0 / (1.0 + abs(rank))`. Gotcha: the SQL splitter in migrations treats `;` as a statement boundary — never use `;` inside `--` comments in migration SQL.

### L2 Distance (Euclidean)
Distance metric used by sqlite-vec for vector search. Lower = more similar. Normalized to [0,1]: `score = 1.0 / (1.0 + distance)`. Contrast with cosine similarity (angle-based, not magnitude-based).

### Levels-of-Processing (Craik & Lockhart 1972)
Cognitive model: deeper encoding produces more durable memories. Memesis consolidation performs "elaborative curation" analogous to this — gating, enrichment, linking. Cited in `core/consolidator.py` docstring. The term "consolidation" in the codebase is historical; the process is closer to elaborative curation.

### Tulving's Episodic-to-Semantic Transition
Stage 1 (ephemeral buffer) captures temporally-tagged session-bound observations (episodic memory). Stage 2 (consolidation) elaborates them toward context-free knowledge (semantic memory). Reference: Tulving 1972, 1985. Cited in `core/consolidator.py` docstring.

### Spreading Activation (Collins & Loftus 1975)
Propagation through semantic networks to neighboring nodes. In memesis, this refers specifically to graph traversal along `linked_observation_ids[]` edges. **Not** the same as incrementing an access count (which is recency reinforcement). The function for access reinforcement is named `recency_reinforcement()` per panel NS-F8.

---

## Packages & Libraries

### Peewee
Python ORM for SQLite. Manages the deferred `SqliteDatabase(None)` singleton (`db` in `core/models.py`). Bound at runtime via `init_db()`. WAL mode and `busy_timeout=5000` are configured by the singleton — bypassing it with raw `sqlite3.connect()` creates concurrent-writer races.

### sqlite-vec
SQLite extension for vector similarity search. Stores float32 embeddings as blob columns. Returns L2 distance. Access via `get_vec_store()` from `core/database.py`. Falls back gracefully if unavailable.

### FTS5 (SQLite built-in)
See FTS5 above. Accessed via `Memory.search_fts()`. FTS5 virtual table is maintained in sync with `Memory` table via model hooks.

### bge-small-en-v1.5
The embedding model used for semantic vectors. 384-dimensional float32 output. Calibration note: cosine similarity threshold (0.72) was set for this model — do not reuse thresholds calibrated for other models (e.g., Titan at 1024d used 0.90).

### uv
Python package manager and virtual environment tool. Required for running any Python in this project: `uv run python3`, `uv run pytest`. Reads `uv.lock` for exact dependency pinning. Never use bare `python3` — it resolves to the system interpreter and bypasses locked deps.

### pytest
Test runner. Always invoke as `uv run pytest tests/`. Eval harness: `uv run pytest eval/`.

### pydantic
Data validation for LLM response schemas. `core/schemas.py` defines `ConsolidationDecision` and related schemas. LLM responses are parsed and validated against these before being acted on.

---

## Concepts

### Memory Lifecycle / Stage Progression
The four stages a memory moves through: `ephemeral → consolidated → crystallized → instinctive`. Each stage has promotion rules and distinct injection behavior. Managed by `core/lifecycle.py`.

### Shadow Mode / SHADOW_ONLY
`SHADOW_ONLY=True` in `core/observability.py`. When true, pruning logic computes what would be deleted and logs it to `shadow-prune.jsonl` but performs NO database mutations. This is a 30-day dry-run (Decision C3) to collect false-prune rates before enabling live deletion.

### Significance Filter
LLM gate in the consolidation pipeline that decides whether an observation is worth storing. Defined by the `CONSOLIDATION_PROMPT` in `core/prompts.py`. Common failure mode: filtering out preference signals that should be kept.

### Elaborative Curation
The actual function of `core/consolidator.py`: gating (keep/prune), enrichment (adding metadata), and linking (finding related memories). Historically called "consolidation" — the term is maintained for code stability but the process is not biological memory consolidation.

### Deduplication / Auto-Promote
When a newly kept memory has cosine similarity > 0.85 with an existing memory, the existing memory's `reinforcement_count` is incremented and the new memory is archived (not kept). Implemented in `core/linking.py::auto_promote_if_dupe`.

### Rehydration
Process of restoring an archived memory to active status when its relevance score in the current project context exceeds `REHYDRATE_THRESHOLD (0.30)`. Run at SessionStart via `RelevanceEngine.rehydrate_for_context()`.

### Hypotheses (`kind='hypothesis'`)
LLM-inferred behavioral patterns from the self-reflection engine. Held in `ephemeral` stage until promotion gate passes: `evidence_count >= 3`, `distinct_sessions >= 2`, no contradictions. Managed by `/memesis:hypotheses` and `core/self_reflection.py`.

### Instinctive Layer
The highest memory stage. Seeded at first run with self-model and observation habits. Always injected at session start with zero overhead. Kept small (< 3 memories typical) to avoid context bloat.

### Pending-Delete
Intermediate stage (`stage='pending_delete'`) for memories scheduled for deletion by the consolidator. Remain in DB until TTL expires (default 7 days, env: `MEMESIS_PENDING_DELETE_TTL_DAYS`) or hard-deleted with `--confirm`. Prevents accidental data loss.

### Temporal Knowledge / Open Questions
`kind='open_question'` memories track unresolved questions. Fields: `resolves_question_id`, `resolved_at`, `is_pinned`. Managed by `core/question_lifecycle.py`.

### Cognitive Modules (RISK-11)
Seven modules that contribute scores to retrieval ranking: `affect`, `coherence`, `habituation`, `orienting`, `replay`, `self_reflection`, `somatic`. Non-experimental by default; `self_reflection` is experimental (opt-in via `MEMESIS_EXPERIMENTAL_MODULES`). Implemented as individual `core/<module>.py` files.

### Progressive Disclosure (Retrieval)
Three-layer retrieval pattern: (1) index layer — ranked summaries (~50-100 tokens each), (2) hydration layer — full details for selected entries (~500-1000 tokens), (3) full-context escape hatch — raw transcript. Prevents context bloat from flat injection.

### Three-Tier Injection
The retrieval architecture at session start:
- **Tier 1** (instinctive): always injected
- **Tier 2** (crystallized): context-matched, token-budgeted
- **Tier 3** (active search): agent-initiated FTS + vec

### Memory Poisoning (MemoryGraft)
Adversarial attack where untrusted input plants memories that surface later by semantic similarity. Mitigated by the `source` field (`human` / `agent`) on Memory rows. See E3 in agentic-memory blockers.

### Matthew Effect
"Rich get richer" dynamic: a memory used often gets a high access_count, boosting its future retrieval score, further increasing usage. Mitigated by sub-linear access boost in the activation formula: `1 + log(1 + access_count)`.

---

## Data Structures

### Observation (table: `observations`)
Raw extracted text from ephemeral session buffer before consolidation decision. Contains ordinal (0-indexed), content, status (`filtered` / null), and `memory_id` if kept. Ordinal mismatch: 0-indexed in DB, 1-indexed in LLM prompts.

### ConsolidationLog (table: `consolidation_log`)
Immutable audit trail. Every keep/prune/merge/promote/demote action writes a row with action, from_stage, to_stage, rationale, llm_response, and token counts.

### MemoryEdge (table: `memory_edges`)
Typed semantic link between two Memory rows. Types: `contradicts`, `supports`, `specializes`, `generalizes`. Used by hypothesis gate (contradicts check) and retrieval graph traversal.

### NarrativeThread (table: `narrative_threads`)
Thematic thread grouping related memories. Members tracked in `ThreadMember` table. Managed by `core/threads.py`.

### RetrievalLog / RetrievalCandidate (tables)
Per-session injection scoring details. `RetrievalCandidate` stores per-memory scores: `fts_rank`, `vector_rank`, `semantic_score`, `recency_score`, `importance_score`, `affect_score`, `reinforcement_score`, `boost_score`.

---

## Patterns

### Deferred Database Singleton
`db = SqliteDatabase(None)` in `core/models.py`. Bound at runtime via `init_db()`. All models use this singleton — never instantiate a separate connection.

### Flag-File Cursor
Used by cron-based transcript ingest: a JSON file per session stores the last byte offset of the processed transcript. On next run, reads from the offset forward. Enables incremental processing without reprocessing the entire transcript.

### Two-Phase Delete (Pending-Delete)
Consolidator marks pruned memories `stage='pending_delete'` rather than hard-deleting immediately. A separate TTL process hard-deletes after `MEMESIS_PENDING_DELETE_TTL_DAYS`. Prevents accidental data loss from aggressive LLM pruning.

### Atomic Write (tempfile + shutil.move)
For any file-backed persistence: write to `tempfile.mkstemp`, then `shutil.move` to destination. Prevents partial writes from being seen by concurrent readers.

### Significance Filter (LLM Gate)
Pre-storage gate: LLM evaluates each observation for durability, novelty, and signal quality before it is written to the memory store. Defined in `CONSOLIDATION_PROMPT`. The key filter failure mode is treating explicit preference signals the same as incidental observations.
