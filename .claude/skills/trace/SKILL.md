---
name: trace
description: >
  Hook chain trace for a specific memesis session. Walks SessionStart → UserPromptSubmit →
  PreCompact for a given session ID, checking each hook's output, observations produced,
  consolidation decisions made, and resulting Memory rows. Use when memories aren't appearing,
  injection is silent, or consolidation seems to not be running. Triggers on: "trace session",
  "hook chain", "why no memory", "injection silent", "consolidation not running",
  "what happened in session", /memesis:trace.
---

# Hook Chain Trace

**Invoked as:** `/memesis:trace`

Walk the hook chain for a specific session. Find broken links: which hook ran, what it produced, and where the chain went silent.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/data-model.md`

---

## Workflow

### Step 1 — Get the session ID

Either provided directly, or find it via:
```python
import os, sys
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import ConsolidationLog
init_db(project_context=os.getcwd())

recent = ConsolidationLog.select().order_by(ConsolidationLog.timestamp.desc()).first()
print(recent.session_id)
```

Or check the most recent ephemeral buffer file:
```bash
ls -lt ${CLAUDE_PLUGIN_DATA}/ephemeral/ | head -5
```

### Step 2 — Trace SessionStart

```python
session_id = "your-session-id"

from core.models import RetrievalLog, RetrievalCandidate, ConsolidationLog

logs = RetrievalLog.select().where(RetrievalLog.session_id == session_id)
for log in logs:
    candidates = RetrievalCandidate.select().where(
        RetrievalCandidate.retrieval_log_id == log.id
    ).order_by(RetrievalCandidate.rank)
    print(f"Injection round {log.id}: {candidates.count()} candidates")

rehydrated = ConsolidationLog.select().where(
    ConsolidationLog.session_id == session_id,
    ConsolidationLog.action == 'rehydrated'
)
print(f"Rehydrated: {rehydrated.count()}")
```

### Step 3 — Trace UserPromptSubmit

```python
prompt_injections = RetrievalLog.select().where(
    RetrievalLog.session_id == session_id,
    RetrievalLog.retrieval_type == 'prompt'
)
print(f"Dynamic prompt injections: {prompt_injections.count()}")
```

### Step 4 — Trace PreCompact (consolidation)

```python
consolidation_actions = ConsolidationLog.select().where(
    ConsolidationLog.session_id == session_id
).order_by(ConsolidationLog.timestamp)

for action in consolidation_actions:
    print(f"{action.action}: {action.memory_id} | {action.rationale[:80]}")
```

### Step 5 — Check Observations

```python
from core.models import Observation

obs = Observation.select().where(Observation.session_id == session_id)
for o in obs:
    print(f"obs {o.ordinal}: status={o.status}, memory_id={o.memory_id}")
```

### Step 6 — Map the chain

```
SessionStart: N injections, M rehydrations
UserPromptSubmit: K dynamic retrievals
PreCompact: P observations extracted → Q kept, R pruned → S memories resulted
```

Identify broken links: a hook that produced zero output when it should have produced some.

---

## Common Failure Modes

| Symptom | Check |
|---------|-------|
| No injections at SessionStart | `RetrievalLog` empty for session; check `Memory.by_stage('instinctive')` count |
| No observations extracted | Ephemeral buffer file missing or empty; check `${CLAUDE_PLUGIN_DATA}/ephemeral/` |
| Observations extracted but no memories | `Observation.status='filtered'` — LLM pruned everything; check consolidation prompt in `core/prompts.py` |
| Consolidation log empty | PreCompact hook didn't run; check hook registration in settings |

---

## Reporting Format

```
## Trace: Session [ID]

### Chain Summary
SessionStart:      N injections, M rehydrations
UserPromptSubmit:  K dynamic retrievals
PreCompact:        P observations → Q kept, R pruned → S memories

### Findings
[Each broken link with file:line root cause]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```
