---
name: pipeline
description: >
  Consolidation pipeline trace for memesis. Given a session ID or time window, traces what
  raw text was in the ephemeral buffer, which Observation rows were extracted, what the LLM
  decided for each, and what Memory rows resulted. Use when a user reported something was
  said or done but no memory was created, or when you need to understand why a specific
  observation was pruned. Triggers on: "why wasn't X remembered", "trace consolidation",
  "what happened to observation", "pipeline trace", "observation not captured",
  /memesis:pipeline.
---

# Consolidation Pipeline Trace

**Invoked as:** `/memesis:pipeline`

For a session or time window: trace the full observation-to-memory path. Find where signal was lost and why.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/data-model.md`

---

## Workflow

### Step 1 — Locate the ephemeral buffer

```bash
ls -la ${CLAUDE_PLUGIN_DATA}/ephemeral/ | tail -10
```

Read the most recent file(s) to understand what raw observations went in.

### Step 2 — Find all Observations for the session

```python
import os, sys, json
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Observation, Memory, ConsolidationLog
init_db(project_context=os.getcwd())

session_id = "your-session-id"

obs = Observation.select().where(
    Observation.session_id == session_id
).order_by(Observation.ordinal)

for o in obs:
    print(f"[{o.status or 'kept'}] ord={o.ordinal} → mem={o.memory_id} | {str(o.content)[:60]}")
```

Note: `Observation.ordinal` is 0-indexed. LLM `obs_ids` are 1-indexed — add 1 when joining.

### Step 3 — Find the consolidation decision for a pruned observation

```python
target_ordinal = 3  # 0-indexed; LLM saw this as obs_id=4

logs = ConsolidationLog.select().where(
    ConsolidationLog.session_id == session_id,
    ConsolidationLog.action.in_(['keep', 'prune', 'merge'])
)

for log in logs:
    response = json.loads(log.llm_response or '{}')
    for d in response.get('decisions', []):
        if isinstance(d, dict) and (target_ordinal + 1) in d.get('obs_ids', []):
            print(f"Log {log.id}: action={log.action}")
            print(f"Rationale: {log.rationale}")
```

### Step 4 — Check for deduplication

```python
import hashlib

content_hash = hashlib.sha256(obs_content.encode()).hexdigest()
existing = Memory.select().where(Memory.content_hash == content_hash)
if existing.exists():
    print(f"Deduplicated against: {existing.first().id}")
# Vector dedup leaves trace in ConsolidationLog action='promoted'
```

### Step 5 — Check the significance filter criteria

Read `core/prompts.py` (`CONSOLIDATION_PROMPT`). Was the pruning reasonable, or is the filter too aggressive?

### Step 6 — Classify each failure

| Scenario | Classification |
|---------|---------------|
| Low-signal observation correctly pruned | Correct |
| Novel decision/correction pruned as "redundant" | Bug — filter too aggressive |
| Kept but deduped against unrelated memory | Bug — incorrect similarity score |
| No Observation row exists | Stage 0 miss — check cursor state |

---

## Reporting Format

```
## Pipeline Trace: Session [ID] / Window [dates]

### Buffer Contents
[N observations in ephemeral buffer]

### Observation Disposition
[Table: ordinal | content snippet | status | memory_id | failure mode]

### Gap Analysis
[Which observations should have produced memories and why they didn't]

### Root Cause
[File:line — LLM prompt too aggressive? Dedup threshold wrong? Hook didn't fire?]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```
