---
title: Memory Lifecycle
type: concept
tags: [lifecycle, stages, promotion, demotion]
---

# Memory Lifecycle

The four-stage model governing how observations accumulate, mature, and persist across Claude Code sessions.

## Stage Progression

```
ephemeral → consolidated → crystallized → instinctive
```

Promotion advances one step at a time. Demotion can skip stages. Deprecation removes the row entirely (`memory.delete_instance()`).

## Stage Descriptions

**ephemeral** — Raw observations written during transcript extraction or via `/memesis:learn`. Not yet LLM-reviewed. Stale ephemeral rows (no activity for 30 days) are deprecation candidates.

**consolidated** — LLM-reviewed (Stage 2 consolidation). Has enrichment fields: `memory_kind`, `knowledge_type`, `subtitle`, `importance` (re-scored). May stay here indefinitely if importance < 0.75 and `reinforcement_count` < 3.

**crystallized** — Synthesized by [[Crystallizer]] into a pattern-level insight. Episodic details stripped. Source consolidated memories are archived (`subsumed_by` set). Permanent record — does not expire.

**instinctive** — Always injected at session start. Requires `importance > 0.85` and usage in 10+ distinct sessions. Never auto-expires. Used for behavioral rules, hard constraints, and the self-model.

## Gating

See [[promotion-gates]] for the full gate logic at each transition.

See [[importance-rubric]] for how importance scores are assigned and what bands trigger promotion.

## Demotion

Triggered by [[LifecycleManager]] when `injection_count >= 10` and `usage_count == 0` (injected repeatedly but never acted on). Demotes one stage.

## Deprecation

Triggered by staleness (30 days no activity) at ephemeral stage, or explicit `/memesis:forget`. Removes the row from the DB.

## Contradiction Effect

An unresolved `contradicts` edge blocks promotion from consolidated → crystallized. Affects only the two endpoint memories; all others promote normally. Resolved by `resolve_contradictions_pass` in the hourly cron.

## Key Files

- `core/lifecycle.py` — [[LifecycleManager]], gate logic
- `core/crystallizer.py` — [[Crystallizer]], synthesis engine
- `core/self_reflection.py` — [[SelfReflector]], hypothesis path
- `core/models.py` — `Memory` ORM model
