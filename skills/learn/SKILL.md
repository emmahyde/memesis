---
name: learn
description: This skill should be used when the user asks to "remember this", "store this for later", "learn this", "save this observation", or explicitly wants to persist information across sessions. Also triggers when the agent identifies a correction, preference signal, or decision worth preserving.
---

# Learn — Store a Memory

Explicitly store an observation or fact for recall across future sessions.

## Usage

```
/memesis:learn [content]
/memesis:learn [correction] I suggested X but Y was right because Z
/memesis:learn [preference] User prefers A over B — reasoning: ...
```

## Procedure

1. Determine the appropriate stage based on content specificity:
   - `consolidated/` — specific facts, one-time observations, corrections
   - `crystallized/` — durable patterns reinforced across multiple sessions
2. Classify the observation type when apparent: `correction`, `preference_signal`, `shared_insight`, `domain_knowledge`, `workflow_pattern`, `self_observation`, `decision_context`
3. Tag with `type:<observation_type>` for downstream searchability
4. Tag with current project context via `os.getcwd()`
5. Write to the memory store using `MemoryStore.create()`
6. Confirm: what was stored, the stage, and the observation type

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")

from core.storage import MemoryStore
from core.prompts import OBSERVATION_TYPES

store = MemoryStore(project_context=os.getcwd())
memory_id = store.create(
    path="category/descriptive_filename.md",
    content=observation_text,
    metadata={
        "stage": "consolidated",
        "title": "Short descriptive title",
        "summary": "One-line summary under 150 chars",
        "tags": ["relevant", "tags", "type:observation_type"],
        "source_session": os.environ.get("CLAUDE_SESSION_ID", "unknown"),
    },
)
```

## Examples

- `/memesis:learn Emma prefers SQLite for local tools, PostgreSQL for production`
- `/memesis:learn The payment pipeline uses optimistic locking on invoice records`
- `/memesis:learn [correction] I suggested threads when asyncio was the right fit — pattern: I reach for familiar tools before checking ecosystem alternatives`
