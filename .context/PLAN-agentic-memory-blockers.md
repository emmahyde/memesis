# Plan: Agentic-Memory Audit BLOCKER Set

**Source:** .context/CONTEXT-agentic-memory-blockers.md
**Generated:** 2026-04-28
**Status:** Ready for execution

## Overview

Closes five audit BLOCKERs (#5, #14, #19, #21, #26-column) by adding TTL/expiry semantics to the memory schema, promoting `cwd` extraction into the transcript API, wiring `RetrievalEngine` to consume-bump and live-filter, shipping a read-only MCP server, and reserving the poisoning-guard `source` column.

**Waves:** 5
**Total tasks:** 10

---

## Plan Concerns

None of the locked decisions are unimplementable as written. Three notes for implementers:

1. **`detect_session_type` default change (A3):** The current `default='code'` in `detect_session_type()` is overridden by locked decision A3 to `'unknown'`. This is a behavior change; callers that pass no `default=` argument will now get `'unknown'` instead of `'code'`. The only callers are `scripts/run_selected_sessions.py` and `hooks/consolidate_cron.py` — both are owned in Wave 2. Update them alongside the signature change.

2. **`Memory.live()` returns a query with compound WHERE:** peewee's `is_null()` and comparison operators compose correctly (`cls.archived_at.is_null() & ((cls.expires_at.is_null()) | (cls.expires_at > now_ts))`). Use `int(time.time())` for `now_ts` to match the `INTEGER` column type; do not use `datetime.now()` here.

3. **MCP SDK research not available:** Wave 4 (MCP server) is planned generically against the `mcp` Python SDK stdio transport. The research file `/Users/emmahyde/projects/memesis/.context/research/agentic-memory-blockers-mcp-sdk.md` was not present at plan time. Before executing Wave 4, check if it has been written and adjust `core/mcp_server.py` implementation accordingly (in-process test invocation pattern especially).

---

## Wave 1: Schema + Tier Policy Foundation

**Prerequisite:** None (first wave)

Two independent tasks. Task 1.1 creates the new tier-policy module (no DB touches). Task 1.2 adds the three new schema columns via `_run_migrations()` and extends `Memory` with the new fields, scopes, and methods. Both are self-contained; 1.2 does not need 1.1 to be complete to write migrations, but it MUST import from `core.tiers` — so the `import` is a soft dependency. If executing truly in parallel, stub the import in 1.2 and reconcile after both complete.

### Task 1.1: Create `core/tiers.py` — tier policy constants

- **Files owned:** `core/tiers.py` (NEW), `tests/test_tiers.py` (NEW)
- **Depends on:** None
- **Decisions:** B2 (tiered TTL floors), B4 (absolute `expires_at`), C2 (tier-derived hardcoded floors), CONTEXT conventions for `core/tiers.py` module
- **Touch estimate:** small (~80 LOC source, ~60 LOC tests)
- **Acceptance criteria:**
  - [ ] `stage_to_tier(stage)` returns `"T1"` for `"instinctive"`, `"T2"` for `"crystallized"`, `"T3"` for `"consolidated"`, `"T4"` for `"ephemeral"`. Returns `"T4"` as catch-all for unknown stage strings.
  - [ ] `tier_ttl(tier)` returns `None` for `"T1"`, `180 * 86400` for `"T2"`, `90 * 86400` for `"T3"`, `30 * 86400` for `"T4"`. Units are integer seconds.
  - [ ] `tier_activation_floor(tier)` returns a `float` for each tier (T1/T2 lower, T3/T4 higher — specific values per CONTEXT C2).
  - [ ] `tier_decay_tau_hours(tier)` returns `720` for T1, `168` for T2, `48` for T3, `12` for T4.
  - [ ] `tests/test_tiers.py`: all four functions covered for all four tier values; `tier_ttl("T1")` returns `None` explicitly; stage mapping test for every valid stage string.
  - [ ] No imports from `core.*` — module is standalone; no circular deps.

### Task 1.2: Schema migrations + `Memory` model extensions

- **Files owned:** `core/database.py`, `core/models.py`, `tests/test_models.py`
- **Depends on:** Task 1.1 (imports `stage_to_tier`, `tier_ttl` from `core.tiers`)
- **Decisions:** B1 (two-stage soft-archive + hard-delete), B4 (`expires_at INTEGER NULL`), E3 (`source TEXT DEFAULT 'human'`), CONTEXT migration conventions (try/except ALTER TABLE in `_run_migrations()`), CONTEXT FTS5+vec cascade for `hard_delete`
- **Touch estimate:** medium (~100 LOC source, ~120 LOC tests)
- **Acceptance criteria:**
  - [ ] `core/database.py:_run_migrations()` adds `expires_at INTEGER DEFAULT NULL` to `memories` via try/except ALTER TABLE block — idempotent on re-run (running migrations twice does not raise).
  - [ ] `core/database.py:_run_migrations()` adds `source TEXT DEFAULT 'human'` to `memories` — same idempotency requirement.
  - [ ] `core/models.py`: `Memory` model has `expires_at = IntegerField(null=True)` and `source = TextField(default='human')` field declarations.
  - [ ] `Memory.set_expiry(self)`: computes `int(time.time()) + tier_ttl(stage_to_tier(self.stage))` and writes to `self.expires_at`, then calls `self.save()`. For T1 stages sets `expires_at = None` (no expiry). Returns `None`.
  - [ ] `Memory.live()` class method: returns query `archived_at IS NULL AND (expires_at IS NULL OR expires_at > <now_unix>)`. Does NOT replace `Memory.active()` — both coexist.
  - [ ] `Memory.hard_delete(memory_id)` classmethod: in a single `db.atomic()`, executes DELETE from `memories_fts` (FTS5 sync), DELETE from `vec_memories`, DELETE from `memories`. Follows the cascade pattern at `core/models.py:184-197`.
  - [ ] `tests/test_models.py` additions: migration idempotency test for both new columns; `set_expiry` correct value for each stage (T1→None, others→positive integer); `live()` excludes a memory with `archived_at` set; `live()` excludes a memory with `expires_at` in the past; `live()` includes a memory with `expires_at` in the future; `hard_delete` removes from memories, memories_fts, and vec_memories (mock VecStore or skip vec assertion if unavailable).

**Wave 1 status:** pending

---

## Wave 2: Transcript + cwd Extraction

**Prerequisite:** None (parallel with Wave 1)

All four tasks touch separate files with no overlapping ownership. Wave 2 is fully independent of Wave 1.

### Task 2.1: Promote `_detect_cwd` into `core/transcript.py`; update `read_transcript_from` return signature

- **Files owned:** `core/transcript.py`, `tests/test_transcript.py` (NEW)
- **Depends on:** None
- **Decisions:** A1 (separate scan returning tuple), CONTEXT reusable code `_detect_cwd` from `scripts/run_selected_sessions.py:46-66`
- **Touch estimate:** small (~40 LOC source, ~70 LOC tests)
- **Acceptance criteria:**
  - [ ] `core/transcript.py` gains a module-private `_detect_cwd(path: Path) -> str | None` that scans the first 200 lines of raw JSONL for a top-level `"cwd"` field on any entry (not only `user`/`assistant` typed entries). Identical logic to the promoted workaround.
  - [ ] `read_transcript_from(path, byte_offset)` return type changes from `tuple[list[dict], int]` to `tuple[list[dict], int, str | None]`. Third element is the detected cwd (calls `_detect_cwd` once per call). Offset logic is unchanged.
  - [ ] `tests/test_transcript.py`: test that `read_transcript_from` on a JSONL with a cwd-bearing attachment entry returns the cwd as third element; test that a JSONL with no cwd-bearing entries returns `None` as third element; test that cwd is detected even when `byte_offset > 0` (partial read still scans full file for cwd).

### Task 2.2: Add `cwd` column to `transcript_cursors`; update `CursorStore`

- **Files owned:** `core/cursors.py`, `tests/test_cursors.py` (NEW)
- **Depends on:** None
- **Decisions:** A2 (lazy cache, never invalidate), CONTEXT migration conventions
- **Touch estimate:** small (~50 LOC source, ~60 LOC tests)
- **Acceptance criteria:**
  - [ ] `core/cursors.py`: `_CREATE_TABLE` updated to include `cwd TEXT DEFAULT NULL`. Migration path: on `__init__`, attempt `ALTER TABLE transcript_cursors ADD COLUMN cwd TEXT DEFAULT NULL` wrapped in try/except (idempotent for existing DBs).
  - [ ] `CursorRow` dataclass gains `cwd: str | None` field.
  - [ ] `CursorStore.get()` populates `cwd` from the row (NULL → `None`).
  - [ ] `CursorStore.upsert()` accepts optional `cwd: str | None = None` kwarg. If `cwd` is `None` AND a prior row exists with a non-NULL cwd, preserve the existing cwd (never overwrite non-NULL with NULL — lazy-populate, never-invalidate policy).
  - [ ] `tests/test_cursors.py`: migration idempotency (running `__init__` on an existing DB without the column adds it without error); cwd round-trips through upsert/get; updating offset without providing cwd does not clear a previously stored cwd; new session with no cwd stores NULL.

### Task 2.3: Update `detect_session_type` fallback to `'unknown'`

- **Files owned:** `core/session_detector.py`, `tests/test_session_detector.py` (update existing if present, else NEW)
- **Depends on:** None
- **Decisions:** A3 (missing cwd returns `unknown`; tool_uses is soft tiebreak only)
- **Touch estimate:** tiny (~5 LOC source, ~30 LOC tests)
- **Acceptance criteria:**
  - [ ] `detect_session_type()` default parameter changes from `default='code'` to `default='unknown'`.
  - [ ] When `cwd=None` and `tool_uses=None`, returns `'unknown'`.
  - [ ] When `cwd=None` and `tool_uses` yields no confident type, returns `'unknown'`.
  - [ ] When `cwd` is provided and matches a hint, returns correct type regardless of `tool_uses`.
  - [ ] `tests/test_session_detector.py`: `detect_session_type(None, None)` → `"unknown"`; `detect_session_type(None, [])` → `"unknown"`; cwd hint takes precedence over tool_uses; tool_uses fires only as tiebreak when cwd returns None.

### Task 2.4: Update callers of `read_transcript_from` to handle 3-tuple

- **Files owned:** `scripts/run_selected_sessions.py`, `hooks/consolidate_cron.py`
- **Depends on:** Task 2.1 (new tuple signature), Task 2.2 (CursorStore cwd upsert)
- **Decisions:** A1, A2 — consume the new tuple and pass cwd through to CursorStore
- **Touch estimate:** small (~30 LOC changes)
- **Acceptance criteria:**
  - [ ] `scripts/run_selected_sessions.py`: unpacks `(messages, new_offset, cwd)` from `read_transcript_from`. Removes the now-redundant `_detect_cwd()` call (function can be deleted from this file). Passes `cwd=cwd` to `CursorStore.upsert()`.
  - [ ] `hooks/consolidate_cron.py`: any call to `read_transcript_from` updated to unpack 3-tuple. cwd passed to session type detection if used.
  - [ ] No test file owned (integration covered by Task 2.1/2.2 tests). If `tests/test_scripts.py` covers `run_selected_sessions`, add a smoke test for the new tuple unpack path.

**Wave 2 status:** pending

---

## Wave 3: RetrievalEngine Wiring + Prune Infrastructure

**Prerequisite:** Wave 1 complete (needs `Memory.live()`, `expires_at` column, `core/tiers.py`)

### Task 3.1: Wire `Memory.live()` filter and consume-bump into `RetrievalEngine`

- **Files owned:** `core/retrieval.py`, `tests/test_retrieval.py`
- **Depends on:** Task 1.1 (`core/tiers.py`), Task 1.2 (`Memory.live()`, `Memory.expires_at`, `core/tiers`)
- **Decisions:** B3 (retrieval bump on consumed memory, not bare match), B4 (`expires_at` bump formula), C1 (activation as re-rank multiplier; prune gate separate), CONTEXT write-contention concern (single batched UPDATE per query)
- **Touch estimate:** medium (~70 LOC source, ~80 LOC tests)
- **Acceptance criteria:**
  - [ ] `hybrid_search()` (and any other search path that returns results to callers) switches from `Memory.active()` to `Memory.live()` as the base queryset. Expired memories do not appear in results.
  - [ ] After ranking and before return, `hybrid_search` issues a single batched UPDATE: `UPDATE memories SET last_accessed_at=<now_iso>, expires_at=<new_expiry> WHERE id IN (<returned_ids>)` — one UPDATE statement, not one per memory. `new_expiry` computed via `tier_ttl(stage_to_tier(memory.stage))` for each returned memory (or use the minimum/maximum TTL across the batch if per-row computation in SQL is needed — per-memory update is acceptable if the batch is a single execute with executemany).
  - [ ] Add NOTE comment at top of `core/retrieval.py` (Track E doc-only): "Future write-tools MUST set `source='agent'`; `_compute_priors()` should filter `source != 'agent' OR access_count > K` once agent-write path ships."
  - [ ] `tests/test_retrieval.py` additions: `hybrid_search` excludes a memory with `expires_at` in the past; `hybrid_search` excludes a memory with `archived_at` set; returned memories have `last_accessed_at` updated after search; returned memories have `expires_at` bumped after search; bump is a single UPDATE (can be verified by counting SQL calls via mock or by inspecting `last_accessed_at` on all returned memories simultaneously).

### Task 3.2: Add `SHADOW_ONLY` flag to `core/observability.py` and `scripts/prune_sweep.py`

- **Files owned:** `core/observability.py`, `scripts/prune_sweep.py` (NEW), `tests/test_observability.py`
- **Depends on:** Task 1.1 (`core/tiers.py` for `tier_ttl`), Task 1.2 (`Memory.hard_delete`)
- **Decisions:** C3 (30-day dry-run; `SHADOW_ONLY=True` default; flip to `False` after dry-run), B1 (hard-delete at 2× TTL past `archived_at`), CONTEXT cascade pattern
- **Touch estimate:** medium (~90 LOC source, ~70 LOC tests)
- **Acceptance criteria:**
  - [ ] `core/observability.py`: add module-level `SHADOW_ONLY: bool = True`. `log_shadow_prune()` unchanged in shadow mode. When `SHADOW_ONLY=False`, after logging, performs soft-archive: `UPDATE memories SET archived_at=<now> WHERE id=? AND archived_at IS NULL`.
  - [ ] `scripts/prune_sweep.py`: standalone script. Scans for memories where `archived_at IS NOT NULL` AND `expires_at IS NOT NULL` AND `expires_at < int(time.time()) - tier_ttl(stage_to_tier(stage))` (i.e. past 2× TTL window). Calls `Memory.hard_delete(memory_id)` for each. Prints a summary line to stdout. Does NOT call `log_shadow_prune` — sweep is a separate operation from the activation logger. Not wired into cron in this task.
  - [ ] `tests/test_observability.py`: with `SHADOW_ONLY=True`, `log_shadow_prune(would_prune=True, ...)` writes to jsonl and does NOT update `archived_at`; with `SHADOW_ONLY=False`, same call sets `archived_at` on the memory row; flipping `SHADOW_ONLY` mid-test produces the correct behavior switch.

**Wave 3 status:** pending

---

## Wave 4: MCP Server

**Prerequisite:** Wave 3 complete (needs `RetrievalEngine.hybrid_search` with live filter; needs stable `Memory.get_by_id`)

**NOTE:** MCP SDK research (`/Users/emmahyde/projects/memesis/.context/research/agentic-memory-blockers-mcp-sdk.md`) was not available at plan time. Before executing this wave, check if the file exists. If it does, read it and adjust `core/mcp_server.py` implementation — especially the in-process test invocation pattern for `tests/test_mcp_server.py`.

### Task 4.1: Implement `core/mcp_server.py` stdio MCP server

- **Files owned:** `core/mcp_server.py` (NEW), `tests/test_mcp_server.py` (NEW), `pyproject.toml`, `README.md`
- **Depends on:** Task 3.1 (`RetrievalEngine` with `Memory.live()` filter), Task 1.2 (`Memory` model with `expires_at`)
- **Decisions:** D1 (stdio transport), D2 (in-tree at `core/mcp_server.py`), D3 (local-only, no auth), E1 (three read-only tools), E3 (doc-only note on poisoning guard)
- **Touch estimate:** medium (~130 LOC source, ~90 LOC tests)
- **Acceptance criteria:**
  - [ ] `core/mcp_server.py` uses `mcp` Python SDK stdio transport. `main()` function is the entry point. Server exposes exactly three tools:
    - `search_memory(query: str, top_k: int = 10, tier: str | None = None)` — calls `RetrievalEngine(base_dir).hybrid_search(query, limit=top_k)`, optionally filters by tier via `stage_to_tier(m["stage"]) == tier`. Returns list of dicts with `id`, `title`, `summary`, `stage`, `rank` (~50-100 tokens/item).
    - `get_memory(memory_id: str)` — calls `Memory.get_by_id(memory_id)`, returns full content + tags + provenance fields. Raises MCP tool error if not found.
    - `recent_observations(session_id: str, limit: int = 10)` — queries `Memory.live()` filtered by `session_id` field (or `project_context` if `session_id` not a direct column), ordered by `created_at DESC`, limited to `limit`.
  - [ ] `pyproject.toml` gains `[project.scripts]` entry: `memesis-mcp = "core.mcp_server:main"`.
  - [ ] `README.md` gains a section "MCP Server" with one paragraph explaining how to register `memesis-mcp` in `~/.claude.json` `mcpServers`.
  - [ ] `tests/test_mcp_server.py`: all three tools are registered (inspect server tool list); `search_memory` calls `RetrievalEngine.hybrid_search` (mock it); `get_memory` returns expected shape for a real DB memory; `get_memory` with unknown ID returns MCP tool error, not exception; `recent_observations` returns recency-ordered results. No real stdio subprocess — use SDK in-process invocation (adjust based on research file if available).
  - [ ] `mcp` package added to `pyproject.toml` `[project.dependencies]` (or `[project.optional-dependencies]` if the SDK is optional).

**Wave 4 status:** pending

---

## Wave 5: Lifecycle Integration

**Prerequisite:** Wave 1 complete (needs `Memory.set_expiry()` and `core/tiers`), Wave 3 complete (establishes the expiry bump pattern; lifecycle must not conflict with retrieval bumps)

### Task 5.1: Wire `Memory.set_expiry()` into `LifecycleManager` stage transitions

- **Files owned:** `core/lifecycle.py`, `tests/test_lifecycle.py`
- **Depends on:** Task 1.2 (`Memory.set_expiry()`), Task 3.1 (expiry policy established in retrieval — no conflict, different call sites)
- **Decisions:** B2 (tiered TTL resets on stage promotion), B3 (expiry also reset on retrieval — two independent reset triggers, not conflicting), CONTEXT lifecycle stage promotion at `core/lifecycle.py:promote()`
- **Touch estimate:** small (~25 LOC source, ~40 LOC tests)
- **Acceptance criteria:**
  - [ ] `LifecycleManager.promote()`: after `memory.save()` at line 79, calls `memory.set_expiry()`. Stage has already changed, so `set_expiry` uses the new stage's tier TTL.
  - [ ] `LifecycleManager.demote()`: after `memory.save()` at line 117, calls `memory.set_expiry()`. Demotion to a lower tier gets a shorter TTL — this is correct behavior.
  - [ ] `LifecycleManager.deprecate()` (archive path): does NOT call `set_expiry()` — archived memories have `archived_at` set and are already excluded by `Memory.live()`. Leave as-is.
  - [ ] `tests/test_lifecycle.py` additions: after `promote()`, memory's `expires_at` is non-null and equals approximately `now + tier_ttl(new_tier)`; after `demote()`, memory's `expires_at` reflects the lower tier's TTL; T1 (instinctive) promotion sets `expires_at = None`.

**Wave 5 status:** pending

---

## File Ownership Map

| File | Owner |
| ---- | ----- |
| `core/tiers.py` | Task 1.1 |
| `tests/test_tiers.py` | Task 1.1 |
| `core/database.py` | Task 1.2 |
| `core/models.py` | Task 1.2 |
| `tests/test_models.py` | Task 1.2 |
| `core/transcript.py` | Task 2.1 |
| `tests/test_transcript.py` | Task 2.1 |
| `core/cursors.py` | Task 2.2 |
| `tests/test_cursors.py` | Task 2.2 |
| `core/session_detector.py` | Task 2.3 |
| `tests/test_session_detector.py` | Task 2.3 |
| `scripts/run_selected_sessions.py` | Task 2.4 |
| `hooks/consolidate_cron.py` | Task 2.4 |
| `core/retrieval.py` | Task 3.1 |
| `tests/test_retrieval.py` | Task 3.1 |
| `core/observability.py` | Task 3.2 |
| `scripts/prune_sweep.py` | Task 3.2 |
| `tests/test_observability.py` | Task 3.2 |
| `core/mcp_server.py` | Task 4.1 |
| `tests/test_mcp_server.py` | Task 4.1 |
| `pyproject.toml` | Task 4.1 |
| `README.md` | Task 4.1 |
| `core/lifecycle.py` | Task 5.1 |
| `tests/test_lifecycle.py` | Task 5.1 |

## Cross-Wave Ownership Handoffs

| File | Wave N Owner | Wave M Owner | Handoff Notes |
| ---- | ------------ | ------------ | ------------- |
| `core/models.py` | Task 1.2 (Wave 1): adds `expires_at`, `source` fields, `set_expiry()`, `live()`, `hard_delete()` | Task 5.1 (Wave 5): calls `memory.set_expiry()` from lifecycle transitions | 5.1 must read 1.2's `set_expiry()` signature before adding call sites in `lifecycle.py`. Must not revert field additions. |
| `core/retrieval.py` | Task 3.1 (Wave 3): switches to `Memory.live()`, adds consume-bump, adds E3 NOTE | Task 4.1 (Wave 4): wraps `hybrid_search` for MCP tool; reads but does not modify `retrieval.py` | 4.1 imports `RetrievalEngine` as-is after 3.1's changes; no modification to `retrieval.py` in Wave 4. Not a conflict, listed for awareness. |
| `tests/test_models.py` | Task 1.2 (Wave 1): adds migration and model tests | Task 5.1 (Wave 5) does NOT own this file — lifecycle tests go in `test_lifecycle.py` only | No conflict; listed to clarify boundary. |
| `tests/test_retrieval.py` | Task 3.1 (Wave 3): adds live-filter and consume-bump tests | No later wave modifies this file | Single owner; listed for completeness. |

**Handoff protocol:** When a file appears here, the later task's implementer MUST:

1. Read the file as modified by the earlier task (not the original)
2. Build on those changes, not revert them
3. If the earlier task's changes conflict with the later task's needs, escalate to team lead

## Decision Traceability

| Decision | Tasks |
| -------- | ----- |
| A1 (separate scan returning tuple) | Task 2.1, Task 2.4 |
| A2 (lazy cwd cache in cursor store) | Task 2.2, Task 2.4 |
| A3 (unknown fallback, tool_uses soft tiebreak) | Task 2.3 |
| B1 (two-stage soft-archive + hard-delete) | Task 1.2, Task 3.2 |
| B2 (tiered TTL floors) | Task 1.1, Task 1.2, Task 5.1 |
| B3 (retrieval consume-bump) | Task 1.2, Task 3.1, Task 5.1 |
| B4 (absolute `expires_at` INTEGER) | Task 1.1, Task 1.2, Task 3.1 |
| C1 (activation both roles: re-rank + prune gate) | Task 3.1, Task 3.2 |
| C2 (tier-derived hardcoded floors) | Task 1.1, Task 3.2 |
| C3 (30-day dry-run, SHADOW_ONLY flag) | Task 3.2 |
| D1 (stdio MCP) | Task 4.1 |
| D2 (in-tree `core/mcp_server.py`) | Task 4.1 |
| D3 (local-only, no auth) | Task 4.1 |
| E1 (three read-only tools) | Task 4.1 |
| E3 (source column reserved, doc-only enforcement) | Task 1.2 (column migration), Task 3.1 (NOTE comment) |
