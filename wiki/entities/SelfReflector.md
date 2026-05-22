---
title: SelfReflector
type: entity
kind: class
file: core/self_reflection.py
---

# SelfReflector

`core/self_reflection.py` — reviews consolidation history and maintains the self-model instinctive memory.

## Status

**Experimental.** Opt-in via `MEMESIS_EXPERIMENTAL_MODULES=self_reflection`. The retrieval scoring path excludes this module unless opted in. The writer path (`reflect()` → `apply_reflection()`) runs unconditionally so hypothesis evidence accumulates for the Wave 3.2 promotion gate. (`core/self_reflection.py:26`)

## Seed Instinctive Memories

`ensure_instinctive_layer()` creates three memories at `stage='instinctive'` if absent:

| Title | Importance |
|---|---|
| Self-Model | 0.90 |
| Observation Habit | 0.85 |
| Compaction Guidance | 0.90 |

## Hypothesis Accumulation

`_write_hypothesis()` writes or increments a `kind='hypothesis'` Memory row per tendency. On first write: `evidence_count=1`. On subsequent calls with the same tendency title: increments `evidence_count`, appends `session_id` to `evidence_session_ids`.

## Hypothesis Promotion Gate

`can_promote_hypothesis(memory)` — `core/self_reflection.py:660`

Non-hypothesis memories return `True` immediately (gate is bypassed). For `kind='hypothesis'`:
1. `evidence_count >= 3`
2. No `contradicts` edge touching the memory (bidirectional)

After promotion: `memory.kind` is cleared to `None` (treated as durable finding). `promote_hypothesis()` writes a `ConsolidationLog` entry.

**Removed**: prior distinct-session requirement (`evidence_session_ids` length check). Dropped because `evidence_count` and `evidence_session_ids` can diverge when `session_id` is not supplied, permanently wedging a well-evidenced hypothesis.

## Related

- [[memory-lifecycle]] — stage model
- [[LifecycleManager]] — standard promotion path
- [[memory-kind-taxonomy]] — hypothesis is a kind value used only by this module
