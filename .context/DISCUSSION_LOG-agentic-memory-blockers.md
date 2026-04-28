# Discussion Log: Agentic-Memory Audit BLOCKER Set

**Date:** 2026-04-28
**Mode:** Interactive
**Slug:** agentic-memory-blockers

## Audit Output

30-criteria audit of `/Users/emmahyde/projects/memesis/` against `~/.claude/skills/agentic-memory/SKILL.md`:

- **21/30 PASS** (70%)
- **5 FAIL** (BLOCKER): #5 cwd extraction, #14 MCP search_memory, #19 expires_at column, #21 activation→prune wiring, #26 memory poisoning
- **4 PARTIAL**: #10 cosine-on-write dedup, #11 dedup, #13 Tier 3 hydration, #30 Stage 2 batch concurrency

User scoped immediate work to: #5, #19+#21, #14 (BLOCKER set). #26 in scope for design only (column reservation), enforcement deferred to write-tools phase.

## Codebase Findings (Quiet Context)

- `read_transcript_from()` filters to `user`/`assistant` entry types only; `cwd` lives on `attachment` entries. Workaround already exists in `_detect_cwd` runner-side.
- `RetrievalEngine.hybrid_search` is feature-complete; no MCP exposure.
- `compute_activation()` runs but only feeds shadow-prune logger, not a prune path.
- No `expires_at`, `archived`, `accessed_at`, or `source` columns on `memories`.
- No `cwd` column on `transcript_cursors`.
- Stale-embedding-after-update is a known correctness bug already documented in `CONCERNS.md`. Out of scope for this discussion (separate fix).
- Existing CONTEXT files reviewed for conflicts: shared-llm-helper conventions (test mock paths, model constants) carry forward unchanged.

## Gray Areas Surfaced

5 areas (A–E) presented with options. User asked for deep reasoning per area before deciding.

### A — cwd extraction
- **A1 strategy:** (a) widen filter / (b) separate scan in `read_transcript_from` returning tuple / (c) caller-supplied hint
- **A2 cache:** add `cwd` column to `transcript_cursors`?
- **A3 fallback:** `unknown` session_type vs infer from tool_uses

### B — TTL semantics
- **B1 enforcement:** soft-archive vs hard-delete vs two-stage
- **B2 tier policy:** tiered floors vs single global TTL
- **B3 reset trigger:** retrieval bump vs explicit pin only
- **B4 column:** `expires_at` absolute vs `ttl_days` relative

### C — activation → prune
- **C1 role:** re-rank only / prune gate / both
- **C2 threshold source:** fixed / tier-derived / learned
- **C3 dry-run:** ship as logger-only first?

### D — MCP topology
- **D1 transport:** stdio / HTTP sidecar / both
- **D2 process location:** in-tree vs separate package
- **D3 auth:** local-only vs token

### E — MCP scope
- **E1 tools:** read-only first vs read+write
- **E2 write safety:** confirm vs trust
- **E3 poisoning guard:** `source=agent` tag + exclude from prior vs no guard

## Reasoning Highlights

**A1 → (b) separate scan returning tuple.** cwd is a property of the transcript file; the function reading the file should return it. (a) leaks attachment shape into clean-entry consumers; (c) duplicates discovery across callers.

**A2 → yes, lazy cache.** Cursor store is the natural cache key. Cron cost across N sessions multiplied is real; pay scan cost once per session.

**A3 → `unknown` first, tool-use as soft tiebreak.** Honest "I don't know" beats wrong inference. Don't pretend confidence we don't have.

**B1 → (c) two-stage.** One extra column buys recoverable window for accidents. Lets TTL be tuned aggressively without fearing data loss. Aligns with email-trash and soft-delete patterns.

**B2 → tiered.** Whole point of tiers is differentiated value; global TTL erases that signal. T1 stays NULL (never expires) — user identity facts shouldn't decay.

**B3 → retrieval-when-used bumps `expires_at`.** Mere semantic match is noise; consumed-by-agent is signal. Closes the Ebbinghaus loop.

**B4 → absolute `expires_at`.** Survives policy changes. Indexable. `expires_at < now()` is one query.

**C1 → both, with separate thresholds.** Re-rank operates per-query (soft signal); prune only fires at hard floor AND past `expires_at`. Two independent failure conditions before deletion. Mirrors human sleep-consolidation.

**C2 → tier-derived hardcoded floors.** Same module as TTL constants. Static policy until evidence of failure. No premature ML.

**C3 → 30-day dry-run.** Shadow-prune logger already exists; flip the flag once logs look sane. Standard feature-flag migration.

**D1 → stdio MCP.** Canonical pattern. No daemon to manage. Lowest operational cost.

**D2 → in-tree at `core/mcp_server.py`.** MCP server is just another consumer of `RetrievalEngine`. One repo, one version-bump path.

**D3 → local-only.** Server runs as user, talks to local SQLite, on localhost. Auth is theater here.

**E1 → read-only first.** BLOCKER set is about progressive disclosure. Reads have no poisoning surface. Ship `search_memory`, `get_memory`, `recent_observations`. Defer writes.

**E2 → trust low-stakes, confirm high-stakes (deferred phase).** Two-tier matches actual risk profile.

**E3 → `source=agent` tag, exclude from prior, auto-promote on K retrievals.** Directly addresses MemoryGraft. In the index, but not in priors. Reserve column now even though enforcement waits.

## User Decision

`Go for it` → accept all recommendations as locked.

## Outstanding (Out of Scope, Tracked for Later)

- #10/#11 cosine-on-write dedup
- #13 Tier 3 hydration coupled with #14 (already covered transitively)
- #26 memory poisoning enforcement (column reserved now, logic when writes ship)
- #30 Stage 2 batch concurrency
- Stale-embedding-after-update (separate CONCERN)
- E1 phase 2: `pin_memory`, `forget_memory`, `add_observation`
