---
name: audit
description: >
  Behavioral expectation audit for a memesis subsystem. Define the behavioral contract from
  code (file:line citations) and verify it holds against real DB state. Use when something
  seems wrong but you don't know where, or before making a change that touches a subsystem
  you need to understand precisely. Triggers on: "audit the X subsystem", "what should happen
  when", "behavioral contract", "verify behavior", "does this hold", "pre-change verification",
  /memesis:audit.
---

# Behavioral Expectation Audit

**Invoked as:** `/memesis:audit`

For a given subsystem: define what the code says should happen, observe what is actually happening, surface the gap.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/design-decisions.md`, `.claude/skills/index/references/data-model.md`

---

## Workflow

### Step 1 — Name the subsystem and scope

Be specific: "consolidation decision for ephemeral observations with importance < 0.3" is better than "consolidation."

### Step 2 — Extract the spec from code

Read the relevant source file and write the behavioral contract in plain language:
- **Inputs:** what state/data must exist
- **Process:** what the code does
- **Outputs:** what state/data results

Cite specific `file:line` for each claim.

### Step 3 — Identify the observable artifact

Where in the DB or filesystem can the contract's truth be measured? Examples:
- `ConsolidationLog` rows with specific action
- `Memory.stage` after consolidation
- `Observation.status` and `Observation.memory_id`
- `RetrievalLog` entries for a session

### Step 4 — Query the artifact

```python
import os, sys
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Memory, ConsolidationLog, Observation
init_db(project_context=os.getcwd())

# Example: verify prune actions produce archived memories
pruned_ids = [
    log.memory_id for log in
    ConsolidationLog.select().where(ConsolidationLog.action == 'prune')
    if log.memory_id
]

not_archived = Memory.select().where(
    Memory.id.in_(pruned_ids),
    Memory.archived_at.is_null()
)

for m in not_archived:
    print(f"VIOLATION: {m.id} pruned but not archived — stage={m.stage}")
```

### Step 5 — Compare to contract

Every deviation is a finding. Record `file:line` of the code responsible for the gap.

### Step 6 — Steelman before reporting

State the strongest case against your finding. What assumptions does it require? What would flip it? Then state the tiebreaker and recommendation.

---

## Key Constraints to Check

- `SHADOW_ONLY=True` in `core/observability.py` — pruning is logged but NOT executed. Prune-related audits must check the shadow JSONL log, not DB deletions.
- Peewee singleton — all DB access via `init_db()`, never `sqlite3.connect()`
- Ordinal mismatch: `Observation.ordinal` is 0-indexed; LLM `obs_ids` are 1-indexed (add 1 when joining)
- `uv run pytest tests/` — never bare `python3 -m pytest`

---

## Reporting Format

```
## Audit: [Subsystem / Question]

### Expected Behavior
[What the code says should happen, cited to file:line]

### Observed Behavior
[What the data/logs/tests show]

### Gap
[The difference, precisely]

### Root Cause
[File:line where divergence originates]

### Steelmanned Recommendation
Against: [Strongest case against. Wins if: ___.]
For: [What breaks the tie — cite code, logs, or tests.]
Recommendation: [Stated recommendation.]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```

"No gap found" is a valid finding. State it explicitly.
