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
/memesis:forget abc12345 --confirm
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
   - **Pending-delete stage (two-phase):** Memories that were automatically scheduled for deletion by the consolidator are placed in `stage='pending_delete'` first. They remain in the database until the TTL expires (default 7 days, configurable via `MEMESIS_PENDING_DELETE_TTL_DAYS` env var) or until explicitly hard-deleted with `--confirm`.
   - **Hard delete of pending-delete memory (with `--confirm` flag):** When a memory is in `stage='pending_delete'`, pass `--confirm` to execute immediate hard deletion via `Memory.hard_delete(memory_id)`. This cascades to FTS5 and vec_memories. This is irreversible.
   - **Delete (with `--permanent` flag):** Call `memory.delete_instance()`. FTS entry is cleaned up by the model's `delete_instance` override. This is irreversible. Use for memories in any stage.
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

# Hard-delete a pending_delete memory (--confirm flag):
# Used when the memory is already in stage='pending_delete' (scheduled by consolidator)
# and the user wants immediate, permanent removal.
#
# memory = Memory.get_by_id(memory_id)
# if memory.stage != 'pending_delete':
#     raise ValueError("--confirm only applies to pending_delete memories; use --permanent for others")
# Memory.hard_delete(memory_id)
# ConsolidationLog.create(
#     action='pruned',
#     memory_id=memory_id,
#     from_stage='pending_delete',
#     to_stage='deleted',
#     rationale="User confirmed hard deletion via /memesis:forget --confirm",
# )

# Or for permanent deletion of any memory (--permanent flag):
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
- **Two-phase delete for consolidator-scheduled removals:** Memories automatically marked by the consolidator land in `stage='pending_delete'` and survive for TTL days (default 7, env `MEMESIS_PENDING_DELETE_TTL_DAYS`). Use `--confirm` to skip the TTL wait.
- **`--confirm` flag:** Hard-deletes a memory that is already in `stage='pending_delete'`. Provides an explicit escape hatch to immediately execute a consolidator-scheduled deletion without waiting for TTL expiry. Requires the memory to already be in `pending_delete` stage.
- **Permanent deletion requires `--permanent` flag** and explicit confirmation (applies to any stage)
- **Instinctive memories require double-confirmation** — warn that this affects behavioral guidelines
- All actions are logged to `ConsolidationLog` with rationale and timestamp

## Examples

```
/memesis:forget "old API endpoint pattern"
/memesis:forget abc12345
/memesis:forget abc12345 --confirm
/memesis:forget abc12345 --permanent
```
