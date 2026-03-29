---
name: health
description: This skill should be used when the user asks about "memory health", "what's being archived", "health check", "stale memories", "relevance decay", or wants to understand which memories are fading. Does NOT overlap with /stats (which shows counts) or /usage (which shows injection/retrieval rates).
---

# Health — Memory Relevance Health Dashboard

Inspect the relevance health of your memory store. Shows memories approaching the archival threshold (fading from active use), recently archived memories (last 30 days), and rehydration candidates that could return to active status if they become relevant again.

## Usage

```
/memesis:health
/memesis:health check
/memesis:health status
```

## Procedure

1. **Initialize the database and relevance engine** using Peewee with the current project context.

2. **Fetch archive candidates** — active memories (non-archived) whose computed relevance score has decayed below or near the 0.15 threshold. For each candidate, record:
   - Relevance score (3 decimal places, e.g., 0.143)
   - Memory title
   - Days since last activity (recency)
   - Importance score (for context)

3. **Fetch recently archived memories** — memories archived within the last 30 days. Show title, archive date, and the relevance score at archival time (from consolidation_log rationale if available).

4. **Fetch rehydration candidates** — archived memories whose relevance score, when re-computed in the current context, exceeds the 0.30 threshold. These are candidates for automatic rehydration. Show:
   - Relevance score
   - Title
   - Archive date
   - Why it became relevant again (summary or context hint)

5. **Explain the relevance formula** inline so the user understands how scores are computed. Include the formula components: importance (importance^0.4), recency (exponential decay over 60-day half-life), usage signal (ratio of usage_count to injection_count), and context boost (1.5 if memory is from the current project, 1.0 otherwise).

6. **Output with context** — if no candidates in a section, say so clearly (e.g., "No rehydration candidates found").

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory
from core.relevance import RelevanceEngine, ARCHIVE_THRESHOLD, REHYDRATE_THRESHOLD
from datetime import datetime, timedelta

init_db(project_context=os.getcwd())

# Initialize the relevance engine
engine = RelevanceEngine()
project_context = os.getcwd()

# --- Archive Candidates ---
# Memories approaching the archival threshold
archive_candidates = engine.get_archival_candidates(project_context=project_context)

print("=== ARCHIVE CANDIDATES ===")
print(f"(Relevance < {ARCHIVE_THRESHOLD} — approaching archival)")
print()

if not archive_candidates:
    print("All active memories are healthy. No candidates for archival.")
else:
    # Sort by relevance ascending (worst first)
    for memory in archive_candidates[:10]:  # Show top 10
        days_since = engine._days_since_last_activity(memory)
        print(f"[{memory._relevance:.3f}] {memory.title or '(untitled)'}")
        print(f"  Days since activity: {days_since:.1f}")
        print(f"  Importance: {memory.importance or 0.5:.2f}")
        print()

# --- Recently Archived ---
# Memories archived in the last 30 days
now = datetime.now()
thirty_days_ago = (now - timedelta(days=30)).isoformat()

archived_recent = list(
    Memory.select()
    .where(
        Memory.archived_at.is_null(False),
        Memory.archived_at >= thirty_days_ago,
    )
    .order_by(Memory.archived_at.desc())
)

print("=== RECENTLY ARCHIVED ===")
print("(Last 30 days)")
print()

if not archived_recent:
    print("No memories archived in the last 30 days.")
else:
    for memory in archived_recent[:10]:  # Show top 10
        print(f"{memory.title or '(untitled)'}")
        print(f"  Archived: {memory.archived_at}")
        print(f"  Importance: {memory.importance or 0.5:.2f}")
        print()

# --- Rehydration Candidates ---
# Archived memories that are now relevant again
rehydration_candidates = engine.get_rehydration_candidates(project_context=project_context)

print("=== REHYDRATION CANDIDATES ===")
print(f"(Archived but relevance > {REHYDRATE_THRESHOLD} — can reactivate)")
print()

if not rehydration_candidates:
    print("No archived memories are currently relevant enough to rehydrate.")
else:
    # Sort by relevance descending (best fit first)
    for memory in rehydration_candidates[:10]:  # Show top 10
        days_archived = engine._days_since_last_activity(memory)
        print(f"[{memory._relevance:.3f}] {memory.title or '(untitled)'}")
        print(f"  Archived: {memory.archived_at}")
        print(f"  Importance: {memory.importance or 0.5:.2f}")
        print()

# --- Explain the formula ---
print("=== RELEVANCE FORMULA ===")
print()
print("Each memory gets a relevance score in [0, 1] using:")
print()
print("  relevance = importance^0.4 * recency^0.3 * usage_signal^0.2 * context_boost^0.1")
print()
print("Where:")
print(f"  • importance: raw importance score ({0.5} default)")
print(f"  • recency: exponential decay (half-life = 60 days) — memories fade gradually")
print(f"  • usage_signal: blend of injection vs actual usage (range 0.3–1.0)")
print(f"  • context_boost: 1.5 if from current project, 1.0 otherwise")
print()
print(f"Archival happens when relevance drops below {ARCHIVE_THRESHOLD}.")
print(f"Rehydration happens when archived memory relevance rises above {REHYDRATE_THRESHOLD}.")
print()
```

## Examples

**Check overall memory health**
```
/memesis:health
```
Produces a full report: which memories are fading (near archival), which were recently archived, and which archived memories could come back into relevance.

**Understanding a specific candidate**
If a memory appears in the archive candidates section, you might ask: "Why is this memory fading?" The relevance score breakdown (importance, days since use, usage ratio, project match) explains the answer.
