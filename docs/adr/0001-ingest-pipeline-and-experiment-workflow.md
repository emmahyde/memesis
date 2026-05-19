# ADR 0001 — Ingest Pipeline Contract & Experiment Workflow

- **Status:** Proposed
- **Date:** 2026-05-17
- **Supersedes:** —
- **Related:** `docs/superpowers/specs/2026-05-17-contradiction-resolution-design.md`,
  plan `i-would-like-you-rustling-sun` (determinism improvements)

---

## 1. Context

The memesis ingest pipeline turns a Claude Code transcript into durable
memories. Three problems surfaced while iterating on it:

1. **No determinism contract.** Re-running the same transcript yields different
   observation counts, affect scores, and consolidation decisions. There is no
   written statement of what *should* be reproducible.
2. **Experiments mutate the canonical store.** `scripts/ingest_one.py` writes
   straight into `~/.claude/memory/index.db`. Re-ingesting a transcript without
   resetting accumulates duplicate memories and inflates reinforcement counts —
   a warping effect. But isolating experiments in a throwaway copy loses the
   ability to observe how new observations interact with *real* existing
   memories (linking, rc++, contradiction edges).
3. **No token visibility.** The CLI transport runs `claude -p
   --output-format text`, which discards usage data. We cannot benchmark
   input/output token cost per run or watch it improve.

This ADR defines the pipeline behavior contract, the determinism guarantees we
commit to, an experiment workflow that captures DB mutations as a replayable
artifact, token benchmarking, and the test plan that enforces all of it.

---

## 2. The ingest pipeline contract

### 2.1 Stage machine

A memory progresses through four lifecycle stages:

```
ephemeral ──> consolidated ──> crystallized ──> instinctive
```

- **ephemeral** — raw extracted observations, session-bound, not yet curated.
- **consolidated** — observations the curation pass elected to keep, written as
  memories. Most memories live here.
- **crystallized** — consolidated memories that recurred across sessions
  (reinforcement count ≥ 3, spacing satisfied) and were promoted.
- **instinctive** — the top tier; stable, high-confidence behavioral memory.

### 2.2 Pipeline steps (per ingest)

| Step | Module | Behavior |
|------|--------|----------|
| Extract | `core/transcript_ingest.py` | Hierarchical windowed extraction; coverage scales with transcript length. |
| Affect prior | `core/extraction_affect.py` | Deterministic, no-LLM behavioral signal; importance prior. |
| Dedup | `core/transcript_ingest.py` | Word-bag MD5 + (planned) embedding near-dup collapse. |
| Consolidate | `core/consolidator.py` | Semantic-clustered, parallel chunked keep/prune/promote decisions. |
| Link | `core/linking.py` | Cosine-similarity `similar` edges; auto-promote on near-identical. |
| Crystallize | `core/crystallizer.py` | Promote rc≥3 memories that satisfy spacing. |

### 2.3 Expected outcomes

- A long session (thousands of turns) produces tens of observations, not a
  handful — extraction coverage must scale with length.
- Re-ingesting an already-processed transcript must be **idempotent against the
  canonical store** (see §4) — it must not silently double-count.
- Every consolidation chunk sees the same memory manifest; chunk membership
  does not change which existing memories an observation can be merged into.

---

## 3. Determinism guarantees

We separate **"what happened"** (observable, must be reproducible) from
**"what matters"** (judgment, inherently LLM-driven).

**Committed deterministic** — identical output for identical input:

- Affect / friction scoring (`extraction_affect.py`) — lexicon + heuristic, no LLM.
- Window boundaries — fixed char stride / user-anchored segmentation.
- Consolidation chunk assignment — embedding clustering is a pure function of
  observation text (embeddings are deterministic).
- Linking, edge creation, crystallization gates — pure DB + cosine math.

**Inherently non-deterministic** — LLM judgment:

- Stage 1 extraction (which facts to emit).
- Stage 2 consolidation (keep/prune/promote verdicts).

**Mitigation:** the response cache (`core/llm_cache.py`, keyed by
`sha256(model+prompt)`) makes a re-run bit-identical when the transcript and
prompts are unchanged. Determinism is *achieved via caching*, not claimed of the
model itself.

---

## 4. Experiment workflow — changeset capture

**Goal:** run an ingest against real existing memories, observe how new
observations land, but **defer the decision to commit**. LLM calls are the
expensive part; the resulting DB mutation should be a cheap, reusable artifact —
a "data migration" file applied later, so a good run is never reprocessed.

Three mechanisms were considered. Each is steelmanned below.

### Option A — Shadow-copy + diff

Copy `index.db` to a scratch path, ingest into the copy (which contains every
real memory), then diff scratch vs. original to emit `changeset.sql`.

**Steelman.** The canonical store is never opened for writes — the safest
posture; no class of partial-write or corruption bug can touch it. The diff is a
pure function of `(before, after)`, so it is **complete by construction**: you
cannot miss a write, because you compare end states, not operations.
Implementation is trivial — `shutil.copy`, `init_db(base_dir=scratch)`, run
ingest *unmodified*, generic table diff. No write-path instrumentation, no
transaction handling, no new dependency. Output is a plain, human-readable
`.sql` file — exactly the "data migration" artifact requested — reviewable and
replayable with `sqlite3 index.db < changeset.sql`. Works uniformly for every
table including BLOB embeddings. Ingest code stays completely decoupled from the
capture machinery.

**Honest weaknesses.** Copies the DB each run (cheap today — `index.db` is
small — but grows with the store). Captures *net effect* only, not operation
order or intermediate history. The diff routine must understand every table's
primary key.

### Option B — Transaction-wrap + write recorder

Ingest the real store inside one transaction; intercept `db.execute_sql` to
record every write statement; `ROLLBACK` at the end unless `--apply`.

**Steelman.** Captures the *actual statements the real code path executed, in
order* — the changeset is a faithful recording, not a reconstruction. No DB
copy: zero I/O overhead, no scratch-file management. Because CLAUDE.md Rule 1
funnels every write through the single Peewee `db` singleton, **one**
interception point catches everything; the recorder is ~20 lines. A single
transaction means ingest sees its own uncommitted writes (linking reads
memories created earlier in the same run), so behavior is byte-identical to a
real run; `ROLLBACK` leaves the store untouched, `COMMIT` applies it. You get a
true operation log — valuable for debugging *how* ingest behaved.

**Honest weaknesses.** Instrumenting the write path is invasive and must stay
correct as code evolves; any write that bypasses `db.execute_sql` is missed
silently. Faithfully capturing bound parameters — especially BLOBs — is fiddly.
A long-held transaction holds a write lock: fine for a solo experiment, bad if
the hourly cron runs concurrently.

### Option C — apsw session extension

Use SQLite's purpose-built session extension (via the `apsw` driver) to record
an invertible changeset blob.

**Steelman.** This is the feature SQLite built for exactly this need. It records
changes at the storage-engine level — it *cannot* miss a write, misorder, or
mishandle a type. Changesets are **invertible**: a one-call exact undo, so
"elect to apply / elect to revert" is free and precise. They can be **rebased**
and conflict-resolved against a store that moved on since capture — a changeset
captured today still applies cleanly next week even though the cron added
memories meanwhile. It is the only option that is both correct by construction
*and* robust to concurrent store evolution.

**Honest weaknesses.** Stdlib `sqlite3` does not expose it — requires `apsw`,
and the codebase is Peewee-on-stdlib-sqlite3, so apsw becomes a parallel access
path in tension with CLAUDE.md Rule 1's single-connection mandate. The changeset
is a binary blob, not the human-readable `.sql` migration file requested — a
separate render step would be needed. Steepest maintenance curve.

### Recommendation

**Option A.** It delivers the exact artifact requested (a reviewable `.sql`
file), never risks the canonical store, adds no dependency, keeps ingest code
untouched, and is complete by construction. B's operation-log benefit is real
but does not justify instrumenting the write path when A's end-state diff misses
nothing. Revisit **C** only if changeset-against-a-moved-store rebasing becomes a
genuine recurring need.

### Workflow

```
ingest_one.py <transcript> --capture changeset.sql   # store untouched, .sql emitted
sqlite3 ~/.claude/memory/index.db < changeset.sql    # elect to apply later
```

Default (no `--capture`) keeps today's behavior: ingest commits to the canonical
store directly.

---

## 5. Token I/O benchmarking

- Switch the CLI transport (`core/llm.py` `_call_via_claude_cli`) from
  `--output-format text` to `--output-format json`; parse the `usage` block:
  `input_tokens`, `output_tokens`, `cache_read_input_tokens`.
- Accumulate per-call counts into a per-run total.
- Append one record per ingest run to a benchmark log
  (`~/.claude/memesis/benchmarks/ingest-tokens.jsonl`):

  ```json
  {"ts": "...", "transcript": "5143c9fd", "tokens_in": N, "tokens_out": N,
   "cache_read_input_tokens": N, "wallclock_s": N, "n_windows": N, "raw_obs": N,
   "deduped_obs": N, "kept": N, "pruned": N, "promoted": N}
  ```

- The log is the benchmark: every run is comparable, regressions are visible,
  and "until it's perfect" has a concrete measure.

---

## 6. Test plan

### 6.1 Unit

| Area | Test file | Asserts |
|------|-----------|---------|
| Affect / friction | `tests/test_extraction_affect.py` | scope-reduction + retry signals |
| Intent | `tests/test_intent.py` | per-turn classification markers |
| Clustering | `tests/test_clustering.py` | union-find groups; chunk balancing |
| Consolidation chunking | `tests/test_consolidator.py` | near-dups co-chunk; oversized split |
| Response cache | `tests/test_llm_cache.py` | batch hit/miss order; `force_live` |
| Linking edges | `tests/test_linking.py` | bidirectional `similar` edges emitted |

### 6.2 Integration

- A **tiny fixture transcript** (10–20 turns) ingested end-to-end in <60 s.
  This — not a 4000-turn transcript — is the loop for validating pipeline code.

### 6.3 Determinism test

- Ingest the fixture twice with the response cache on; assert identical
  `raw_obs`, `deduped_obs`, and consolidated counts.

### 6.4 Changeset test

- Capture `changeset.sql` from a fixture ingest; apply it to a fresh copy of the
  pre-ingest DB; assert the result matches a direct ingest of the same fixture.

### 6.5 Regression gates

- Full suite green (`uv run python -m pytest tests/ -q`).
- Eval recall not regressed (`uv run python -m pytest eval/`).

---

## 7. Open questions

1. **Was `5143c9fd` already cron-consumed** before the manual `ingest_one` run?
   Resolve by checking `~/.claude/memesis/cursors.db` for that transcript path.
   If yes, its 33 memories are content-duplicates and should be merged/removed.
2. **Stray rows:** `d104c2a4` left 2 `consolidation_log` rows from a killed run
   (0 memories). Harmless; clean up opportunistically.
3. Whether the response cache should be **on by default** for `ingest_one`
   (determinism + cost) or opt-in (freshness). Currently planned opt-in.

---

## 8. Decision log

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Determinism via response caching, not model claims | LLM judgment is irreducibly non-deterministic. |
| 2 | Changeset capture = Option A (shadow-copy + diff) | Safest, simplest, exact `.sql` artifact, no new dep. |
| 3 | Token capture via `--output-format json` | Only transport-level source of true usage data. |
| 4 | Tiny fixture transcript is the code-iteration loop | 40-min full ingests are not a feedback loop. |
