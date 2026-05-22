---
title: Promotion Gates
type: concept
tags: [promotion, lifecycle, crystallization, instinctive, gates]
---

# Promotion Gates

The conditions that must all pass before [[LifecycleManager]] will advance a memory to the next stage. Implemented in `core/lifecycle.py:can_promote` and the private `_can_promote_to_*` methods.

## ephemeral → consolidated

No gate. Always valid during the consolidation pass. (`core/lifecycle.py:279`)

## consolidated → crystallized

`core/lifecycle.py:335` — `_can_promote_to_crystallized`

**Prerequisite check** (fails fast before path checks):
- `memory_kind` must be non-NULL, unless `memory.kind == 'open_question'`. Open-question rows carry `memory_kind=NULL` by design and are exempt.
- `promoter.has_blocking_contradiction(memory_id)` must return `False`. An unresolved `contradicts` edge on any incident memory_edge blocks promotion.

**Path 1 — High-importance fast path:**
- `importance >= CRYSTALLIZE_IMPORTANCE_THRESH` (default 0.75, overridable via `MEMESIS_CRYSTALLIZE_IMPORTANCE_THRESH`)
- Passes at any `reinforcement_count`
- Rationale: a rare-but-pivotal memory should not be held back by low reinforcement frequency

**Path 2 — Standard path:**
- `reinforcement_count >= 3`
- `_has_spaced_reinforcement` passes (checks `ConsolidationLog` for distinct calendar days of reinforcement; `MIN_REINFORCEMENT_SPAN_DAYS = 0` so this is trivially satisfied with no log entries)

Either path is sufficient.

## crystallized → instinctive

`core/lifecycle.py:405` — `_can_promote_to_instinctive`

Both conditions required:
- `importance > 0.85`
- `_count_unique_sessions(memory_id) >= 10` (counts distinct `session_id` values in `RetrievalLog` where `was_used=1`)

## Hypothesis path (kind='hypothesis' only)

`core/self_reflection.py:660` — `can_promote_hypothesis`

Non-hypothesis memories bypass this gate. For hypothesis rows:
- `evidence_count >= 3`
- No `contradicts` edge touching the memory (bidirectional query on `memory_edges`)

After promotion: `memory.kind` cleared to `None`.

## Crystallizer Batch Cap

[[Crystallizer]] caps crystallization at `CRYSTALLIZE_BATCH_LIMIT` (default 10) per cron invocation, sorted highest-importance-first. The gate logic is correct for all candidates; the cap just controls throughput.

## Cron Order

Cron sequence is fixed: consolidation (mints `contradicts` edges) → `resolve_contradictions_pass` → promotions. Promotions in a given tick see all edges resolved in that same tick.

## Related

- [[LifecycleManager]] — executes gate checks
- [[importance-rubric]] — how importance scores are set
- [[memory-lifecycle]] — full stage model
- [[memory-kind-taxonomy]] — `memory_kind` requirement at crystallization
