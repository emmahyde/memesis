---
name: threads
description: Use when the user asks "show threads", "what threads exist", "memory threads", "narrative threads", or wants to see how memories are connected into evolving stories. Shows narrative thread visualization with member memories and evolution arcs. Does NOT overlap with /connect (which manages explicit memory links) — this skill surfaces threads that were built automatically by the consolidation pipeline.
---

# Threads — Narrative Thread Visualization and Drill-Down

Show the narrative threads that the consolidation pipeline has detected and narrated. Each thread is a cluster of memories grouped by tag overlap and temporal spread, synthesized into an evolution arc (correction chain, preference evolution, or knowledge building story).

Threads are built automatically during consolidation — you do not create them manually.

## Usage

```
/memesis:threads                   # List all threads, most recently updated first
/memesis:threads <id-or-title>     # Full detail — ordered members with stage, importance, and summary
```

## Procedure

### Default: List All Threads

1. Query `NarrativeThread.select().order_by(NarrativeThread.updated_at.desc())`
2. For each thread, count its members via `ThreadMember.select().where(ThreadMember.thread_id == thread.id).count()`
3. Render the listing (see format below)
4. If no threads exist, explain that threads are built automatically during consolidation and suggest running `/memesis:recall` to confirm memories exist

### Drill-Down: Single Thread

1. Resolve the thread: try `NarrativeThread.get_by_id(arg)` first; if that fails (DoesNotExist or arg looks like a title), search with `NarrativeThread.select().where(NarrativeThread.title.contains(arg)).first()`
2. Load ordered members via `thread.members` (returns Memory objects ordered by ThreadMember.position)
3. Render full detail (see format below)

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import NarrativeThread, ThreadMember, Memory
init_db(project_context=os.getcwd())

# --- List all threads ---
threads = NarrativeThread.select().order_by(NarrativeThread.updated_at.desc())

for thread in threads:
    count = ThreadMember.select().where(ThreadMember.thread_id == thread.id).count()
    summary = thread.summary or "(no summary)"
    print(f"[{thread.id[:8]}]  {thread.title}  ({count} memories)")
    print(f"  {summary}")
    print(f"  updated: {thread.updated_at}")
    print()

# --- Drill into one thread (by id or title) ---
arg = "<user-provided id or title>"

try:
    thread = NarrativeThread.get_by_id(arg)
except NarrativeThread.DoesNotExist:
    thread = NarrativeThread.select().where(NarrativeThread.title.contains(arg)).first()

if thread is None:
    print(f"No thread found matching: {arg}")
else:
    members = list(thread.members)  # Memory objects ordered by position
    stage_badges = {
        "instinctive": "[instinct]",
        "crystallized": "[crystal]",
        "consolidated": "[consol]",
        "ephemeral":    "[ephemeral]",
    }
    print(f"Thread: {thread.title}")
    print(f"ID: {thread.id}")
    if thread.summary:
        print(f"Summary: {thread.summary}")
    if thread.narrative:
        print(f"\nNarrative arc:\n{thread.narrative}")
    print(f"\nMembers ({len(members)}):")
    for i, mem in enumerate(members, 1):
        badge = stage_badges.get(mem.stage, f"[{mem.stage}]")
        title = mem.title or "(untitled)"
        importance = f"{mem.importance:.2f}" if mem.importance is not None else "—"
        quote = (mem.summary or "")[:120]
        if len(mem.summary or "") > 120:
            quote += "..."
        print(f"  {i}. {badge} {title}  (importance: {importance})")
        if quote:
            print(f"     \"{quote}\"")
        print(f"     id: {mem.id}")
    print()
    print(f"Last updated: {thread.updated_at}")
```

## Output Formats

### Thread Listing

```
[a3f2b1c0]  Cron Python Path Correction Chain  (3 memories)
  Three corrections to the cron Python path, converging on the correct interpreter.
  updated: 2026-03-28T14:22:11

[9e4d7f8a]  Consolidation Prompt Evolution  (5 memories)
  How the consolidation prompt became more selective over four iterations.
  updated: 2026-03-27T09:45:03
```

When no threads exist:

```
No narrative threads found. Threads are built automatically during consolidation — they appear once the pipeline has grouped related memories into evolution arcs. Run /memesis:recall to confirm memories are present, then consolidation will detect threads over time.
```

### Thread Detail

```
Thread: Cron Python Path Correction Chain
ID: a3f2b1c0-...
Summary: Three corrections to the cron Python path, converging on the correct interpreter.

Narrative arc:
The agent initially assumed /usr/bin/python3, then discovered it lacked the anthropic package, switched to /usr/local/bin/python3, and finally pinned to the venv interpreter for full dependency isolation.

Members (3):
  1. [consol] Cron Python Path Fix  (importance: 0.72)
     "Hourly consolidation cron was broken; /usr/bin/python3 lacks anthropic, fixed to /usr/local/bin/python3."
     id: 4f2a8c1e-...

  2. [consol] Cron Venv Interpreter  (importance: 0.65)
     "Switched cron to venv Python to ensure all transitive deps including anthropic are available."
     id: 7c3b9d2f-...

  3. [crystal] Cron Python Path Lesson  (importance: 0.81)
     "Always pin cron interpreters to the venv Python, not system Python — dependency availability is not guaranteed."
     id: 2a1e6b4c-...

Last updated: 2026-03-28T14:22:11
```

## Stage Badges

| Stage | Badge |
|-------|-------|
| instinctive | `[instinct]` |
| crystallized | `[crystal]` |
| consolidated | `[consol]` |
| ephemeral | `[ephemeral]` |

## How Threads Are Built

Threads are a product of the consolidation pipeline — you do not create them manually. During each consolidation run:

1. `ThreadDetector` clusters non-threaded memories by tag overlap (union-find) and temporal spread
2. Valid clusters (2+ members, spanning at least 2 calendar days) are passed to `ThreadNarrator`
3. `ThreadNarrator` calls the LLM to synthesize the cluster into a named arc (correction chain, preference evolution, or knowledge building story)
4. The result is persisted in `narrative_threads` + `thread_members` and injected at Tier 2.5 during session start — linking any injected memories that belong to the same thread

This means thread membership reflects the actual evolution of your understanding over time, not a manually curated grouping.

## Examples

```
/memesis:threads
```

Lists all narrative threads ordered by most recently updated, with member count and summary.

```
/memesis:threads Cron Python Path Correction Chain
```

```
Thread: Cron Python Path Correction Chain
ID: a3f2b1c0-...
Summary: Three corrections to the cron Python path, converging on the correct interpreter.

Narrative arc:
The agent initially assumed /usr/bin/python3, then discovered it lacked the anthropic package...

Members (3):
  1. [consol] Cron Python Path Fix  (importance: 0.72)
     "Hourly consolidation cron was broken; /usr/bin/python3 lacks anthropic..."
     id: 4f2a8c1e-...
  ...
```

```
/memesis:threads a3f2b1c0
```

Same drill-down result, resolved by ID prefix.
