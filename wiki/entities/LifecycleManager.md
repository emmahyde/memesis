---
title: LifecycleManager
type: entity
kind: class
file: core/lifecycle.py
---

# LifecycleManager

`core/lifecycle.py` — manages all memory stage transitions with validation and logging.

## Responsibilities

- Validates and executes promote/demote/deprecate operations on `Memory` rows.
- Implements the two-path crystallization gate (`_can_promote_to_crystallized`).
- Implements the instinctive gate (`_can_promote_to_instinctive`).
- Surfaces promotion candidates and demotion/deprecation candidates for the cron.
- Computes Zipf-style instinctive coverage statistics (`get_instinctive_coverage`).

## Stage Order

```python
STAGE_ORDER = ['ephemeral', 'consolidated', 'crystallized', 'instinctive']
```

Promotion advances one step at a time. Demotion can skip stages.

## Key Constants

| Constant | Default | Override |
|---|---|---|
| `MIN_REINFORCEMENT_SPAN_DAYS` | `0` | Hardcoded |
| `CRYSTALLIZE_IMPORTANCE_THRESH` | `0.75` | `MEMESIS_CRYSTALLIZE_IMPORTANCE_THRESH` env var |

## Promotion Gate — consolidated → crystallized

See [[promotion-gates]] and [[importance-rubric]] for full detail.

Two paths (either qualifies):
1. `importance >= CRYSTALLIZE_IMPORTANCE_THRESH` at any `reinforcement_count`
2. `reinforcement_count >= 3` + `_has_spaced_reinforcement`

Prerequisite: `memory_kind` non-NULL (except `kind == 'open_question'`). Blocked by unresolved `contradicts` edge.

## Promotion Gate — crystallized → instinctive

`importance > 0.85` AND used in 10+ distinct sessions (`RetrievalLog` with `was_used=1`).

## Demotion Candidates

Memories with `injection_count >= 10` and `usage_count == 0` at crystallized or instinctive stage. (`core/lifecycle.py:188`)

## Deprecation Candidates

Ephemeral memories with no injection or usage activity in the last 30 days. (`core/lifecycle.py:218`)

## Related

- [[Crystallizer]] — calls `get_promotion_candidates()` and drives synthesis
- [[promotion-gates]] — concept page for gate logic
- [[memory-lifecycle]] — concept page for the full stage model
