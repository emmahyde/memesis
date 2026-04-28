# Context: Agentic-Memory Audit BLOCKER Set

**Date:** 2026-04-28
**Mode:** Interactive
**Slug:** agentic-memory-blockers

## Work Description

Close the BLOCKER + MAJOR gaps surfaced by the 30-criteria agentic-memory audit (21/30 PASS, 70% conformance). Three load-bearing fixes:

1. **#5 cwd extraction** — `read_transcript_from()` filters out attachment entries, so `cwd` never reaches `detect_session_type()`. All sessions currently classify as `unknown`, defeating session-type-aware prompts/chunking.
2. **#19 + #21 staleness** — no `expires_at` column, no TTL enforcement; `compute_activation()` only feeds shadow-prune logger, not a real prune path. Most-cited anti-pattern in agentic-memory references is unaddressed.
3. **#14 MCP `search_memory` + `get_memory`** — `RetrievalEngine.hybrid_search` exists but no MCP exposure, so progressive disclosure is half-built and agents can't query memory cheaply.

Also covered: **#26 memory poisoning** safeguard for any agent-write path.

## Locked Decisions

### A. cwd extraction

**A1 — Strategy:** Modify `read_transcript_from()` to scan raw JSONL once for `cwd` on attachment entries, return `(entries, new_offset, cwd)`. Promotes the `_detect_cwd` workaround in `scripts/run_selected_sessions.py` into the API.
**A2 — Cache:** Add `cwd` column to `transcript_cursors` table. Lazy populate on first detection. Never invalidate (sessions are file-rooted; cwd doesn't change for Claude Code sessions in practice).
**A3 — Fallback:** `session_type=unknown` when cwd missing. `tool_uses` fingerprint is a soft tiebreak only inside `detect_session_type()` — never a primary signal.

### B. TTL semantics

**B1 — Two-stage enforcement:** soft-archive at `expires_at` (set `archived=1`, exclude from search results), hard-delete at `2 × expires_at` with cascade to FTS5 + `vec_memories`. One extra column, one extra query path; recoverable window for accidents.
**B2 — Tiered TTL floors:**
- T1: `expires_at = NULL` (never expire)
- T2: 180d
- T3: 90d
- T4: 30d

Tier values live in `core/tiers.py` alongside existing `compute_activation` constants.

**B3 — Reset on use:** retrieval bumps `expires_at` only when the memory is *consumed* by the agent (returned + read from RetrievalEngine), not on bare semantic match. Requires `accessed_at` write on the consumed-memory path inside `RetrievalEngine`.

**B4 — Column shape:** `expires_at INTEGER` (Unix timestamp, absolute). Indexed. Bump = `UPDATE memories SET expires_at = strftime('%s','now') + tier_ttl(tier)`.

### C. activation → prune

**C1 — Both roles:** activation as re-rank multiplier per-query (always); activation as prune gate only when `activation < tier_floor` AND `expires_at < now()`. Two independent failure conditions before deletion.
**C2 — Tier-derived floors:** hardcoded constants per tier, same module as B2 TTL constants. No learning until static policy is observed to fail.
**C3 — 30-day dry-run:** ship as logger-only first. The shadow-prune logger that already exists becomes the audit trail. Flip to live prune after 30 days of clean logs.

### D. MCP topology

**D1 — Stdio MCP server.** Entry in `~/.claude.json` `mcpServers`. No long-running daemon. Canonical MCP pattern.
**D2 — In-tree at `core/mcp_server.py`.** Single `pyproject.toml` entry point. Same repo as RetrievalEngine; one place to version-bump when schemas change.
**D3 — Local-only auth.** No tokens. Documented assumption: MCP server runs as user, talks to local SQLite, never exposed off-host.

### E. MCP tool scope

**E1 — Read-only first phase:**
- `search_memory(query, top_k=10, tier=None)` → wraps `RetrievalEngine.hybrid_search`, returns ranked summaries (~50–100 tokens each)
- `get_memory(memory_id)` → full hydration (~500–1000 tokens)
- `recent_observations(session_id, limit=10)` → recency-ordered

Defer `pin_memory`, `forget_memory`, `add_observation` until read patterns are observed.

**E2 — Write safety (deferred to phase 2):** trust agent for low-stakes (pin), confirm for high-stakes (forget, add). Out of scope for current BLOCKER set.

**E3 — Poisoning guard (designed now, enforced when writes ship):** all agent-originated writes get `source='agent'` column. `RetrievalEngine` excludes `source='agent'` rows from semantic prior computation until they accumulate K independent retrievals (auto-promote). Manual review is fallback path, not primary.

## Conventions to Enforce

- **Migrations:** add new ALTER TABLE blocks to `core/database.py:_run_migrations()` wrapped in try/except (idempotent on re-run). NO separate `core/migrations/` dir — that's not the project pattern.
- **All new columns get explicit defaults** so existing rows don't break:
  - `memories.expires_at` — already-discussed *new* column, `INTEGER DEFAULT NULL` (Unix timestamp)
  - `memories.source` — new, `TEXT DEFAULT 'human'`
  - `transcript_cursors.cwd` — new, `TEXT DEFAULT NULL`
  - **Already exist (no migration needed):** `memories.archived_at` (use as soft-archive flag — `IS NOT NULL` = archived), `memories.access_count`, `memories.last_accessed_at`, `memories.cwd`, `memories.is_pinned`. The `Memory.active()` scope already filters `archived_at IS NULL`.
- **FTS5 + `vec_memories` cascades:** follow existing `Memory.save()` / `Memory.delete_instance()` pattern in `core/models.py:184-197`. Any new hard-delete code path MUST go through a single-purpose `Memory.hard_delete()` method that wraps DELETE-from-fts + DELETE-from-vec_memories + DELETE-from-memories in `db.atomic()`. No raw `DELETE FROM memories` from app code.
- **New `core/tiers.py` module** owns: `tier_ttl(tier)`, `tier_activation_floor(tier)`, `tier_decay_tau_hours(tier)`, `stage_to_tier(stage)`. Tier-tau mapping currently lives only in `compute_activation` docstring (T1: 720h, T2: 168h, T3: 48h, T4: 12h) — promote to constants.
- **Stage→tier mapping:** `Memory.stage` is the existing TextField (`ephemeral`, `consolidated`, `crystallized`, `instinctive`, `archived`). `stage_to_tier()` is the bridge; existing observability code uses tier strings directly (`T1`, `T2`, `T3`, `T4`).
- **MCP server** uses `mcp` Python SDK (stdio transport). No HTTP layer. No FastAPI.
- **Per-operation SQLite connections** for vec_memories (unchanged); persistent peewee singleton (unchanged).
- **Tests for new code MUST** patch `core.llm.call_llm` not `anthropic.Anthropic` (per shared-llm-helper convention). Use `tmp_path` + local `base` fixture for DB isolation. No async tests — codebase has no `pytest-asyncio`.

## Concerns to Watch

- **FTS5/vec_memories drift:** soft-archive must exclude from search but keep the index entries (so resurrection on `expires_at` bump is cheap). Hard-delete must cascade. Mismatch here = silent retrieval bugs.
- **Tier-floor tuning:** T4=30d is aggressive. If agent-write volume is high, T4 memories may evaporate before they get the K retrievals to auto-promote out of `source=agent` quarantine. Watch dry-run logs for "would-prune-but-quarantined" cases.
- **`accessed_at` write contention:** RetrievalEngine reads happen on every consolidation tick. Bumping `expires_at` on every consumed memory means every read is now a write. Batch with a single UPDATE per query, not per memory.
- **cwd cache staleness:** if a user moves a project dir mid-session (rare), cached cwd in `transcript_cursors` will be wrong. Acceptable failure mode — re-scan is one bash command (`UPDATE transcript_cursors SET cwd=NULL WHERE session_id=?`).
- **MCP server cold-start latency:** stdio MCP spawns on each Claude Code session. RetrievalEngine init (FAISS-equivalent vec load) must stay <500ms or Claude Code will UX-feel laggy. Profile this before declaring done.
- **Memory poisoning E3 surface:** even read-only MCP exposes search to the agent. If an attacker gets text into a memory through some other path (transcript ingest of adversarial input), `search_memory` retrieval ranks it. The `source='agent'` quarantine is necessary but not sufficient — transcript-ingest path needs a separate review for adversarial input.

## Reusable Code

- `_detect_cwd()` from `scripts/run_selected_sessions.py:46-66` — promote into `core/transcript.py` as the implementation of cwd scanning.
- `compute_activation()` from `core/observability.py:84-115` — tier_floor lookup goes adjacent. Tier-tau values (T1: 720h, T2: 168h, T3: 48h, T4: 12h) currently in docstring only — promote to `core/tiers.py` constants.
- `log_shadow_prune()` from `core/observability.py:264-330` — already logs what *would* be pruned; flip a single flag (`SHADOW_ONLY = False` after dry-run) and add the cascade-delete call to make it real.
- `RetrievalEngine.hybrid_search` from `core/retrieval.py` — wrap unchanged for `search_memory` MCP tool. Already feature-complete.
- Existing `Memory` fields to reuse (no new columns needed): `archived_at` (B1 soft-archive flag), `access_count` (B3 increment), `last_accessed_at` (B3 timestamp), `cwd`, `is_pinned`. The `Memory.active()` scope at `core/models.py:138-140` already filters `archived_at IS NULL`.
- New columns required: `memories.expires_at INTEGER NULL`, `memories.source TEXT DEFAULT 'human'`, `transcript_cursors.cwd TEXT NULL`.
- FTS5+vec_memories cascade pattern: `Memory.save()` and `Memory.delete_instance()` at `core/models.py:184-197`.
- MCP stdio server boilerplate: `mcp` Python SDK examples — no rolled-by-hand transport.
- Migration pattern: `_run_migrations()` at `core/database.py:149-244` — try/except ALTER TABLE blocks (idempotent on re-run).

## Canonical References

- `core/transcript.py` — `read_transcript_from()` to be modified for A1
- `core/cursors.py` — `CursorStore` schema migration for A2 (add `cwd` column to `transcript_cursors`)
- `core/session_detector.py` — `detect_session_type()` for A3 fallback semantics
- `core/models.py` — `Memory` model; add `expires_at` and `source` fields (other needed fields already exist)
- `core/database.py` — `_run_migrations()`; add ALTER TABLE blocks for new columns
- `core/tiers.py` — NEW; tier policy constants and `stage_to_tier()`, `tier_ttl()`, `tier_activation_floor()`, `tier_decay_tau_hours()`
- `core/retrieval.py` — `RetrievalEngine`; add `last_accessed_at` + `expires_at` bump on consumed memories (B3); `source='agent'` filter on prior (E3); active-only filter excludes `archived_at IS NOT NULL` (already present)
- `core/observability.py` — `compute_activation` adjacent to `tier_activation_floor`; shadow-prune flip (C3)
- `core/mcp_server.py` — NEW; stdio MCP server with three read tools (E1)
- `pyproject.toml` — NEW entry point for MCP server CLI (`memesis-mcp = "core.mcp_server:main"`)
- `~/.claude.json` — `mcpServers` registration (user-side, not in repo)
- Audit table at `.planning/PIPELINE-INSIGHT-REPORT.md` — source of BLOCKER list
- `~/.claude/skills/agentic-memory/SKILL.md` — reference framework
