---
title: Crystallizer
type: entity
kind: class
file: core/crystallizer.py
---

# Crystallizer

`core/crystallizer.py` — transforms consolidated memories into crystallized semantic insights via LLM synthesis.

## Responsibilities

- Fetches promotion candidates from [[LifecycleManager]].
- Groups related candidates by embedding cosine similarity (Phase 1) or tag overlap (Phase 2 fallback).
- Calls LLM to synthesize each group into one denser, pattern-level memory.
- Archives source memories (sets `archived_at`, records `subsumed_by`).
- Creates `subsumed_into` edges in `memory_edges` when the `causal_edges` flag is set.

## Batch Cap

```python
CRYSTALLIZE_BATCH_LIMIT = int(os.environ.get("MEMESIS_CRYSTALLIZE_BATCH_LIMIT", "10"))
```

Candidates are sorted highest-importance-first and capped at `CRYSTALLIZE_BATCH_LIMIT` per cron invocation. A large backlog drains over multiple ticks. (`core/crystallizer.py:124`)

## Grouping Strategy

1. **Embedding-based** (`_group_by_embeddings`): cosine similarity with adaptive threshold `max(0.75, P75_off_diagonal_sims)`, capped at 0.85. Uses stored embeddings from `memory_embeddings` via `get_vec_store()`.
2. **Tag-overlap fallback** (`_group_by_tags`): same `observation_type` tag + at least one shared non-type tag. Used when embeddings unavailable or texts too short (<10 chars).
3. Singletons (≤2 candidates, or ungrouped) each form their own group — still synthesized individually.

## Crystallized Memory

- `stage = 'crystallized'`
- `importance` = max of source group's importance scores (not a fixed value — preserves the instinctive-gate signal).
- Content = LLM insight + `source_pattern` provenance line + `Synthesized from: [titles]`.
- File written to `~/.claude/memory/crystallized/<safe_name>.md`.
- Dedup via `content_hash` (MD5 of body).

## Fallback

If LLM synthesis fails, `_fallback_promote` calls `LifecycleManager.promote()` on each memory without synthesis. (`core/crystallizer.py:495`)

## Related

- [[LifecycleManager]] — provides `get_promotion_candidates()`
- [[memory-lifecycle]] — stage model context
- [[promotion-gates]] — gate rules Crystallizer relies on
