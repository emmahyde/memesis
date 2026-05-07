---
name: stats
description: Use when the user asks "memory stats", "how many memories", "memory counts", "what have you stored", or wants to see high-level memory statistics. Shows counts by stage, importance distribution, cross-project view, and cognitive module scoring breakdown. Does NOT overlap with /health (archival/relevance diagnostics) or /usage (injection/usage tracking).
---

# Stats — Memory Counts and Distribution

View high-level statistics about your stored memories: counts by lifecycle stage, importance distribution, cross-project view, and cognitive module scoring breakdown (RISK-11).

## Usage

```
/memesis:stats                    # Show all stats for current project
/memesis:stats --global           # Show global stats across all projects
```

## Procedure

1. Initialize the database and load all memories using `Memory.active()` scope (non-archived)
2. Count memories by stage using `Memory.by_stage(stage)` for each: ephemeral, consolidated, crystallized, instinctive
3. Also count archived memories separately
4. Distribute active memories into importance buckets: low (0.1–0.4), medium (0.4–0.7), high (0.7–1.0)
5. For project-scoped view (default):
   - Get current working directory as project context
   - Count memories where `project_context` matches or is null (shared)
   - Separately count project-specific vs globally shared
6. For global view (--global flag):
   - Show stats across all project contexts
   - List breakdown by project context (with name/project_id if available)
7. Compute cognitive module breakdown (RISK-11):
   - Import `compute_module_scores` and `_get_enabled_modules` from `core.retrieval`
   - Load a sample of active memories (up to 50) for module score computation
   - Call `compute_module_scores(memories, enabled_modules=_get_enabled_modules())`
   - Report mean contribution per module and experimental status
8. Render sections in order: (1) counts by stage, (2) importance distribution, (3) cross-project view, (4) cognitive module breakdown

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory
init_db(project_context=os.getcwd())

project_context = os.getcwd()
global_view = False  # Set to True if --global flag was passed

# --- Counts by stage ---
stages = ["ephemeral", "consolidated", "crystallized", "instinctive"]
stage_counts = {}
for stage in stages:
    count = Memory.by_stage(stage).count()
    stage_counts[stage] = count

# Count archived (memories with non-null archived_at, regardless of stage)
archived_count = Memory.select().where(Memory.archived_at.is_null(False)).count()

# --- Importance distribution (active memories only) ---
active = Memory.active()
low_count = active.where(Memory.importance >= 0.1, Memory.importance < 0.4).count()
medium_count = active.where(Memory.importance >= 0.4, Memory.importance < 0.7).count()
high_count = active.where(Memory.importance >= 0.7, Memory.importance <= 1.0).count()
unrated = active.where(Memory.importance.is_null()).count()

# --- Cross-project view ---
if global_view:
    # All active memories, grouped by project_context
    all_active = Memory.active()

    # Count memories per project_context
    project_stats = {}
    for mem in all_active:
        ctx = mem.project_context or "[global]"
        if ctx not in project_stats:
            project_stats[ctx] = 0
        project_stats[ctx] += 1
else:
    # Project-scoped view
    project_specific = Memory.active().where(
        Memory.project_context == project_context
    ).count()

    # Global/shared (null project_context or different project)
    global_shared = Memory.active().where(
        (Memory.project_context.is_null()) |
        (Memory.project_context != project_context)
    ).count()

# --- Output ---
print("## Memory Statistics\n")

print("### Counts by Stage\n")
total_active = sum(stage_counts.values())
for stage in stages:
    count = stage_counts[stage]
    print(f"- **{stage.capitalize()}:** {count}")
print(f"- **Archived:** {archived_count}")
print(f"- **Total Active:** {total_active}\n")

print("### Importance Distribution (Active Memories)\n")
print(f"- **High (0.7–1.0):** {high_count}")
print(f"- **Medium (0.4–0.7):** {medium_count}")
print(f"- **Low (0.1–0.4):** {low_count}")
if unrated > 0:
    print(f"- **Unrated:** {unrated}")
print()

if global_view:
    print("### Cross-Project View (Global)\n")
    for ctx, count in sorted(project_stats.items()):
        print(f"- **{ctx}:** {count}")
else:
    print("### Cross-Project View (Current Project)\n")
    print(f"- **Project-specific:** {project_specific}")
    print(f"- **Globally shared:** {global_shared}")
    print(f"\nTip: Use `--global` flag to see all projects' memory counts.\n")

# --- Cognitive Module Breakdown (RISK-11) ---
from core.retrieval import compute_module_scores, _get_enabled_modules
import importlib

enabled_modules = _get_enabled_modules()
sample_memories = list(Memory.active().limit(50))
module_scores = compute_module_scores(sample_memories, enabled_modules=enabled_modules)

all_modules = ["affect", "coherence", "habituation", "orienting", "replay", "self_reflection", "somatic"]

print("### Cognitive Module Scoring\n")
print(f"Scored {len(sample_memories)} memories (sample of up to 50).\n")
for module_name in all_modules:
    score = module_scores.get(module_name, 0.0)
    try:
        mod = importlib.import_module(f"core.{module_name}")
        is_experimental = getattr(mod, "experimental", False)
    except Exception:
        is_experimental = False
    is_active = module_name in enabled_modules
    status = "experimental (opt-in)" if is_experimental else "production"
    active_label = "" if is_active else " [excluded from scoring]"
    print(f"- **{module_name}:** mean contribution {score:.3f} — {status}{active_label}")
print()
print("Opt-in experimental modules: set `MEMESIS_EXPERIMENTAL_MODULES=<comma-list>` env var.\n")
```

## Output Format

```
## Memory Statistics

### Counts by Stage

- **Ephemeral:** 12
- **Consolidated:** 45
- **Crystallized:** 8
- **Instinctive:** 3
- **Archived:** 2
- **Total Active:** 68

### Importance Distribution (Active Memories)

- **High (0.7–1.0):** 15
- **Medium (0.4–0.7):** 32
- **Low (0.1–0.4):** 21

### Cross-Project View (Current Project)

- **Project-specific:** 52
- **Globally shared:** 16

Tip: Use `--global` flag to see all projects' memory counts.

### Cognitive Module Scoring

Scored 50 memories (sample of up to 50).

- **affect:** mean contribution 0.320 — production
- **coherence:** mean contribution 0.040 — production
- **habituation:** mean contribution 0.280 — production
- **orienting:** mean contribution 0.210 — production
- **replay:** mean contribution 0.150 — production
- **self_reflection:** mean contribution 0.000 — experimental (opt-in) [excluded from scoring]
- **somatic:** mean contribution 0.320 — production

Opt-in experimental modules: set `MEMESIS_EXPERIMENTAL_MODULES=<comma-list>` env var.
```

## Examples

**Basic stats for current project:**
```
/memesis:stats
```

Shows memory counts by stage, importance distribution, and project-specific vs shared breakdown for the current project context.

**Global stats across all projects:**
```
/memesis:stats --global
```

Shows aggregate statistics across all project contexts, with a breakdown by project (or "global" for null project_context).
