# CONTEXT: Risk Register Fixes

Source: Canvas review of `.context/RISK-REGISTER.md` on 2026-05-07. Decisions captured in `~/.claude/projects/-Users-emmahyde-projects-memesis/memory/consolidated/risk-register-review-2026-05-07.md`. This CONTEXT covers the implementable batch (RISK-02, 06, 07, 08, 09, 10, 11, 12, 14). Architectural shifts (RISK-01 cron migration, RISK-15 dashboard) are out of scope here — separate planning.

## Scope

Nine risks, grouped by surface area. Order chosen so foundational data-layer changes (migration runner, embedding metadata) ship before consumers depend on them.

### 1. RISK-10 — Migration runner [H]

- New module: `core/migrations/`
  - `core/migrations/__init__.py` — runner: scans `core/migrations/sql/`, applies in timestamp order, records in `schema_migrations` table.
  - `core/migrations/sql/YYYYMMDDHHMMSS_<description>.sql` — one file per migration. Plain SQL preferred; for non-trivial transforms (e.g., `consolidation_log` CHECK constraint replacement which requires DROP + recreate + copy), use `.py` with `up(conn)` callable.
- `schema_migrations` table: `(version TEXT PRIMARY KEY, applied_at TIMESTAMP)`.
- Seeding existing DBs: on first run, if `PRAGMA user_version >= 2`, mark all pre-existing migrations as already-applied. Avoids re-running ALTERs that succeeded in old codepath.
- `core/database.py` `init_db()` calls migration runner after pragma setup.
- Use `playhouse.migrate` for programmatic ALTER helpers when raw SQL is awkward.
- Tests: ephemeral DB, run forward from empty, run forward from seeded `user_version=2`, idempotency (apply twice = no-op).

### 2. RISK-06 — Embedding metadata [H]

- Schema: add `embedding_model TEXT NOT NULL`, `embedding_version TEXT NOT NULL`, `embedding_dim INTEGER NOT NULL` to vec records (in `core/vec.py` schema).
- New `_system` table: `(key TEXT PRIMARY KEY, value TEXT)`. Stores active `embedding_model`, `embedding_dim`, `embedding_version`.
- Insert validation in `core/vec.py` rejects rows whose `embedding_dim` mismatches active config.
- New CLI: `memesis reindex --vec` — re-embed all memories under active model, atomic swap.
- Migration: backfill metadata on existing vec rows from current default model (`embeddings.py` constants).
- Tests: insert with mismatched dim raises; reindex command idempotent; metadata round-trips.

### 3. RISK-02 — Pydantic schemas for consolidation LLM output [H]

- New module: `core/schemas.py` — Pydantic models for LLM consolidation output.
- Validation rules:
  - `action ∈ {keep, update, merge, archive, prune, promote}`
  - Stage transitions allowed: `ephemeral→consolidated`, `consolidated→crystallized`, `crystallized→instinctive`, `*→pending_delete`. Any other rejected.
  - `importance ∈ [0.0, 1.0]`
  - Memory IDs validated as existing UUIDs in current DB
  - Destructive actions (`prune`, `archive`) require non-empty `rationale`
- Two-phase apply in `core/consolidator.py`:
  - Phase 1: write `pending_delete` stage. No hard delete.
  - Phase 2: hard delete only after TTL (configurable, default 7 days) or explicit confirmation via `memesis forget --confirm <id>`.
- Pydantic for LLM I/O contract; Peewee remains storage layer. No overlap.
- Tests: invalid action rejected; bad ID rejected; pending_delete TTL flow; confirmation flow.

### 4. RISK-07 — DB transaction audit [M]

- Audit `core/consolidator.py`, `core/transcript_ingest.py`, `core/session_vec.py` for `with conn:` or `BEGIN` blocks that span LLM calls.
- Refactor: read inside txn → close txn → LLM call → write inside new txn. No write lock held during network I/O.
- Confirm `busy_timeout=5000` set on every connection (currently only `init_db`). Move to a connection factory used everywhere.
- Tests: concurrent write contention with sleep-mocked LLM does not deadlock.

### 5. RISK-08 — asyncio.gather error isolation [M]

- In `core/consolidator.py` batch consolidation loop: replace bare `asyncio.gather` with `asyncio.gather(..., return_exceptions=True)`.
- Per-item exception handling: log + continue, not abort batch.
- Add `asyncio.Semaphore(3)` to cap concurrent LLM subprocess calls.
- Idempotency keys on retry: hash `(memory_id, prompt_version, model)` so retried items don't duplicate writes.
- Tests: one item raises → others still complete; semaphore bound respected; retry with same key is no-op.

### 6. RISK-11 — Cognitive module audit [M]

- Read all 7 modules under `core/cognitive/` (or wherever they live — locate first): `affect`, `coherence`, `habituation`, `orienting`, `self_reflection`, `somatic`, `replay`.
- Document for each: inputs, outputs, scoring formula, validation status.
- Output: `docs/cognitive-modules.md` with one section per module + summary table.
- Per-module score logging: extend retrieval output to include `module_scores: {affect: 0.3, coherence: 0.7, ...}`. Already-aggregated `score` stays.
- `experimental: bool` flag on each module. Gating: experimental modules excluded from default scoring; opt-in via env var `MEMESIS_EXPERIMENTAL_MODULES=affect,coherence`.
- `memesis stats` adds module breakdown section: how many memories scored by each module, mean contribution, experimental status.

### 7. RISK-12 — Self-reflection hypothesis gates [M]

- Self-reflection writer always sets `kind=hypothesis`, `evidence_count=1` on initial write.
- Distinguish at write time: explicit user statements can have `evidence_count=1` and promote on demand; inferred hypotheses require `>=3` evidence + `>=2` distinct session IDs + no contradicting memory.
- Schema: `memories.kind TEXT` (default `null` for non-hypothesis), `memories.evidence_count INTEGER DEFAULT 0`, `memories.evidence_session_ids TEXT` (JSON array).
- Promotion checker runs on consolidation: if gates pass, demote `kind` to null + bump stage.
- New CLI: `memesis hypotheses` — list pending hypotheses with evidence count, allow promote/reject/edit.
- Tests: gate enforcement, promotion path, contradiction blocks promotion.

### 8. RISK-14 — Stdout discipline in hooks [M]

- Audit `hooks/` (or wherever Claude Code hook entry points live).
- Wrap each entry point in `try/except`. On any path that is not the official injection JSON, route to stderr.
- Add a `_hook_safe_print` helper. Replace all `print(...)` in hooks with it.
- Integration tests: capture stdout, assert it parses as valid hook JSON or is empty. Capture stderr, assert errors land there.

### 9. RISK-09 — Decouple injection_count from importance [L]

- Schema: `memories.injection_count INTEGER DEFAULT 0` stays.
- Importance scoring formula in `core/scoring.py` (or wherever importance is computed): remove injection_count term.
- Log `injection_count` separately in retrieval output for eval correlation studies.
- Eval task added to `eval/` to compute correlation between injection_count and downstream "useful" signal (TBD: define useful — likely `kept_after_consolidation` or `promoted`).

## Out of scope (for this CONTEXT)

- **RISK-01** — PreCompact → cron architectural shift. Bigger redesign; needs its own CONTEXT and discussion.
- **RISK-15** — Observability dashboard. Vision item; scope undefined.
- **RISK-16, RISK-17** — Deferred per review.
- **Connection pooling** — Deferred; WAL singleton sufficient.

## Decisions reference

All decisions from `memory/consolidated/risk-register-review-2026-05-07.md`. If implementation hits a fork not covered, escalate before silently choosing.

## Key cross-cutting concerns

- **Schema migration ordering matters.** RISK-10 (runner) MUST land before RISK-06 (embedding metadata cols), RISK-12 (kind/evidence cols), RISK-09 (no schema change but consumers expect runner). All later schema changes ship as migration files.
- **Pydantic + Peewee boundary.** Pydantic models live in `core/schemas.py`, parse LLM output. Peewee models in `core/models.py`, persist to SQLite. Mapping happens in `core/consolidator.py`. No Pydantic in storage layer.
- **Test isolation rule.** Per CLAUDE.md: tests never touch `~/.claude/memory`. Use `conftest.py` fixtures.
- **LLM call rule.** Per CLAUDE.md: all LLM calls through `core.llm.call_llm()`.
