---
name: new-rule
description: This skill should be used when the user asks to "add a rule", "create a rule", "make a rule", "never let me/you do X", "always do Y before Z", or wants to author an enforced guardrail the agent must obey.
---

# New Rule — Author an Enforced Guardrail

Create a `Rule`: an enforced guardrail, distinct from a memory. The PreToolUse
hook (`hooks/pre_tool_guard.py`) evaluates every active rule against each tool
call and soft-blocks violations.

## Usage

```
/memesis:new-rule [natural-language rule]
/memesis:new-rule "never run rm -rf outside /tmp"
/memesis:new-rule "always run tests before committing"
```

## Procedure

1. Restate the rule the user wants in one imperative line.
2. Choose the narrowest `check_kind` that captures it:
   - `forbid_bash_pattern` — `check_arg` is a regex matched against Bash commands
   - `forbid_path_edit` — `check_arg` is a glob matched against edited file paths
   - `require_absent` — `check_arg` is a regex that must not appear in tool input
   - `semantic` — fuzzy rule; `check_arg` is the rule text, LLM-judged at enforcement
3. Choose `severity`: `block` (deny the call), `ask` (defer to the user), `warn`
   (surface only, never block). Default to `block` for clear prohibitions.
4. Choose `scope`: omit for a global rule, or pass a project slug / path glob to
   limit it.
5. Show the user the resolved rule spec and confirm before inserting.
6. Insert the rule with `status='active'` — a user-authored rule is enforced
   immediately (unlike LLM-proposed rules, which start `proposed`).
7. Confirm the rule id and that it is now in force.

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db, get_commit_ref
from core.models import Rule
from core.rules import CHECK_KINDS
init_db(project_context=os.getcwd())

assert check_kind in CHECK_KINDS
rule = Rule.create(
    text="never run rm -rf outside /tmp",
    check_kind="forbid_bash_pattern",
    check_arg=r"rm\s+-rf\s+(?!/tmp)",
    severity="block",
    status="active",        # user-authored — enforced immediately
    scope=None,             # global
    commit_ref=get_commit_ref(),
)
print(f"Rule {rule.id[:8]} active.")
```

## Safety

- A `block` rule that is too broad will stall legitimate work — prefer a precise
  regex/glob, or use `severity='ask'` when unsure.
- Test the predicate against an example tool call (`core.rules.evaluate_rule`)
  before telling the user it is in force.
- Rules are listed, disabled, or confirmed via `/memesis:rules`.
