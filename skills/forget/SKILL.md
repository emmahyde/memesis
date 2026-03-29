---
name: forget
description: This skill should be used when the user asks to "forget this", "delete memory", "remove that memory", "I don't want you to remember", or wants to deprecate, archive, or permanently remove a stored memory.
---

# Forget — Remove or Archive a Memory

Delete or deprecate a memory from the store. Archived memories are preserved for audit but excluded from injection.

## Usage

```
/memesis:forget [memory_id or topic description]
```

## Procedure

1. Search for matching memories by ID or topic (FTS5 search)
2. Display matches with title, stage, importance, and relevance score
3. Ask for explicit confirmation before proceeding
4. Execute the removal:
   - `LifecycleManager.deprecate()` — moves file to `archived/`, removes from DB index
   - All actions logged to `consolidation_log` with rationale
5. Confirm what was removed

## Safety

- Deleting an `instinctive` memory requires explicit double-confirmation — these directly affect behavioral guidelines and self-model
- All deletions are logged to `meta/consolidation-log` with rationale
- Files are moved to `archived/` directory, not permanently deleted — recovery is possible

## Implementation

```python
from core.storage import MemoryStore
from core.lifecycle import LifecycleManager

store = MemoryStore(project_context=os.getcwd())
lifecycle = LifecycleManager(store)

# Search by topic
results = store.search_fts("old API endpoint pattern", limit=5)

# After user confirms:
lifecycle.deprecate(memory_id, rationale="User requested removal via /memesis:forget")
```

## Examples

```
/memesis:forget mem_abc123
/memesis:forget "old API endpoint pattern"
```
