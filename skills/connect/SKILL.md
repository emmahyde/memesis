---
name: connect
description: Use when the user says "connect these memories", "link memories", "create a thread", "these are related", or wants to manually group memories into a named narrative thread. Does NOT overlap with /threads (which views existing threads) — this skill only creates new threads.
---

# Connect — Link Memories into a Narrative Thread

Manually group related memories into a named NarrativeThread. Accepts memory IDs directly, or a topic description that searches for candidates. Claude composes a title and summary from the selected memories; the user can override before saving.

## Usage

```
/memesis:connect <memory-id-1> <memory-id-2> [...]     # Direct IDs
/memesis:connect <topic description>                    # Search and select
```

## Procedure

### Path A — Direct memory IDs

1. Parse each argument as a memory ID.
2. Look up each via `Memory.get_by_id(id)`. If any ID is not found, report which ones failed and stop.
3. Display the resolved titles in order so the user can confirm.
4. Compose a thread title (≤80 chars) and one-sentence summary from the memory titles and summaries. Ask the user if they want to keep or change these.
5. Create the `NarrativeThread`, then one `ThreadMember` per memory with `position` starting at 1.
6. Confirm with thread ID, title, and ordered list of linked memory titles.

### Path B — Topic description

1. Run `Memory.search_fts(topic, limit=10)` — wrap the topic in `Memory.sanitize_fts_term()` to avoid FTS5 operator injection.
2. Display a numbered list of ranked results (title, stage badge, summary excerpt, memory ID).
3. Ask the user: "Which memories should be in this thread? Enter numbers (e.g. 1 3 5) or IDs."
4. Resolve the selection to Memory objects.
5. Continue from step 4 of Path A (compose title/summary, confirm, create).

### Both paths — Creation sequence

```python
from datetime import datetime

now = datetime.now().isoformat()

thread = NarrativeThread.create(
    title=composed_title,
    summary=composed_summary,
    created_at=now,
    updated_at=now,
)

for position, memory in enumerate(selected_memories, start=1):
    ThreadMember.create(
        thread_id=thread.id,
        memory_id=memory.id,
        position=position,
    )
```

## Stage Badges

| Stage | Badge |
|-------|-------|
| instinctive | `[instinct]` |
| crystallized | `[crystal]` |
| consolidated | `[consol]` |
| ephemeral | `[ephemeral]` |

## Implementation

### Path A — Lookup by IDs

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory, NarrativeThread, ThreadMember
init_db(project_context=os.getcwd())

from datetime import datetime

memory_ids = ["<id-1>", "<id-2>", "<id-3>"]  # from user input

# --- Resolve memories ---
resolved = []
not_found = []
for mid in memory_ids:
    try:
        mem = Memory.get_by_id(mid)
        resolved.append(mem)
    except Memory.DoesNotExist:
        not_found.append(mid)

if not_found:
    print(f"Memory IDs not found: {not_found}")
    sys.exit(1)

# --- Display for confirmation ---
print("Memories to link:")
for i, mem in enumerate(resolved, 1):
    title = mem.title or "(untitled)"
    print(f"  {i}. {title}  (id: {mem.id})")

# --- Compose title and summary (Claude fills these in) ---
composed_title = "<thread title composed from memory titles>"
composed_summary = "<one-sentence summary of what connects these memories>"

# --- Create thread and members ---
now = datetime.now().isoformat()
thread = NarrativeThread.create(
    title=composed_title,
    summary=composed_summary,
    created_at=now,
    updated_at=now,
)

for position, mem in enumerate(resolved, start=1):
    ThreadMember.create(
        thread_id=thread.id,
        memory_id=mem.id,
        position=position,
    )

# --- Confirm ---
print(f"\nThread created: {thread.id}")
print(f"Title: {thread.title}")
print(f"Summary: {thread.summary}")
print("\nLinked memories:")
for i, mem in enumerate(resolved, 1):
    print(f"  {i}. {mem.title or '(untitled)'}  (id: {mem.id})")
```

### Path B — Search by topic then select

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory, NarrativeThread, ThreadMember
init_db(project_context=os.getcwd())

from datetime import datetime

topic = "<user topic description>"
limit = 10

# --- FTS search ---
sanitized = Memory.sanitize_fts_term(topic)
fts_results = Memory.search_fts(sanitized, limit=limit)

if not fts_results:
    print(f"No memories found matching: {topic}")
    sys.exit(0)

# --- Display numbered list for selection ---
stage_badges = {
    "instinctive": "[instinct]",
    "crystallized": "[crystal]",
    "consolidated": "[consol]",
    "ephemeral":    "[ephemeral]",
}
print(f"Memories matching '{topic}':\n")
for i, mem in enumerate(fts_results, 1):
    badge = stage_badges.get(mem.stage, f"[{mem.stage}]")
    title = mem.title or "(untitled)"
    summary = (mem.summary or "")[:100]
    print(f"  {i}. {badge} {title}")
    if summary:
        print(f"     {summary}")
    print(f"     id: {mem.id}")
    print()

# --- After user selects (e.g. "1 3 5" or mixed IDs/numbers) ---
# Resolve selection — this block runs after the user responds
user_selection = [1, 3, 5]  # replace with parsed user input (1-indexed)
selected_memories = [fts_results[n - 1] for n in user_selection]

# --- Compose title and summary (Claude fills these in) ---
composed_title = "<thread title composed from selected memory titles>"
composed_summary = "<one-sentence summary of what connects these memories>"

# --- Create thread and members ---
now = datetime.now().isoformat()
thread = NarrativeThread.create(
    title=composed_title,
    summary=composed_summary,
    created_at=now,
    updated_at=now,
)

for position, mem in enumerate(selected_memories, start=1):
    ThreadMember.create(
        thread_id=thread.id,
        memory_id=mem.id,
        position=position,
    )

# --- Confirm ---
print(f"\nThread created: {thread.id}")
print(f"Title: {thread.title}")
print(f"Summary: {thread.summary}")
print("\nLinked memories:")
for i, mem in enumerate(selected_memories, 1):
    print(f"  {i}. {mem.title or '(untitled)'}  (id: {mem.id})")
```

## Composing Thread Title and Summary

After resolving the selected memories, compose:

- **Title** (≤80 chars): A descriptive name capturing what ties these memories together. Prefer thematic nouns over generic phrases like "Related memories". Example: "SQLite ORM Migration — Decision Context".
- **Summary** (≤150 chars): One sentence explaining the thread's connective tissue. Example: "Decisions and corrections from migrating raw sqlite3 to Peewee across the memesis codebase."

Present both to the user before saving:

> Thread title: "SQLite ORM Migration — Decision Context"
> Summary: "Decisions and corrections from migrating raw sqlite3 to Peewee across the memesis codebase."
> OK to save? Or type new title/summary to override.

If the user says "yes" or similar, proceed to create. If they provide new text, use their version.

## Confirmation Output Format

```
Thread created.

ID:      8e1f2a3b-...
Title:   SQLite ORM Migration — Decision Context
Summary: Decisions and corrections from migrating raw sqlite3 to Peewee across the memesis codebase.

Linked memories (3):
  1. [consol] Peewee Preference Signal  (id: 4f2a...)
  2. [crystal] Memesis Architecture Context  (id: 9b3d...)
  3. [consol] Usage Tracking Fix  (id: 7c1e...)
```

## Examples

### Example 1 — Direct memory IDs

```
/memesis:connect 4f2a8c1e-... 9b3d5f2a-... 7c1e3d8b-...
```

Resolves each ID, displays their titles for confirmation, then prompts for title/summary approval before creating the thread.

```
Memories to link:
  1. Peewee Preference Signal
  2. Memesis Architecture Context
  3. Usage Tracking Fix

Thread title: "SQLite ORM Migration — Decision Context"
Summary: "Decisions and corrections from migrating raw sqlite3 to Peewee."
OK to save? (yes / type override)
```

After confirmation:

```
Thread created.

ID:      8e1f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b
Title:   SQLite ORM Migration — Decision Context
Summary: Decisions and corrections from migrating raw sqlite3 to Peewee.

Linked memories (3):
  1. [consol] Peewee Preference Signal  (id: 4f2a...)
  2. [crystal] Memesis Architecture Context  (id: 9b3d...)
  3. [consol] Usage Tracking Fix  (id: 7c1e...)
```

### Example 2 — Topic description search

```
/memesis:connect cron and python path issues
```

Searches for matching memories and presents a numbered list:

```
Memories matching 'cron and python path issues':

  1. [crystal] Cron Python Path Fix
     Hourly consolidation cron was broken; /usr/bin/python3 lacks anthropic.
     id: 8e1f2a...

  2. [consol] Usage Tracking Fix
     track_usage() was never called in production; wired into PreCompact via stdin.
     id: 7c1e3d...

  3. [consol] Consolidation Backtest Baseline
     Pre-reduce baseline. Pipeline is now scan → reduce → consolidate → seed.
     id: 2b4f9a...

Which memories should be in this thread? Enter numbers (e.g. 1 2) or IDs.
```

User replies: `1 2`

```
Thread title: "Cron and Hook Wiring Fixes"
Summary: "Two production fixes to the memesis background pipeline: Python path and usage tracking."
OK to save? (yes / type override)
```

After confirmation:

```
Thread created.

ID:      c3d4e5f6-7a8b-9c0d-1e2f-3a4b5c6d7e8f
Title:   Cron and Hook Wiring Fixes
Summary: Two production fixes to the memesis background pipeline: Python path and usage tracking.

Linked memories (2):
  1. [crystal] Cron Python Path Fix  (id: 8e1f2a...)
  2. [consol] Usage Tracking Fix  (id: 7c1e3d...)
```
