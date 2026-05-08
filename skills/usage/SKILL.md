---
name: usage
description: Use when the user asks about memory injection patterns, retrieval hit rates, which memories get injected most, or "what memories are being used". Shows injection frequency, last-used timestamps, zombie detection, and retrieval hit/miss analysis. Does NOT overlap with /stats (memory counts/distribution) or /health (archival/relevance diagnostics).
---

# Usage — Memory Injection and Retrieval Tracking

View how memories are being used: which ones are injected most, when they were last seen, and which may have gone stale (zombies — stored but never retrieved).

## Usage

```
/memesis:usage                    # Show injection stats for current project
/memesis:usage --global           # Across all projects
/memesis:usage --zombies          # Only show memories never injected (injection_count=0)
/memesis:usage --top N            # Top N by injection count (default 20)
```

## Procedure

1. Run `python3 scripts/audit_retrieval.py` to get injection hit rate and zombie list.
2. For interactive inspection, query the database:
   - Injection counts: `Memory.select().order_by(Memory.injection_count.desc())`
   - Last-used: filter by `Memory.last_used_at` recency
   - Zombies: `injection_count = 0` and stage not ephemeral
3. Present results as a table: stage | title | injection_count | last_used_at | importance
4. Flag zombies (injection_count=0, age > 14 days) as candidates for `/memesis:forget`

## Interpretation

- **High injection count + low importance:** Memory may be over-broad — check if it should be narrowed.
- **injection_count=0 + crystallized/instinctive:** Zombie. Was promoted without being retrieved. Run `/memesis:health` to diagnose.
- **injection_count=0 + consolidated:** Normal for new memories, concerning after 30+ sessions.
- **Declining last_used_at across instinctive:** May indicate topic drift; consider `/memesis:reflect` to audit relevance.

## Related

- `/memesis:stats` — counts and distribution by stage
- `/memesis:health` — archival and relevance diagnostics
- `/memesis:forget` — archive memories that should no longer be injected
