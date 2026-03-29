---
name: learn
description: This skill should be used when the user says "remember this", "store this", "save this", or "note that". Also triggers on corrections ("I was wrong about X"), preference signals ("I prefer X over Y", "use X not Y"), and any explicit request to persist a specific fact or observation across sessions. Does NOT trigger for multi-part structured knowledge or process documentation — use /teach for that.
---

# Learn — Store a Memory

Explicitly store a specific fact, correction, preference, or observation for recall across future sessions. Uses a two-tier write path: simple, well-defined facts go directly to consolidated storage; complex or ambiguous observations queue to the ephemeral buffer for consolidation review.

## Usage

```
/memesis:learn [content]
/memesis:learn I prefer Peewee over raw sqlite3 for query ergonomics
/memesis:learn [correction] I suggested asyncio but threads were right — the workload was CPU-bound
/memesis:learn The staging k8s cluster uses a non-standard ingress annotation for rewrite rules
```

## Procedure

1. **Classify the content** inline — no extra LLM call. Determine:
   - **Observation type**: one of `correction`, `preference_signal`, `shared_insight`, `domain_knowledge`, `workflow_pattern`, `self_observation`, `decision_context`
   - **Complexity**: is this a discrete, self-contained fact or does it require synthesis with other observations to be useful?

2. **Choose the write path** based on classification:

   **Path A — Direct write (consolidated stage)**
   Use when the observation is:
   - A correction (I said X, Y was right — the pattern is clear)
   - A preference signal (user chose A, pushed back on B)
   - A specific, bounded fact (tool config, project-specific constraint, naming convention)
   - Something that would lose nothing by being stored now vs. after consolidation

   **Path B — Ephemeral buffer**
   Use when the observation is:
   - A broad behavioral pattern that gains meaning from other session observations
   - Something that might reinforce or contradict an existing memory (consolidation handles conflict resolution)
   - Ambiguous in type or scope

3. **When uncertain** between paths, present the choice explicitly:
   > "This could go directly to consolidated storage (available immediately, no pruning risk) or queue with your other session observations for consolidation review (may get refined or merged with related memories). Which do you prefer?"

4. **Write the memory** using the appropriate path below.

5. **Confirm** what was stored: title, observation type, which path was used, and the memory ID (direct write) or buffer location (ephemeral).

## Implementation

### Path A — Direct write to consolidated

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db, get_vec_store
from core.models import Memory
init_db(project_context=os.getcwd())

import json
from datetime import datetime

memory = Memory.create(
    stage="consolidated",
    title="Short descriptive title (under 80 chars)",
    summary="One-line summary capturing the core insight (under 150 chars)",
    content=observation_text,
    tags=json.dumps(["type:observation_type", "relevant-context-tag"]),
    importance=0.7,
    project_context=os.getcwd(),
    source_session=os.environ.get("CLAUDE_SESSION_ID", "unknown"),
    created_at=datetime.now().isoformat(),
    updated_at=datetime.now().isoformat(),
)
print(f"Stored: {memory.id}")
```

Replace `observation_type` with one of: `correction`, `preference_signal`, `shared_insight`, `domain_knowledge`, `workflow_pattern`, `self_observation`, `decision_context`

### Path B — Ephemeral buffer with priority flag

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db, get_vec_store
from core.models import Memory
base_dir = init_db(project_context=os.getcwd())

import subprocess
from datetime import datetime

buffer_path = base_dir / "ephemeral" / f"session-{datetime.now().strftime('%Y-%m-%d')}.md"

# Prefix with [PRIORITY] so the consolidation cron treats this as always-keep
# Replace observation_type with the actual type string
priority_observation = f"[PRIORITY] {observation_text}"

subprocess.run(
    [sys.executable, "${CLAUDE_PLUGIN_ROOT}/hooks/append_observation.py",
     str(buffer_path), priority_observation, "--type", "observation_type"],
    check=True,
)
print(f"Queued to ephemeral buffer: {buffer_path}")
```

## Examples

**Direct write — specific preference**
```
/memesis:learn I prefer SQLite for local tools and single-machine projects; PostgreSQL for anything with concurrent writes or multiple services
```
Path A. Type: `preference_signal`. Bounded, actionable, no synthesis needed. Stored directly to consolidated.

**Direct write — correction**
```
/memesis:learn [correction] I suggested running the migration in a transaction — but SQLite doesn't support transactional DDL for ADD COLUMN. I need to check SQLite DDL constraints before recommending transaction wrapping.
```
Path A. Type: `correction`. The mistake pattern is clear and self-contained. Stored directly to consolidated.

**Direct write — domain knowledge**
```
/memesis:learn The staging k8s cluster (staging-k8s) uses nginx.ingress.kubernetes.io/rewrite-target: /$2 with a capture group in the path — standard rewrite annotations don't work there
```
Path A. Type: `domain_knowledge`. Specific, bounded, project-scoped. Stored directly to consolidated.

**Buffered — complex behavioral observation**
```
/memesis:learn I noticed that across this session I've been reaching for the most powerful tool available rather than the simplest sufficient one — happened with the ORM choice, the search strategy, and the caching layer
```
Path B (or offer choice). Type: `self_observation`. This gains context from the full session pattern. Queue to ephemeral buffer so consolidation can compare it to existing self-model entries.
