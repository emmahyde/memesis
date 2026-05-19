---
name: lifecycle
description: >
  Lifecycle audit for memesis memories. Verifies stage transitions are happening correctly:
  identifies memories that should have been promoted but weren't, memories that shouldn't
  have been promoted but were, and memories stuck at a stage anomalously long. Use when stage
  transition rules were recently changed, or you suspect drift between code and data.
  Triggers on: "stage transitions wrong", "memories stuck", "lifecycle audit", "promotion rules",
  "why isn't X crystallized", "check lifecycle", /memesis:lifecycle.
---

# Lifecycle Audit

**Invoked as:** `/memesis:lifecycle`

Verify that all memories satisfy their stage's promotion criteria. Identify drift between the rules in code and the state in the DB.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/data-model.md`, `.claude/skills/index/references/design-decisions.md`

---

## Promotion Rules

From `core/lifecycle.py`:

| Transition | Gate |
|-----------|------|
| ephemeral → consolidated | Consolidation LLM decision (no automatic threshold) |
| consolidated → crystallized | `reinforcement_count >= 3` + temporal spacing (`MIN_REINFORCEMENT_SPAN_DAYS`) |
| crystallized → instinctive | `importance > 0.85` AND usage in 10+ distinct sessions |
| Any → archived | `relevance_score < ARCHIVE_THRESHOLD (0.15)` via `RelevanceEngine` |

---

## Workflow

### Setup

```python
import os, sys
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Memory, ConsolidationLog
from datetime import datetime, timedelta
init_db(project_context=os.getcwd())
```

### Step 1 — Under-promoted consolidated memories

```python
consolidated = Memory.select().where(
    Memory.stage == 'consolidated',
    Memory.archived_at.is_null()
)

for m in consolidated:
    if (m.reinforcement_count or 0) >= 3:
        print(f"ELIGIBLE for crystallize: {m.title} | rc={m.reinforcement_count} | imp={m.importance}")
```

### Step 2 — Over-promoted crystallized memories

```python
bad_crystal = Memory.select().where(
    Memory.stage == 'crystallized',
    Memory.archived_at.is_null(),
    Memory.reinforcement_count < 3
)
for m in bad_crystal:
    print(f"ANOMALY: {m.title} is crystallized but rc={m.reinforcement_count}")
```

### Step 3 — Instinctive promotion rule check

```python
for m in Memory.select().where(Memory.stage == 'instinctive', Memory.archived_at.is_null()):
    if (m.importance or 0) <= 0.85:
        print(f"ANOMALY: {m.title} is instinctive but importance={m.importance}")
```

### Step 4 — Stuck ephemerals

```python
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
stuck = Memory.select().where(
    Memory.stage == 'ephemeral',
    Memory.created_at < cutoff,
    Memory.archived_at.is_null()
)
print(f"Stuck ephemeral (>7 days): {stuck.count()}")
```

### Step 5 — Summarize and steelman

Count each anomaly class. Before recommending remediation, steelman: are these anomalies bugs in lifecycle logic, or intentional holds (`is_pinned=1`, manual overrides)?

---

## Common Anomaly Causes

| Anomaly | Likely cause |
|---------|-------------|
| Eligible consolidated not promoted | `can_promote()` not called; spacing not met; `lifecycle.promote()` not triggered |
| Crystallized with rc < 3 | Legacy row pre-dates rc tracking; or `promote()` called bypassing gate |
| Instinctive with importance ≤ 0.85 | Manual seeding at first-run; or importance edited after promotion |
| Stuck ephemeral | PreCompact didn't fire; cron gap; session ended abnormally |

---

## Reporting Format

```
## Audit: Lifecycle Stage Transitions

### Stage Distribution
ephemeral: N  |  consolidated: N  |  crystallized: N  |  instinctive: N  |  archived: N

### Findings
[Each anomaly class with count and examples]

### Root Cause
[File:line where gate is missing or misfiring]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```
