---
name: failures
description: >
  Observation failure audit for memesis. Audits the observations table for a session or time
  window: classifies failure modes (filtered, never processed, deduped), estimates false-negative
  rate, checks if the significance filter is miscalibrated. Use when auditing consolidation
  quality or diagnosing systematic under-observation. Triggers on: "observation failures",
  "what got filtered", "why wasn't this captured", "significance filter", "consolidation
  quality", "under-observation", /memesis:failures.
---

# Observation Failure Audit

**Invoked as:** `/memesis:failures`

Audit the `observations` table for a window. Classify each failure mode. Estimate false-negative rate. Check if the significance filter needs tuning.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/data-model.md`

---

## Failure Mode Classification

| Status | memory_id | Classification |
|--------|----------|----------------|
| `filtered` | NULL | LLM pruned it — check `ConsolidationLog.rationale` |
| NULL | NULL | Extracted but consolidation never processed — cron/hook gap |
| NULL | NOT NULL | Kept and consolidated — normal |
| (row missing) | — | Never extracted — Stage 0 miss; check cursor state |

---

## Workflow

### Step 1 — Query observations for the window

```python
import os, sys, json
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Observation
init_db(project_context=os.getcwd())

window_obs = Observation.select().order_by(Observation.created_at.desc()).limit(100)

status_counts = {}
for o in window_obs:
    key = o.status or 'null'
    status_counts[key] = status_counts.get(key, 0) + 1

print("Observation status distribution:", status_counts)
```

### Step 2 — Spot-check each failure class

```python
from core.models import ConsolidationLog

filtered = Observation.select().where(
    Observation.status == 'filtered',
    Observation.memory_id.is_null()
).limit(10)

for o in filtered:
    log = ConsolidationLog.get_or_none(
        ConsolidationLog.session_id == o.session_id,
        ConsolidationLog.action == 'prune'
    )
    print(f"Filtered: '{str(o.content)[:80]}'\n  Reason: {log.rationale if log else 'no log'}\n")
```

### Step 3 — Estimate false-negative rate

Manually sample 10 pruned observations and score each:
- Genuinely novel and durable? → should have been kept (false negative)
- Correctly pruned as ephemeral/redundant? → correct prune

False-negative rate > 20% indicates the filter is too aggressive.

### Step 4 — Check significance filter criteria

Read `CONSOLIDATION_PROMPT` in `core/prompts.py`. Common miscalibration: treating explicit corrections and preference signals the same as incidental observations. Both should pass; the filter should target genuinely ephemeral content.

### Step 5 — Steelman before recommending filter changes

Filter tuning is high-risk: raising sensitivity increases noise, lowering it loses signal. The strongest case against any filter change: the sample may be unrepresentative or the pruned items are genuinely low-value in hindsight. Falsifying condition: the sample includes explicit corrections rated low-importance by the LLM.

---

## Reporting Format

```
## Observation Failure Audit: [Session / Window]

### Status Distribution
filtered: N | null+no_memory: N | null+memory: N (kept)

### Failure Class Breakdown
[Each class with examples and root cause]

### False-Negative Estimate
[N/10 correctly pruned; false-negative rate = X%]

### Filter Assessment
[Is CONSOLIDATION_PROMPT calibrated correctly?]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```
