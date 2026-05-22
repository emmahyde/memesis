---
title: "Memesis System Status — 2026-05-19"
date: 2026-05-19
tags: [status, memesis, lifecycle, storage, transport]
---

# Memesis System Status — 2026-05-19

Operational reference for future-Claude sessions. Covers the current state of each major subsystem as read directly from the source tree. Code references use `file_path:line_number`.

---

## Memory Lifecycle Stages

Four stages in strict order. Promotion advances one step at a time; demotion can skip stages.

```
ephemeral → consolidated → crystallized → instinctive
```

| Stage | Description | Expiry |
|---|---|---|
| ephemeral | Raw observations from transcript extraction | 30-day staleness gate (deprecation candidates) |
| consolidated | LLM-reviewed, Stage 2 enriched, KEEP'd observations | Stays until promoted or demoted |
| crystallized | LLM-synthesized semantic insight; episodic details stripped | Permanent record |
| instinctive | Always-on, injected every session start | Never auto-expires |

Demotion trigger (`core/lifecycle.py:188`): memories injected 10+ times with `usage_count == 0` are flagged for demotion. Deprecation trigger (`core/lifecycle.py:218`): ephemeral memories with no activity for 30 days.

---

## Promotion Gates

### ephemeral → consolidated

Always valid during consolidation. No gate. (`core/lifecycle.py:279`)

### consolidated → crystallized — `_can_promote_to_crystallized`

`core/lifecycle.py:335`

**Prerequisite**: `memory_kind` must be non-NULL unless `kind == 'open_question'`. Open-question rows are exempt; they carry `memory_kind=NULL` by design. (`core/lifecycle.py:287`)

**Contradiction block**: `promoter.has_blocking_contradiction(memory_id)` must return False. (`core/lifecycle.py:271`)

**Two paths (either qualifies):**

1. **High-importance fast path** — `importance >= CRYSTALLIZE_IMPORTANCE_THRESH` (default 0.75, env-overridable via `MEMESIS_CRYSTALLIZE_IMPORTANCE_THRESH`). Crystallizes at any `reinforcement_count`. Rationale: a rare-but-pivotal memory should not wait on repeated reinforcement.

2. **Standard path** — `reinforcement_count >= 3` AND spaced reinforcement (`_has_spaced_reinforcement`). `MIN_REINFORCEMENT_SPAN_DAYS = 0` currently (spacing check passes if no ConsolidationLog entries exist or they span 0+ distinct days).

### crystallized → instinctive — `_can_promote_to_instinctive`

`core/lifecycle.py:405`

Requires **both**:
- `importance > 0.85`
- used in `10+` distinct sessions (`_count_unique_sessions` via `RetrievalLog`)

### Crystallizer batch cap

`core/crystallizer.py:124`

```python
CRYSTALLIZE_BATCH_LIMIT = int(os.environ.get("MEMESIS_CRYSTALLIZE_BATCH_LIMIT", "10"))
```

Per cron invocation, candidates are sorted highest-importance-first and capped at `CRYSTALLIZE_BATCH_LIMIT` (default 10). A large backlog drains over multiple cron ticks.

---

## Importance Rubric

Defined in `core/prompts.py:252` (IMPORTANCE RE-SCORING block, panel C7). Applied by the Stage 2 consolidation LLM when reviewing observations.

| Band | Score | Lifecycle effect |
|---|---|---|
| ROUTINE | 0.00–0.30 | Memory expires. Re-derivable from code/git. |
| CONTEXT | 0.30–0.55 | Short-lived consolidated memory. Minor-detour cost if wrong. |
| SIGNIFICANT | 0.55–0.74 | Stays consolidated. Scoped or contingent — not permanent. |
| LOAD-BEARING | 0.75–0.84 | **Crystallizes**. Getting this wrong causes real rework. Applies beyond the session. |
| INVARIANT/CORRECTIVE | 0.85–1.00 | **Instinctive-eligible**. Explicit correction, hard constraint, or behavioral rule that fires every session. |

**Score-to-tier mapping:**
- `importance >= 0.75` → triggers `_can_promote_to_crystallized` high-importance fast path
- `importance > 0.85` → satisfies the instinctive gate (plus 10-session usage requirement)

**Kind-based default bands** (soft — overridable with stated rationale in `rationale` field):

| Kind | Default band |
|---|---|
| correction, constraint | 0.85–1.00 |
| decision, preference | 0.75–0.84 |
| open_question | 0.30–0.55 (or 0.75–0.84 if action item present) |
| finding | No shift; score purely on band tests |

**Calibration rule**: spread scores. A distribution clustering at 0.5–0.6 carries no ranking signal. Push higher for: numeric evidence, alignment with established practice, explicit unresolved action item. Push lower for: third-party-owned subject matter, anything mechanically enforced by a test or CI check.

---

## Self-Model / Hypothesis Path

`core/self_reflection.py:660` — `can_promote_hypothesis`

**Gate applies only to `kind == 'hypothesis'` rows.** All other kinds bypass it.

Gate rules:
1. `evidence_count >= 3`
2. No `contradicts` edge touching the memory (bidirectional check on `memory_edges`)

**Removed requirement**: a prior distinct-session check (`evidence_session_ids`) was dropped. `evidence_count` already encodes repeated observation; the session proxy could permanently wedge a well-evidenced hypothesis when `session_id` was not provided.

`SelfReflector` is **experimental** (`core/self_reflection.py:26`). Opt-in via `MEMESIS_EXPERIMENTAL_MODULES=self_reflection`. The writer path (`reflect()` → `apply_reflection()`) runs unconditionally to accumulate hypothesis evidence for the Wave 3.2 promotion gate, but retrieval scoring excludes the module unless opted in.

Seed instinctive memories created by `ensure_instinctive_layer()`:
- `Self-Model` (importance 0.90)
- `Observation Habit` (importance 0.85)
- `Compaction Guidance` (importance 0.90)

---

## Storage

**Single global DB**: `~/.claude/memory/index.db`

`core/database.py:63` — `_resolve_db_path`:
```python
bd = Path.home() / ".claude" / "memory"
return bd, bd / "index.db"
```

Project identity is recorded per-row via the `project` column (e.g., `-Users-emmahyde-projects-memesis`), not via separate DB files. `project_context` arg to `init_db()` no longer routes the DB path — it only sets the `project` column value.

ORM: Peewee with WAL mode and `busy_timeout=5000`. **Never open a separate `sqlite3.connect()` to `index.db`** — use `init_db()`, Peewee models, or `db.execute_sql()` only. (`CLAUDE.md` Rule 1)

### DB Inventory by Stage (as of 2026-05-19)

> DB at `~/.claude/memory/index.db` confirmed present. Live counts require `uv run python3 -c "from core.database import init_db; init_db(); from core.models import Memory; [print(s, Memory.select().where(Memory.stage==s, Memory.archived_at.is_null()).count()) for s in ['ephemeral','consolidated','crystallized','instinctive']]"` from the repo root. Not executed here to avoid side effects.

---

## Transport

`core/llm.py` — `call_llm()`

Priority order:
1. **claude-agent-sdk** (`claude_agent_sdk.query`) — preferred. OAuth subscription credentials. SDK serializes token refreshes, eliminating rc=1 races from parallel `claude -p` invocations. Enables `asyncio.gather` concurrency via `call_llm_batch`.
2. **Fallback: `claude -p` subprocess** — used when SDK unavailable or `asyncio.run` nesting detected.

**Bedrock transport removed.** API-key path (`ANTHROPIC_API_KEY`) intentionally disabled — stripped from spawned subprocesses to prevent CLI from entering API-key mode. (`core/llm.py:17`)

Default model: `claude-sonnet-4-6` (`core/llm.py:47`)

System prompts: resolved from `core/system_prompts/<name>.md` via `_load_system_prompt()`. Missing file raises `FileNotFoundError` (hard error — a misnamed prompt is a bug). (`core/llm.py:57`)

Batch concurrency: `call_llm_batch(prompts, max_concurrency=5)` — semaphore-bounded `asyncio.gather`. Per-prompt failures return `[ERROR] ...` string rather than raising, for partial-success fan-out. (`core/llm.py:423`)

---

## memory_kind Taxonomy

Curated enum stored in `memories.memory_kind`. Distinct from `kind` (observation-extraction taxonomy). Enforced at promotion time and by DB triggers.

**Values** (`core/validators.py:44`, `core/migrations/sql/20260517_0018_memory_kind_check.py`):

| Kind | Description |
|---|---|
| decision | Chose between alternatives; has rationale + rejected options |
| lesson | Pattern extracted from ≥2 incidents; prescribes future behavior |
| gotcha | Trap that bit us; concrete + reproducible |
| goal | North-star statement shaping future decisions |
| invariant | Fragile coupling future refactors must preserve |
| opinion | Stance on right/wrong with rationale |
| bias | Systematic LLM/system failure mode (anti-checklist) |
| todo | Action item with concrete done-state predicate |
| debt | Known issue/cleanup, status-bearing |
| fact | Small, code-derivable but worth pinning (rare) |

**NULL is allowed** — correct for ephemeral rows and `open_question` kind. Presence enforced at promotion (`LifecycleManager.can_promote`, `core/lifecycle.py:287`). Garbage values rejected by DB triggers.

**DB enforcement** (`core/migrations/sql/20260517_0018_memory_kind_check.py`): Two `BEFORE INSERT` / `BEFORE UPDATE` triggers on `memories` raise `ABORT` when `memory_kind IS NOT NULL AND memory_kind NOT IN (...)`. Migration 0018.

---

## Cron Schedule

Two launchd agents. Both currently **unloaded** (must be loaded with `launchctl load` to run).

### consolidate-cron

Plist: `~/Library/LaunchAgents/com.emmahyde.memesis.consolidate-cron.plist`
Script: `hooks/consolidate_cron.py`
Schedule: `StartCalendarInterval Minute=7` — fires once per hour at :07
Logs: `/tmp/memesis-consolidate.log`
Runtime: Python at `/Users/emmahyde/.local/share/mise/installs/python/3.12.12/bin/python3`

Cron sequence (fixed order): consolidation (mints `contradicts` edges) → `resolve_contradictions_pass` → promotions. Promotions in the same cron tick see all resolved edges from that tick.

### transcript-cron

Plist: `~/Library/LaunchAgents/com.emmahyde.memesis.transcript-cron.plist`
Script: `scripts/transcript_cron.py`
Schedule: `StartInterval=900` — fires every 15 minutes
Logs: `/tmp/memesis-transcript.log`

**Live-tree execution**: both agents invoke repo scripts directly with no staging step. Uncommitted edits to path-resolution or DB-init code take effect on the next cron tick.

### Freeze procedure

Before any irreversible DB migration:
```
launchctl unload ~/Library/LaunchAgents/com.emmahyde.memesis.consolidate-cron.plist
launchctl unload ~/Library/LaunchAgents/com.emmahyde.memesis.transcript-cron.plist
```

---

## Key Invariants for Future Sessions

1. **Never bypass Peewee with raw sqlite3** on `index.db`. WAL + busy_timeout managed by the singleton.
2. **All LLM calls through `core.llm.call_llm()`**. No direct `anthropic.Anthropic()` clients.
3. **Tests use conftest temp-dir fixtures and mock `call_llm`**. No real-store writes or real LLM calls in tests.
4. **Cron order is fixed**: consolidate → resolve → promote.
5. **`memory_kind` NULL is valid** at ephemeral stage; required non-NULL before crystallization (except `open_question`).
6. **Importance 0.75 crystallizes; 0.85 + 10 sessions instinctivizes**.
