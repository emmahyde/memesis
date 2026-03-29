---
name: usage
description: This skill should be used when the user asks for "usage stats", "which memories are used", "injection counts", "how often are memories used", "usage patterns", or "which memories get injected". Does NOT overlap with /stats (counts by stage), /health (archival candidates), or /threads (narrative threads).
---

# Usage — Track Memory Injection and Usage Patterns

Analyze how actively your memories are being used: which are injected most frequently, which actually get used in responses, usage trends over time, and which memories might be candidates for demotion or refinement.

## Usage

```
/memesis:usage
/memesis:usage show injected-but-unused
/memesis:usage trends
```

## Procedure

1. **Initialize the database** with your project context using the Peewee pattern below.

2. **Query the four core sections**:
   - **Most injected**: Top 10 memories by `injection_count` (ordered descending). Shows how often each memory is being offered as context.
   - **Most used**: Top 10 memories by `usage_count` (ordered descending). Shows which memories actually appear in responses.
   - **Injected but never used**: Memories with high injection count (5+) but zero usage count. These are candidates for demotion, refinement, or removal.
   - **Importance trends**: Sample the last 10 updated memories and show which have rising vs falling importance scores. Use `updated_at` as a proxy for recent activity.

3. **Render the output** in a readable format with:
   - Memory title and ID
   - For injected/used: count, last injected/used timestamp, importance score
   - For injected-but-unused: injection count, days since first injection (to assess staleness)
   - For trends: old importance vs new importance and the delta

4. **Graceful fallback**: If a query returns no results, note that (e.g., "No memories injected yet" or "All injected memories have been used at least once").

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory
init_db(project_context=os.getcwd())

from datetime import datetime
from peewee import fn

# Section 1: Most injected (top 10)
print("=== MOST INJECTED MEMORIES ===\n")
most_injected = (
    Memory.select()
    .where(Memory.archived_at.is_null())  # Active only
    .order_by(Memory.injection_count.desc())
    .limit(10)
)

for mem in most_injected:
    if (mem.injection_count or 0) > 0:
        last_inj = mem.last_injected_at or "never"
        importance = f"{mem.importance:.2f}" if mem.importance else "N/A"
        print(f"[{importance}] {mem.title or 'Untitled'} ({mem.id[:8]}...)")
        print(f"  Injected: {mem.injection_count}x, Last: {last_inj}\n")

# Section 2: Most used (top 10)
print("\n=== MOST USED MEMORIES ===\n")
most_used = (
    Memory.select()
    .where(Memory.archived_at.is_null())  # Active only
    .order_by(Memory.usage_count.desc())
    .limit(10)
)

for mem in most_used:
    if (mem.usage_count or 0) > 0:
        last_used = mem.last_used_at or "never"
        importance = f"{mem.importance:.2f}" if mem.importance else "N/A"
        print(f"[{importance}] {mem.title or 'Untitled'} ({mem.id[:8]}...)")
        print(f"  Used: {mem.usage_count}x, Last: {last_used}\n")

# Section 3: Injected but never used (high injection, zero usage)
print("\n=== INJECTED BUT NEVER USED ===\n")
injected_unused = (
    Memory.select()
    .where(
        Memory.archived_at.is_null(),  # Active only
        Memory.injection_count >= 5,   # High injection threshold
        Memory.usage_count == 0,       # Never used
    )
    .order_by(Memory.injection_count.desc())
)

count = 0
for mem in injected_unused:
    if count < 10:
        importance = f"{mem.importance:.2f}" if mem.importance else "N/A"
        print(f"[{importance}] {mem.title or 'Untitled'} ({mem.id[:8]}...)")
        print(f"  Injected: {mem.injection_count}x, Never used")
        print(f"  Last injected: {mem.last_injected_at}\n")
        count += 1

if count == 0:
    print("No memories meet the threshold (5+ injections, 0 usage).\n")

# Section 4: Importance trends (last 10 updated memories)
print("\n=== IMPORTANCE TRENDS (Recently Updated) ===\n")
recent_updates = (
    Memory.select()
    .where(Memory.archived_at.is_null())
    .order_by(Memory.updated_at.desc())
    .limit(10)
)

rising = []
falling = []
for mem in recent_updates:
    # Query the old importance from a previous snapshot if available,
    # or sample the current importance as baseline. For simplicity,
    # we'll show memories with high vs low importance.
    importance = mem.importance or 0.5

    # Heuristic: memories with high injection but low usage likely have
    # falling importance; memories with balanced injection/usage likely rising.
    injections = mem.injection_count or 0
    usages = mem.usage_count or 0

    if injections > 0:
        usage_ratio = usages / max(1, injections)
    else:
        usage_ratio = 0

    if usage_ratio > 0.5:
        rising.append((mem.title or 'Untitled', importance, usage_ratio))
    elif injections >= 5 and usages == 0:
        falling.append((mem.title or 'Untitled', importance, usage_ratio))

if rising:
    print("RISING (high usage ratio):")
    for title, imp, ratio in rising[:5]:
        print(f"  {title}: {imp:.2f} importance, {ratio:.1%} usage ratio")
    print()

if falling:
    print("FALLING (high injection, no usage):")
    for title, imp, ratio in falling[:5]:
        print(f"  {title}: {imp:.2f} importance, {ratio:.1%} usage ratio")
    print()

if not rising and not falling:
    print("No clear trends yet. Continue using memories to establish patterns.\n")
```

## Examples

**Basic usage stats**
```
/memesis:usage
```
Shows all four sections: most injected, most used, injected-but-unused, and importance trends.

**Example output**
```
=== MOST INJECTED MEMORIES ===

[0.72] SQLite Quirks and Constraints (a3f12e...)
  Injected: 15x, Last: 2026-03-29T14:22:00

[0.68] Ruby Convention Preferences (b7c4a1...)
  Injected: 12x, Last: 2026-03-29T13:55:00

=== MOST USED MEMORIES ===

[0.88] Self-Model: Learning Style (c9d5e2...)
  Used: 8x, Last: 2026-03-29T14:30:00

[0.75] Ruby Convention Preferences (b7c4a1...)
  Used: 7x, Last: 2026-03-29T14:25:00

=== INJECTED BUT NEVER USED ===

[0.45] Peewee Query Optimization Patterns (d1f3a4...)
  Injected: 6x, Never used
  Last injected: 2026-03-28T10:15:00

=== IMPORTANCE TRENDS ===

RISING (high usage ratio):
  Self-Model: Learning Style: 0.88 importance, 67% usage ratio

FALLING (high injection, no usage):
  Peewee Query Optimization Patterns: 0.45 importance, 0% usage ratio
```
