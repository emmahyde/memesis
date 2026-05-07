# Plan: Risk Register Fixes Batch

**Source:** .context/CONTEXT-risk-register-fixes.md
**Generated:** 2026-05-07
**Status:** Ready for execution

## Overview

Implements the 9 implementable risk items identified in the 2026-05-07 canvas review: migration runner, embedding metadata, Pydantic schemas, DB transaction safety, asyncio isolation, cognitive module audit, self-reflection gates, stdout discipline, and injection_count decoupling. Foundational data-layer work (RISK-10 runner, RISK-02 schema) ships first; consumer integrations follow in later waves.

**Waves:** 4
**Total tasks:** 9

---

## Wave 1: Foundations — No consumer dependencies

**Prerequisite:** None (first wave)

### Task 1.1: Migration runner (RISK-10)

- **Files owned:**
  - `core/migrations/__init__.py` (new)
  - `core/migrations/sql/` (new directory + initial seed file if needed)
  - `core/database.py` (add migration runner call in `init_db()` only — no connection factory yet)
  - `tests/test_migrations.py` (new)
- **Depends on:** None
- **Decisions:** RISK-10
- **Acceptance criteria:**
  - [ ] `core/migrations/__init__.py` exports a `run_migrations(conn)` function that scans `core/migrations/sql/` for `*.sql` and `*.py` migration files, applies them in lexicographic (timestamp-prefix) order, records each in `schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMP)`.
  - [ ] `.py` migration files must export `up(conn)` callable; used for multi-step transforms (e.g., `consolidation_log` CHECK constraint rebuild) where raw SQL is insufficient.
  - [ ] `playhouse.migrate` is available as a helper for ALTER-heavy migrations; runner supports it but does not require it.
  - [ ] `init_db()` in `core/database.py` calls `run_migrations()` after pragma setup and before returning.
  - [ ] Seeding: on first run, if `PRAGMA user_version >= 2`, all migration files are marked as already-applied in `schema_migrations` without executing their SQL — no re-running of ALTERs that succeeded in the old codepath.
  - [ ] Idempotency: applying all migrations a second time is a no-op (already-applied versions are skipped).
  - [ ] Tests cover: forward run from empty DB, forward run from seeded `user_version=2` DB (seeding path), idempotency (apply twice = no-op).
  - [ ] Existing `try/except ALTER TABLE` migrations in `core/database.py` are converted to migration files; the old inline code is removed.

### Task 1.2: Pydantic schemas for consolidation LLM output (RISK-02, schemas only)

- **Files owned:**
  - `core/schemas.py` (new)
  - `tests/test_schemas.py` (new)
- **Depends on:** None
- **Decisions:** RISK-02
- **Acceptance criteria:**
  - [ ] `core/schemas.py` exports Pydantic models covering all LLM consolidation output shapes.
  - [ ] Validation rules enforced at parse time: `action ∈ {keep, update, merge, archive, prune, promote}`; stage transitions restricted to `ephemeral→consolidated`, `consolidated→crystallized`, `crystallized→instinctive`, `*→pending_delete` — any other rejected with `ValidationError`; `importance ∈ [0.0, 1.0]`; destructive actions (`prune`, `archive`) require non-empty `rationale`.
  - [ ] Memory ID validation: IDs are syntactically valid UUID4 strings (DB existence check is deferred to the consumer in Wave 3).
  - [ ] No Peewee imports in `core/schemas.py` — Pydantic only. No storage logic.
  - [ ] Tests cover: invalid action rejected, bad stage transition rejected, out-of-range importance rejected, destructive action without rationale rejected, valid payload parses cleanly.

### Task 1.3: Stdout discipline in hooks (RISK-14)

- **Files owned:**
  - `hooks/pre_compact.py`
  - `hooks/session_start.py`
  - `hooks/user_prompt_inject.py`
  - `hooks/append_observation.py`
  - `hooks/consolidate_cron.py`
  - `tests/test_hooks.py` (extend existing)
- **Depends on:** None
- **Decisions:** RISK-14
- **Acceptance criteria:**
  - [ ] A `_hook_safe_print(data)` helper (or equivalent module-level function) is added; all `print(...)` calls in hook entry points are replaced with it.
  - [ ] `_hook_safe_print` routes non-injection-JSON output to `sys.stderr`; only valid hook JSON (or empty) reaches `sys.stdout`.
  - [ ] Every hook entry point is wrapped in `try/except`; any unhandled exception routes its message to `sys.stderr` and does not write to `sys.stdout`.
  - [ ] Integration tests: capture stdout for each hook entry; assert stdout is either empty or parses as valid hook JSON. Capture stderr; assert error output lands there, not stdout.

### Task 1.4: Cognitive module audit and instrumentation (RISK-11)

- **Files owned:**
  - `docs/cognitive-modules.md` (new)
  - `core/affect.py`
  - `core/coherence.py`
  - `core/habituation.py`
  - `core/orienting.py`
  - `core/somatic.py`
  - `core/replay.py`
  - `core/self_reflection.py` (experimental flag scaffold only — writer gate is Wave 2/3)
  - `core/retrieval.py` (add `module_scores` to retrieval output)
  - `skills/stats/SKILL.md` (document new module breakdown section)
  - `tests/test_cognitive_modules.py` (new)
- **Depends on:** None
- **Decisions:** RISK-11
- **Note:** This is the largest Wave 1 task. If agent session budget is insufficient, the team lead may split into 1.4a (doc + flag scaffolding) and 1.4b (per-module instrumentation + retrieval + stats). Both would still be Wave 1 with non-overlapping file ownership.
- **Acceptance criteria:**
  - [ ] `docs/cognitive-modules.md` documents all 7 modules (`affect`, `coherence`, `habituation`, `orienting`, `self_reflection`, `somatic`, `replay`): inputs, outputs, scoring formula, validation status, one section per module plus a summary table.
  - [ ] Each module has an `experimental: bool` attribute or module-level constant. Experimental modules are excluded from default scoring; opt-in via env var `MEMESIS_EXPERIMENTAL_MODULES=<comma-list>`.
  - [ ] Retrieval output structs in `core/retrieval.py` include `module_scores: dict[str, float]` alongside the existing aggregated `score`. The already-aggregated `score` is preserved unchanged.
  - [ ] `skills/stats/SKILL.md` documents the new module breakdown section: memory counts scored per module, mean contribution, experimental status.
  - [ ] Tests: experimental module excluded from default scoring; env var opt-in enables it; `module_scores` key present in retrieval output.

**Wave 1 status:** pending

---

## Wave 2: Schema migrations + isolated changes

**Prerequisite:** Wave 1 complete (Task 1.1 migration runner must be in place before any migration files are added)

### Task 2.1: Embedding metadata and dimension validation (RISK-06)

- **Files owned:**
  - `core/vec.py`
  - `core/embeddings.py`
  - `core/migrations/sql/20260507000001_embedding_metadata.sql` (new migration file)
  - `skills/reindex/SKILL.md` (new skill definition for `memesis reindex --vec`)
  - `tests/test_vec.py` (extend existing)
- **Depends on:** Task 1.1 (migration runner must exist to register new migration file)
- **Decisions:** RISK-06
- **Acceptance criteria:**
  - [ ] Migration adds `embedding_model TEXT NOT NULL DEFAULT ''`, `embedding_version TEXT NOT NULL DEFAULT ''`, `embedding_dim INTEGER NOT NULL DEFAULT 0` to vec records table.
  - [ ] New `_system` table created by migration: `(key TEXT PRIMARY KEY, value TEXT)`. Stores active `embedding_model`, `embedding_dim`, `embedding_version` on startup.
  - [ ] `VecStore.store_embedding()` validates that the embedding byte length matches `embedding_dim * 4` (float32); raises `ValueError` on mismatch — no silent garbage.
  - [ ] Backfill: existing vec rows with blank `embedding_model`/`embedding_version`/`embedding_dim` are updated from current default model constants in `core/embeddings.py`.
  - [ ] `skills/reindex/SKILL.md` documents `memesis reindex --vec`: re-embeds all memories under active model, atomic swap.
  - [ ] Tests: insert with mismatched dim raises `ValueError`; reindex is idempotent; metadata round-trips.

### Task 2.2: Self-reflection hypothesis schema and writer gate (RISK-12, schema + writer side)

- **Files owned:**
  - `core/models.py` (add `kind`, `evidence_count`, `evidence_session_ids` columns)
  - `core/self_reflection.py` (writer gate: set `kind=hypothesis`, `evidence_count=1` on initial write; accumulate evidence)
  - `core/migrations/sql/20260507000002_hypothesis_schema.sql` (new migration file)
  - `tests/test_self_reflection.py` (extend existing)
- **Depends on:** Task 1.1 (migration runner), Task 1.4 (experimental flag scaffold must exist in `core/self_reflection.py` before writer gate is added)
- **Decisions:** RISK-12
- **Acceptance criteria:**
  - [ ] Migration adds `kind TEXT DEFAULT NULL`, `evidence_count INTEGER DEFAULT 0`, `evidence_session_ids TEXT DEFAULT '[]'` (JSON array) to `memories` table.
  - [ ] Self-reflection writer always sets `kind='hypothesis'`, `evidence_count=1` on initial write for inferred content.
  - [ ] Explicit user statements may have `evidence_count=1` and promote on demand (no multi-evidence gate).
  - [ ] Inferred hypotheses accumulate `evidence_count` and `evidence_session_ids` across sessions — writer appends to the JSON array.
  - [ ] Tests: new memory has correct `kind`, `evidence_count`; evidence accumulates across sessions.

### Task 2.3: Decouple injection_count from importance (RISK-09)

- **Files owned:**
  - `core/feedback.py`
  - `core/relevance.py`
  - `eval/injection_correlation_test.py` (new)
  - `tests/test_feedback.py` (extend existing)
- **Depends on:** None (no schema change required; pure formula change + eval task)
- **Decisions:** RISK-09
- **Acceptance criteria:**
  - [ ] `injection_count` term removed from the importance scoring formula in `core/feedback.py` and/or `core/relevance.py` (wherever `injection_count` currently contributes to importance).
  - [ ] `injection_count` is still logged separately in retrieval output for eval correlation — not removed from data model.
  - [ ] `eval/injection_correlation_test.py` computes correlation between `injection_count` and `kept_after_consolidation` (or `promoted`) as the "useful" signal. Documents the TBD metric choice with a comment.
  - [ ] Tests: importance score is unchanged when `injection_count` varies (confirms decoupling).

**Wave 2 status:** pending

---

## Wave 3: Consolidator integration

**Prerequisite:** Wave 2 complete (Pydantic schemas from Task 1.2 must exist; hypothesis schema from Task 2.2 must exist)

### Task 3.1: Pydantic consumer wiring + asyncio isolation (RISK-02 consumer + RISK-08)

- **Files owned:**
  - `core/consolidator.py`
  - `skills/forget/SKILL.md` (extend with `--confirm` flag for two-phase delete)
  - `tests/test_consolidator.py` (extend existing)
- **Depends on:** Task 1.2 (Pydantic schemas), Task 2.2 (hypothesis schema — `pending_delete` stage must exist in DB)
- **Decisions:** RISK-02, RISK-08
- **Acceptance criteria:**
  - [ ] `core/consolidator.py` imports and uses `core.schemas` to parse LLM consolidation output. Raw JSON dict access is replaced by Pydantic model access.
  - [ ] Memory ID validation in `core/schemas.py` is extended at parse time in the consolidator to check DB existence (the `core/schemas.py` module itself stays DB-free — the consolidator performs the existence check post-parse).
  - [ ] Two-phase apply: on `prune`/`archive` decisions, consolidator writes `pending_delete` stage first. Hard delete is gated behind TTL (default 7 days, configurable) or explicit `memesis forget --confirm <id>` (extends `skills/forget/SKILL.md`).
  - [ ] `asyncio.gather` in batch consolidation loop replaced with `asyncio.gather(..., return_exceptions=True)`. Per-item exceptions are logged and the batch continues.
  - [ ] `asyncio.Semaphore(3)` caps concurrent LLM subprocess calls.
  - [ ] Idempotency keys: hash `(memory_id, prompt_version, model)` so retried items do not duplicate writes.
  - [ ] Write-site discipline preserved: Kensinger bump (`+0.05` for `affect_valence == "friction"`) stays at its single site in `_execute_keep()`. `card.importance` trust is not regressed.
  - [ ] Tests: invalid action raises `ValidationError` (not silent); `pending_delete` stage written on destructive action; one item raising in `gather` does not abort batch; semaphore bound respected; retry with same idempotency key is no-op.

### Task 3.2: Hypothesis promotion gate and CLI (RISK-12, promotion + CLI)

- **Files owned:**
  - `core/self_reflection.py` (promotion checker: gate function `can_promote_hypothesis(memory)`)
  - `skills/hypotheses/SKILL.md` (new skill)
  - `tests/test_self_reflection.py` (extend, new gate tests)
- **Depends on:** Task 2.2 (hypothesis schema columns must exist; writer gate state must be readable)
- **Decisions:** RISK-12
- **Acceptance criteria:**
  - [ ] `can_promote_hypothesis(memory)` returns `True` only if `evidence_count >= 3`, `len(evidence_session_ids) >= 2` distinct sessions, and no contradicting memory exists.
  - [ ] Explicit user statements with `evidence_count=1` promote on demand (gate does not apply to them).
  - [ ] Promotion checker is called during consolidation cycle: passing hypotheses have `kind` set to `None` and `stage` bumped.
  - [ ] `skills/hypotheses/SKILL.md` documents `memesis hypotheses`: lists pending hypotheses with evidence count; supports promote/reject/edit actions.
  - [ ] Tests: gate enforced (< 3 evidence rejected); multi-session requirement enforced; contradiction blocks promotion; happy-path promotion demotes `kind` to null and bumps stage.
  - [ ] `can_promote_hypothesis` function signature is stable before Task 3.1 ships (Task 3.1 may import it if consolidator calls the gate — coordinate if needed, but Task 3.2 owns the definition).

**Wave 3 status:** pending

---

## Wave 4: Transaction audit and connection factory

**Prerequisite:** Wave 3 complete (consolidator changes from Task 3.1 must be stable before txn boundaries are refactored on top of them)

### Task 4.1: DB transaction audit and connection factory (RISK-07)

- **Files owned:**
  - `core/database.py` (add connection factory with `busy_timeout=5000` on every connection)
  - `core/consolidator.py` (refactor txn boundaries: read→close txn→LLM call→write in new txn)
  - `core/transcript_ingest.py` (same txn boundary refactor)
  - `core/session_vec.py` (same txn boundary refactor)
  - `core/vec.py` (apply connection factory to apsw connections — `setbusytimeout(5000)`)
  - `tests/test_database.py` (extend or create)
  - `tests/test_consolidator.py` (extend — concurrency test)
- **Depends on:** Task 3.1 (consolidator must be in its Wave 3 state before txn boundaries are moved)
- **Decisions:** RISK-07
- **Acceptance criteria:**
  - [ ] A `make_connection(db_path)` factory function in `core/database.py` sets `busy_timeout=5000` on every new connection (both Peewee and apsw paths).
  - [ ] All `core/vec.py` apsw connections call `conn.setbusytimeout(5000)` — the existing 0ms default is eliminated.
  - [ ] `core/consolidator.py`, `core/transcript_ingest.py`, `core/session_vec.py`: any transaction that previously spanned an LLM call is split — reads complete and txn closes before the LLM call, then a new txn opens for writes.
  - [ ] Existing inline `PRAGMA busy_timeout` call in `init_db()` remains but connection factory is now the canonical path for all new connections.
  - [ ] Write-site discipline in `core/consolidator.py` is preserved: Kensinger bump and `card.importance` trust at lines ~524-538 must not be disturbed by the txn refactor.
  - [ ] Tests: concurrent write contention with sleep-mocked LLM (simulates LLM latency while a txn is theoretically open) does not deadlock. Test uses `threading.Thread` + mock `call_llm` with `time.sleep(0.1)`.

**Wave 4 status:** pending

---

## File Ownership Map

| File | Owner |
| --- | --- |
| `core/migrations/__init__.py` | Task 1.1 |
| `core/migrations/sql/` (directory) | Task 1.1 |
| `core/migrations/sql/20260507000001_embedding_metadata.sql` | Task 2.1 |
| `core/migrations/sql/20260507000002_hypothesis_schema.sql` | Task 2.2 |
| `core/database.py` | Task 1.1 (runner call), Task 4.1 (connection factory) |
| `core/schemas.py` | Task 1.2 |
| `hooks/pre_compact.py` | Task 1.3 |
| `hooks/session_start.py` | Task 1.3 |
| `hooks/user_prompt_inject.py` | Task 1.3 |
| `hooks/append_observation.py` | Task 1.3 |
| `hooks/consolidate_cron.py` | Task 1.3 |
| `docs/cognitive-modules.md` | Task 1.4 |
| `core/affect.py` | Task 1.4 |
| `core/coherence.py` | Task 1.4 |
| `core/habituation.py` | Task 1.4 |
| `core/orienting.py` | Task 1.4 |
| `core/somatic.py` | Task 1.4 |
| `core/replay.py` | Task 1.4 |
| `core/retrieval.py` | Task 1.4 |
| `skills/stats/SKILL.md` | Task 1.4 |
| `core/vec.py` | Task 2.1 (metadata + validation), Task 4.1 (connection factory) |
| `core/embeddings.py` | Task 2.1 |
| `skills/reindex/SKILL.md` | Task 2.1 |
| `core/models.py` | Task 2.2 |
| `core/self_reflection.py` | Task 1.4 (flag scaffold), Task 2.2 (writer gate), Task 3.2 (promotion gate) |
| `core/feedback.py` | Task 2.3 |
| `core/relevance.py` | Task 2.3 |
| `eval/injection_correlation_test.py` | Task 2.3 |
| `core/consolidator.py` | Task 3.1, Task 4.1 |
| `skills/forget/SKILL.md` | Task 3.1 (extend with `--confirm` flag) |
| `skills/hypotheses/SKILL.md` | Task 3.2 |
| `core/session_vec.py` | Task 4.1 |
| `core/transcript_ingest.py` | Task 4.1 |
| `tests/test_migrations.py` | Task 1.1 |
| `tests/test_schemas.py` | Task 1.2 |
| `tests/test_hooks.py` | Task 1.3 |
| `tests/test_cognitive_modules.py` | Task 1.4 |
| `tests/test_vec.py` | Task 2.1 |
| `tests/test_self_reflection.py` | Task 2.2, Task 3.2 |
| `tests/test_feedback.py` | Task 2.3 |
| `tests/test_database.py` | Task 4.1 |
| `tests/test_consolidator.py` | Task 3.1, Task 4.1 |

---

## Cross-Wave Ownership Handoffs

Files owned by different tasks across waves. The team lead MUST ensure the earlier wave is complete before the later wave's task touches the file.

| File | Wave N Owner | Wave M Owner | Handoff Notes |
| --- | --- | --- | --- |
| `core/database.py` | Task 1.1 — adds `run_migrations()` call in `init_db()` | Task 4.1 — adds `make_connection()` factory, applies busy_timeout universally | 4.1 must read 1.1's migration-call placement; factory must not remove or reorder the migration call |
| `core/vec.py` | Task 2.1 — adds embedding metadata columns, dimension validation, backfill | Task 4.1 — applies connection factory to all apsw connections (`setbusytimeout(5000)`) | 4.1 must preserve 2.1's `store_embedding` dim validation; factory addition is additive |
| `core/self_reflection.py` | Task 1.4 — adds `experimental` flag scaffold, env-var gating | Task 2.2 — adds writer gate (`kind=hypothesis`, evidence accumulation) | 2.2 builds on 1.4's flag scaffold; must not remove it |
| `core/self_reflection.py` | Task 2.2 — writer gate state (`kind=hypothesis`, evidence accumulation) | Task 3.2 — adds `can_promote_hypothesis()` gate function and promotion logic | 3.2 reads 2.2's evidence accumulation before implementing gate thresholds; assumes Task 2.2's schema columns exist |
| `core/consolidator.py` | Task 3.1 — Pydantic consumer wiring, two-phase apply, asyncio isolation, idempotency keys | Task 4.1 — txn boundary refactor (read→close txn→LLM→write in new txn) | 4.1 MUST preserve write-site discipline at `_execute_keep()` lines ~524-538: Kensinger +0.05 bump, `card.importance` trust, and Pydantic validation introduced by 3.1 must all survive the txn refactor |
| `tests/test_self_reflection.py` | Task 2.2 — writer gate tests | Task 3.2 — promotion gate tests | 3.2 extends, does not replace; existing 2.2 tests must remain green |
| `tests/test_consolidator.py` | Task 3.1 — Pydantic + asyncio tests | Task 4.1 — concurrency / no-deadlock tests | 4.1 extends; 3.1 tests must remain green |

**Handoff protocol:** When a file appears here, the later task's implementer MUST:

1. Read the file as modified by the earlier task (not the original)
2. Build on those changes, not revert them
3. If the earlier task's changes conflict with the later task's needs, escalate to team lead

---

## Decision Traceability

| Decision | Tasks |
| --- | --- |
| RISK-10 | Task 1.1 |
| RISK-02 | Task 1.2 (schemas), Task 3.1 (consumer wiring) |
| RISK-14 | Task 1.3 |
| RISK-11 | Task 1.4 |
| RISK-06 | Task 2.1 |
| RISK-12 | Task 2.2 (schema + writer), Task 3.2 (promotion gate + CLI) |
| RISK-09 | Task 2.3 |
| RISK-08 | Task 3.1 |
| RISK-07 | Task 4.1 |
