---
name: dashboard
description: Use when the user asks for "memory dashboard", "memesis dashboard", "show dashboard", or wants an overview of the memory system. Displays a combined summary pulling from stats, health, and usage data in one view.
---

# Dashboard — Memory System Overview

Quick combined view of the memory system. For deeper analysis, use the focused skills:

- **`/stats`** — Counts by stage, importance distribution, cross-project view
- **`/health`** — Relevance health: archival candidates, rehydration candidates
- **`/threads`** — Narrative thread visualization
- **`/breakdown`** — Injection counts, usage patterns, importance trends

## Usage

```
/memesis:dashboard
```

## Procedure

1. Initialize the database with `init_db(project_context=os.getcwd())`
2. Gather summary data:
   - Memory counts per stage (consolidated, crystallized, instinctive, archived)
   - Top 3 most-used memories
   - Number of archive candidates (relevance < 0.20)
   - Number of active threads
3. Render a compact summary table

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory, NarrativeThread
from core.relevance import RelevanceEngine
init_db(project_context=os.getcwd())

# Counts
for stage in ('consolidated', 'crystallized', 'instinctive'):
    count = Memory.by_stage(stage).count()
    # render: stage — count

archived = Memory.select().where(Memory.archived_at.is_null(False)).count()
# render: archived — count

# Top used
top = Memory.select().where(
    Memory.archived_at.is_null()
).order_by(Memory.usage_count.desc()).limit(3)

# Threads
thread_count = NarrativeThread.select().count()

# Health snapshot
engine = RelevanceEngine()
candidates = engine.get_archival_candidates(project_context=os.getcwd())
```

4. Present as a compact dashboard with one-line summaries per section
5. Suggest the focused skill for any section the user wants to explore deeper

## Examples

```
/memesis:dashboard
```
