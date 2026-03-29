---
name: memory
description: This skill should be used when the user asks to "show my memories", "search memories", "what do you remember", "memory stats", "memory status", "browse memories", or wants to inspect, search, or diagnose the memory system health.
---

# Memory — Browse, Search, and Diagnose

Inspect the memory store — browse by stage, search by content, or run full system diagnostics.

## Subcommands

### stats

Show counts per lifecycle stage, importance distribution, and recent activity.

```python
from core.storage import MemoryStore
store = MemoryStore(project_context=os.getcwd())
for stage in ('instinctive', 'crystallized', 'consolidated', 'ephemeral'):
    print(f"{stage}: {len(store.list_by_stage(stage))}")
print(f"archived: {len(store.list_archived())}")
```

### browse [stage]

List memories in a stage with titles, summaries, and importance scores. Valid stages: `instinctive`, `crystallized`, `consolidated`.

```python
memories = store.list_by_stage("crystallized")
for m in memories:
    print(f"  [{m['importance']:.2f}] {m['title']} — {m['summary']}")
```

### search [query]

Full-text search across all memories via FTS5. Return ranked results with summaries.

```python
from core.retrieval import RetrievalEngine
engine = RetrievalEngine(store)
results = engine.active_search(query, session_id)
```

### status

Comprehensive diagnostics:

1. **Memory counts** by stage (including archived)
2. **Relevance distribution** — score all active memories, show top 10 and bottom 10
3. **Archive candidates** — memories approaching the 0.15 archival threshold
4. **Rehydration candidates** — archived memories above 0.30 that could reactivate
5. **Self-model state** — current tendencies and last update date
6. **Consolidation history** — recent keep/prune/promote decisions
7. **Token budget** — percentage of context window consumed by memory injection

```python
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector

relevance = RelevanceEngine(store)
scored = relevance.score_all(project_context=os.getcwd())
archive_candidates = relevance.get_archival_candidates()
rehydration_candidates = relevance.get_rehydration_candidates(project_context=os.getcwd())
```

## Usage

```
/memesis:memory stats
/memesis:memory browse crystallized
/memesis:memory search "payment pipeline"
/memesis:memory status
```
