---
name: rules
description: This skill should be used when the user asks to "list rules", "show rules", "review proposed rules", "approve a rule", "activate a rule", "disable a rule", or wants to manage enforced guardrails.
---

# Rules — Review and Manage Enforced Guardrails

List `Rule` rows, confirm LLM-proposed rules into force, and disable rules.
Rules are enforced by the PreToolUse hook (`hooks/pre_tool_guard.py`).

## Usage

```
/memesis:rules                       list all rules grouped by status
/memesis:rules proposed              list only proposed (pending) rules
/memesis:rules approve <id>          proposed -> active (now enforced)
/memesis:rules disable <id>          active -> disabled (no longer enforced)
```

## Rule lifecycle

- `proposed` — LLM-derived from a memory by the cron sweep; **inert**, never
  enforced until approved.
- `active` — enforced on every tool call.
- `disabled` — retained for audit, not enforced.

## Procedure

1. **List:** select rules, group by `status`. For each show `id[:8]`, `text`,
   `check_kind`, `severity`, `scope` (or "global"), and `violation_count`.
2. **Approve:** look up the rule by id prefix; show its full spec; confirm with
   the user; set `status='active'`, bump `updated_at`.
3. **Disable:** look up by id prefix; set `status='disabled'`, bump
   `updated_at`. For a `proposed` rule prefer disabling over deleting so it is
   not re-proposed from the same source memory.
4. When approving a rule, mention its `source_memory_id` if set so the user can
   trace where it came from.

## Implementation

```python
import os, sys
from datetime import datetime
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Rule
init_db(project_context=os.getcwd())

# List
for r in Rule.select().order_by(Rule.status, Rule.created_at):
    scope = r.scope or "global"
    print(f"{r.id[:8]}  [{r.status}]  {r.severity}  {scope}  — {r.text}")

# Approve a proposed rule (after user confirmation)
rule = Rule.get_by_id(rule_id)
rule.status = "active"
rule.updated_at = datetime.now().isoformat()
rule.save()

# Disable an active rule
rule = Rule.get_by_id(rule_id)
rule.status = "disabled"
rule.updated_at = datetime.now().isoformat()
rule.save()
```

## Safety

- Approving a rule makes it enforced immediately — confirm the predicate is
  precise before activating.
- A rule with a high `violation_count` is either valuable or miscalibrated;
  surface that to the user when listing.
