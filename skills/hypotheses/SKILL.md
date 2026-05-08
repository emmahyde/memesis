---
name: hypotheses
description: This skill triggers when the user says "show hypotheses", "pending hypotheses", "promote hypothesis", "reject hypothesis", or asks to review inferred behavioral patterns. Use for inspecting and acting on LLM-inferred hypothesis memories before they are promoted into the durable memory lifecycle.
---

# Hypotheses — Inspect and Act on Pending Hypotheses

List, promote, reject, or edit hypothesis memories. Hypotheses are LLM-inferred behavioral patterns accumulated by the self-reflection engine. They are held in the `ephemeral` stage under `kind='hypothesis'` until they pass the promotion gate (3 evidence counts, 2+ distinct sessions, no contradictions).

## Usage

```
/memesis:hypotheses
/memesis:hypotheses list
/memesis:hypotheses promote <memory-id>
/memesis:hypotheses reject <memory-id>
/memesis:hypotheses edit <memory-id>
```

## Procedure

### list (default)

1. Query all Memory rows where `kind = 'hypothesis'` and `archived_at IS NULL`.
2. For each hypothesis, display:
   - **Title** — the tendency name
   - **Evidence count** — `evidence_count` field
   - **Sessions** — distinct count from `json.loads(evidence_session_ids)`
   - **Stage** — current lifecycle stage
   - **Gate status** — whether `can_promote_hypothesis()` returns True
   - **Memory ID** — truncated to 8 chars for readability
3. Sort by evidence_count descending.
4. Show a summary line: `N pending hypotheses (M ready to promote)`.

### promote <memory-id>

1. Resolve the memory ID (full or 8-char prefix match).
2. Call `can_promote_hypothesis(memory)` from `core.self_reflection`.
   - If gate returns False: explain which criterion is unmet (evidence_count < 3, sessions < 2, or contradiction exists) and abort.
3. If gate returns True: call `promote_hypothesis(memory, rationale="User-initiated promotion via /memesis:hypotheses")`.
4. Confirm: `Promoted '<title>' from <from_stage> to <to_stage>. kind cleared.`

### reject <memory-id>

1. Resolve the memory ID.
2. Set `memory.archived_at = datetime.now().isoformat()`.
3. Call `memory.save()`.
4. Log to ConsolidationLog: action='archived', rationale='User rejected hypothesis via /memesis:hypotheses'.
5. Confirm: `Rejected '<title>'. Memory archived and removed from promotion queue.`

### edit <memory-id>

1. Resolve the memory ID.
2. Display the current content as editable JSON (the `content` field stores a JSON observation dict).
3. Apply the user's changes and call `memory.save()`.
4. Confirm the updated fields.

## Gate rules (informational)

Hypotheses are promoted when ALL of the following hold:

| Rule | Threshold |
|------|-----------|
| Evidence count | `evidence_count >= 3` |
| Distinct sessions | `len(set(json.loads(evidence_session_ids))) >= 2` |
| No contradictions | No `contradicts` edge in `memory_edges` involving this memory (checked bidirectionally) |

Explicit user-statement memories (`kind != 'hypothesis'`) are exempt from the gate and may be promoted on demand without these thresholds.

## Implementation

```python
from core.self_reflection import can_promote_hypothesis, promote_hypothesis
from core.models import Memory, ConsolidationLog, MemoryEdge
import json
from datetime import datetime

# List
hypotheses = list(
    Memory.select()
    .where(Memory.kind == "hypothesis")
    .where(Memory.archived_at.is_null())
    .order_by(Memory.evidence_count.desc())
)

for h in hypotheses:
    sessions = len(set(json.loads(h.evidence_session_ids or "[]")))
    ready = can_promote_hypothesis(h)
    print(f"{h.id[:8]}  {h.title}  evidence={h.evidence_count}  sessions={sessions}  ready={ready}")

# Promote
memory = Memory.get_by_id(memory_id)
if can_promote_hypothesis(memory):
    new_stage = promote_hypothesis(memory, rationale="User-initiated promotion")
    print(f"Promoted to {new_stage}")
else:
    print("Gate not satisfied — check evidence_count, sessions, contradictions")
```
