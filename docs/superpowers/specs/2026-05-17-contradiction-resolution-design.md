# Stored-vs-Stored Contradiction Resolution — Design Spec

**Date:** 2026-05-17
**Status:** Design approved, ready for planning
**Task:** #8

## Problem

memesis detects contradictions between two already-stored memories by threading
`contradicts` edges into `memory_edges`. But the existing resolution actions
(SUPERSEDE / ARCHIVE / REFINE in `consolidator._resolve_conflicts`) only fire for
**new-observation-vs-stored-memory** conflicts. Thread `contradicts` edges between
two stored memories are detection-only — no procedure ever resolves them. A memory
can therefore crystallize while still contradicting another stored memory.

## Approved decisions

| Decision | Choice |
|---|---|
| Trigger | At promotion — a memory cannot advance stage while it has an unresolved/queued contradiction |
| Resolution outcomes | SUPERSEDE, ARCHIVE, REFINE, + BLOCK (hold at current stage) |
| Blocked state | Dedicated review queue table; promotion retries only after human action |
| Edge scope | All `contradicts` edges incident to the promoting memory |
| Mechanism | Approach B (async resolver pass in the hourly cron) + approach C auto-recheck |
| Resolution unit | The edge, not the memory — per-edge resolution with edge-claim dedup |
| Module ownership | New `core/promoter.py` owns the entire resolution lifecycle |

## Section 1 — Schema (migration `20260517_0010`)

### New column on `memory_edges`

`resolution_state TEXT NOT NULL DEFAULT 'unresolved'` — only meaningful for
`edge_type='contradicts'`. Values:

- `unresolved` — resolver has not yet processed this edge (transient; clears next cron)
- `resolved` — auto-resolved via SUPERSEDE/ARCHIVE/REFINE
- `queued` — LLM ruled BLOCK; awaiting human via the review queue

### New table `contradiction_reviews`

| Column | Type | Purpose |
|---|---|---|
| `id` | AutoField pk | |
| `memory_id` | Text | the memory whose promotion was blocked |
| `edge_id` | Int → `memory_edges.id` | the `contradicts` edge ruled unresolved |
| `other_memory_id` | Text | the contradicting memory (denormalized for queue display) |
| `project` | Text, indexed | scoping, consistent with the project-column work |
| `llm_rationale` | Text | why the LLM declined to auto-resolve |
| `status` | Text | `open` / `resolved` / `dismissed` |
| `created_at` | ts | |
| `recheck_fingerprint` | Text | hash of both memories' `content` + `stage` at queue time |
| `retry_count` | Int default 0 | LLM-error retries (distinct from genuine BLOCK) |
| `resolved_at` | ts, nullable | |

Unique constraint on `(edge_id)` where `status='open'` — one open row per edge.

`recheck_fingerprint` drives approach C: each sweep recomputes it; a change means
either memory was edited/promoted/archived since queueing → row auto-reopens.

## Section 2 — `core/promoter.py`

New module. Owns the contradiction-resolution lifecycle. Exports:

- `resolve_contradictions_pass(session_id)` — async sweep, called by the hourly
  consolidate cron **after** `consolidate()` (consolidation may mint new edges).
- `has_blocking_contradiction(memory_id) -> tuple[bool, str]` — pure DB read; True
  if any incident edge has `resolution_state IN ('unresolved','queued')`.
- `_resolve_edge(edge, session_id)` — single-edge LLM resolution (Section 3).

### Call sites (two lines total)

1. `core/lifecycle.py` `can_promote()` (line 253) — add a clause:
   ```python
   blocked, why = promoter.has_blocking_contradiction(memory_id)
   if blocked:
       return False, why
   ```
2. Consolidate cron entry point — append
   `promoter.resolve_contradictions_pass(session_id)` after the consolidate call.

Resolution application reuses `consolidator._resolve_conflicts` so one code path
mutates memories. If circular import bites, extract `_resolve_conflicts` into
`promoter.py` and have `consolidator` call back.

### Sweep flow

1. Query `contradicts` edges with `resolution_state='unresolved'`.
2. Keep an edge only if ≥1 endpoint is promotion-eligible (`can_promote` minus the
   contradiction clause). Dormant conflicts between two stable memories are skipped.
3. C-recheck: for `resolved`/`queued` edges with an open review row, recompute
   `recheck_fingerprint`; if changed → flip edge to `unresolved`, close the old row
   `status='resolved'` reason `superseded-by-recheck`, re-enqueue.
4. `_resolve_edge` each unresolved eligible edge:
   - SUPERSEDE/ARCHIVE/REFINE → apply via `_resolve_conflicts`; edge → `resolved`.
   - BLOCK → edge → `queued`; insert `contradiction_reviews` row (`status='open'`).
5. Promotion gate is a pure read — promotion never calls the LLM.

Cron ordering: `consolidate → resolve_contradictions_pass → promotions`. Promotions
in the same run see the resolver's results.

Edge-claim: the sweep processes edges sequentially on the single `db` connection;
the `resolution_state != 'unresolved'` filter excludes already-touched edges across
runs. No separate claim entity.

### Latency

A freshly-contradicted memory waits at most one cron tick (~1hr) for its first
resolution. Blast radius of a transient `unresolved` edge = exactly its two endpoint
memories; every other memory promotes normally. This pause-before-crystallize is
intended behavior.

## Section 3 — LLM resolution contract (`_resolve_edge`)

One `call_llm()` per edge (via `core.llm.call_llm`). Input: both memories' full
content + stage + the `contradicts` edge's detection rationale (already on
`edge.metadata`).

Response schema (JSON, validated `core/schemas.py` style):

```json
{ "verdict": "SUPERSEDE|ARCHIVE|REFINE|BLOCK",
  "winner_id": "<memory id or null>",
  "merged_content": "<string, REFINE only, else null>",
  "rationale": "<string, required>" }
```

| Verdict | Meaning | Applied via |
|---|---|---|
| SUPERSEDE | one memory replaces the other | `_resolve_conflicts` — loser archived |
| ARCHIVE | one memory stale/wrong, drop it | `_resolve_conflicts` — loser → archived |
| REFINE | both partly right; merge into corrected memory | `_resolve_conflicts` — merged memory |
| BLOCK | genuinely unresolved | edge → `queued`, review row |

### Guardrails

- Behavioral framing (CLAUDE.md Rule 5): prompt describes contradictions as
  workflow-pattern divergence, not feelings.
- LLM failure / invalid JSON → treat as BLOCK, rationale `resolution-llm-error`,
  `retry_count` incremented. Never auto-mutate on a bad response.
- LLM-error rows auto-retry each pass up to N=3, then convert to a genuine
  human-queue BLOCK. (Distinct from genuine BLOCK, which waits for a human and only
  reopens via C-recheck.)
- Both endpoints `instinctive` → prompt forces BLOCK; auto-resolution may not
  silently kill a top-tier memory.

## Section 4 — Tests

All via conftest temp-dir fixtures (CLAUDE.md Rule 3 — never touch
`~/.claude/memory`). `call_llm` mocked throughout — zero real LLM calls.

- `has_blocking_contradiction`: no edges → not blocked; `unresolved`/`queued` →
  blocked; `resolved` → not blocked; edge incident to a different memory → not blocked.
- `resolution_state` transitions: `_resolve_edge` with mocked `call_llm` per verdict
  → assert edge state + review-row presence.
- C-recheck: queue a BLOCK row, mutate one memory, run pass → old row closed
  `superseded-by-recheck`, edge back to `unresolved`.
- LLM-error path: `call_llm` raises → edge `queued`, row `resolution-llm-error`,
  `retry_count=1`; 3 passes → converts to genuine BLOCK.
- Promotion gate integration: `can_promote` with a `queued` edge → False; resolve
  → True.
- Instinctive guard: both endpoints `instinctive`, LLM returns SUPERSEDE → forced BLOCK.

## Out of scope

- Surfacing the queue in `/memesis:recall` — follow-up; the table is the source of
  truth and a CLI/report reader is additive.
- Resolving non-`contradicts` edge types.
