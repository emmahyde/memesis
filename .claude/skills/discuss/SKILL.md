---
name: discuss
description: >
  Situational awareness and next-steps proposal for the memesis project. Reads the current
  state of the codebase, memory DB, recent sessions, and known issues, then proposes the
  most valuable next actions. Use whenever you want to orient, plan, or decide what to work
  on. Triggers on: "what should we work on", "what's next", "memesis:discuss", "discuss",
  "what's the state of the project", "orient me", "what needs attention", /memesis:discuss.
---

# Discuss — Situational Next Steps

**Invoked as:** `/memesis:discuss`

Read the current project state. Propose the most valuable next steps. Be specific and opinionated.

---

## What to Read

Gather context from these sources in parallel:

### 1. Recent commits

```bash
rtk git log --oneline -15
```

What was just completed? Is there a direction already established?

### 2. Current git status

```bash
rtk git status
```

Uncommitted changes signal work in progress. Don't propose something orthogonal to an active direction.

### 3. Memory DB state

```python
import os, sys
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Memory, ConsolidationLog, Observation
init_db(project_context=os.getcwd())

from collections import Counter
stage_dist = Counter(m.stage for m in Memory.select().where(Memory.archived_at.is_null()))
print("Stage distribution:", dict(stage_dist))

recent_logs = list(ConsolidationLog.select().order_by(ConsolidationLog.timestamp.desc()).limit(10))
print("Recent actions:", dict(Counter(r.action for r in recent_logs)))
```

### 4. Known issues and planning state

```bash
ls .planning/ 2>/dev/null && cat .planning/*.md 2>/dev/null | head -100
```

What decisions are pending? What risks are flagged? What is the current tier list?

### 5. Test suite health

```bash
uv run pytest tests/ -q --tb=no 2>&1 | tail -5
```

Failing tests block everything else.

### 6. Open questions in memory

```python
from core.models import Memory
open_qs = Memory.select().where(
    Memory.kind == 'open_question',
    Memory.resolved_at.is_null(),
    Memory.archived_at.is_null()
)
for q in open_qs:
    print(f"  [{q.stage}] {q.title}")
```

---

## How to Propose Next Steps

After gathering context, propose 2-4 concrete next steps ordered by:

1. **Blockers first** — failing tests, P0 risks (RISK-01 PreCompact timeout, RISK-04 secret persistence)
2. **High-signal / low-cost** — Tier 0 bug fixes with known root causes and small change surface
3. **Strategic** — work that unlocks other work or continues the current direction from recent commits
4. **Quality** — test coverage gaps, observability improvements

For each proposed step:
```
### [N]. [Title]
Why: [1-2 sentences — why this is the right next thing]
What: [Specific action — file, function, behavior to change]
Skill: [/memesis:<skill> to invoke if applicable]
```

---

## Tone

Be direct. Don't propose everything — propose the best 2-4 things given the actual state. If the project is in a clear direction (tests green, no blockers), say so and propose the natural continuation. If there are blockers, lead with them. If genuinely unclear, ask one clarifying question.

Don't hedge. Propose. The user can redirect.
