---
name: forget
description: This skill should be used when the user asks to "forget this", "delete memory", "remove that memory", "I don't want you to remember", or wants to archive or permanently remove a stored memory.
---

# Forget — Remove or Archive a Memory

Archive or delete a memory. Archived memories are excluded from injection but preserved for audit. Deletion is permanent.

## Usage

```
/memesis:forget [memory_id or topic description]
/memesis:forget "old API endpoint pattern"
/memesis:forget abc12345
```

## Procedure

1. Determine whether the argument is a memory ID (UUID prefix) or a topic description
2. **By ID:** Look up the memory directly via `Memory.get_by_id()`
3. **By topic:** Search via `Memory.search_fts()`, display ranked matches with title, stage, and importance — ask the user to select which memory to forget
4. Display the matched memory's full details: title, stage, importance, summary, created date
5. Ask for explicit confirmation before proceeding
6. **If the memory is `instinctive`:** Require double-confirmation — these directly affect behavioral guidelines and self-model. Warn the user.
7. Execute the removal:
   - **Archive (default):** Set `archived_at` to now. Memory is excluded from injection but remains searchable and recoverable.
   - **Delete (with `--permanent` flag):** Call `memory.delete_instance()`. FTS entry is cleaned up by the model's `delete_instance` override. This is irreversible.
8. Log the action to `ConsolidationLog` with rationale
9. Confirm what was done and whether it can be undone

## Implementation

```python
import os, sys
from datetime import datetime
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory, ConsolidationLog
init_db(project_context=os.getcwd())

# Search by topic
sanitized = Memory.sanitize_fts_term("old API endpoint pattern")
results = Memory.search_fts(sanitized, limit=5)

# Or look up by ID prefix
# memory = Memory.get_by_id("abc12345")

# After user confirms — archive:
memory = Memory.get_by_id(memory_id)
memory.archived_at = datetime.now().isoformat()
memory.save()

# Log the action
ConsolidationLog.create(
    action='archived',
    memory_id=memory.id,
    from_stage=memory.stage,
    to_stage=memory.stage,
    rationale="User requested removal via /memesis:forget",
)

# Or for permanent deletion (--permanent flag):
# memory.delete_instance()
# ConsolidationLog.create(
#     action='pruned',
#     memory_id=memory_id,
#     from_stage=memory.stage,
#     to_stage='deleted',
#     rationale="User requested permanent deletion via /memesis:forget --permanent",
# )
```

## Safety

- **Archive is the default** — memories can be recovered via `/memesis:health` (shows recently archived)
- **Permanent deletion requires `--permanent` flag** and explicit confirmation
- **Instinctive memories require double-confirmation** — warn that this affects behavioral guidelines
- All actions are logged to `ConsolidationLog` with rationale and timestamp

## Examples

```
/memesis:forget "old API endpoint pattern"
/memesis:forget abc12345
/memesis:forget abc12345 --permanent
```
