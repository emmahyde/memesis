# SPEC: `memesis inspect <id>` — Standalone Textual TUI

**Status:** Draft  
**Target:** `scripts/inspect.py` — standalone CLI, `python3 scripts/inspect.py <id>`  
**Runtime:** Terminal, outside Claude Code. No plugin integration needed.  
**Design source:** `.context/inspect-design-ref.html` (exported JSX mock)

---

## Overview

Single-memory inspector. Two-pane layout: left = metadata/lifecycle/timeline, right = tabbed content (8 tabs). Full keyboard navigation. Read-only by default; write actions (promote, forget, etc.) behind confirmation.

Textual is the right framework. `scripts/dashboard.py` already uses it structurally. Textual is not yet in `pyproject.toml` — add it.

---

## Dependency change

```toml
# pyproject.toml — add to [project.dependencies]
"textual>=0.80.0",
"rich>=13.0",  # already installed system-wide; pin it
```

---

## Color palette (from design mock, GitHub dark)

Define as Textual CSS variables in `scripts/inspect.tcss`:

```
--bg:         #0d1117
--border:     #30363d
--dim:        #6e7681
--muted:      #8b949e
--text:       #c9d1d9
--bright:     #f0f6fc
--green:      #3fb950
--blue:       #58a6ff
--purple:     #a371f7
--orange:     #f0883e
--yellow:     #e3b341
--red:        #f85149
--str:        #a5d6ff   (syntax: string literals)
--func:       #d2a8ff   (syntax: function names)
```

Stage → color mapping:
| Stage | Color var |
|---|---|
| ephemeral | `--orange` |
| consolidated | `--blue` |
| crystallized | `--purple` |
| instinctive | `--bright` |
| (inactive/future) | `--dim` |

Action → color mapping:
| Action | Color var |
|---|---|
| keep / reinforce | `--green` |
| promote | `--purple` |
| subsume | `--yellow` |
| demoted / pruned | `--red` |
| injected | `--muted` |
| archived | `--dim` |

---

## Layout

```
┌─ Header ─────────────────────────────────────────────────────────┐
│ memesis · <cwd>  │  ❯ memesis inspect <id_short>  [tab:pane 1-8] │
├─ ContextPane (30%) ──────┬─ DetailPane (70%) ────────────────────┤
│                          │                                        │
│  [stage badge] [kind]    │  ╭─ Detail · [N/8] <tabname>  →      │
│                          │  [ 1 ][ 2 ][ 3 ][ 4 ][ 5 ][ 6 ]...   │
│  id   <full uuid>        │                                        │
│                          │  <scrollable content>                  │
│  Lifecycle               │                                        │
│    ✓ │ ephemeral  24d    │                                        │
│    ✓ │ consolidated 24d  │                                        │
│    ● │ crystallized 17d  │                                        │
│      │ instinctive  —    │                                        │
│                          │                                        │
│  Timeline                │                                        │
│    ├ ◆ ephemeral  24d    │                                        │
│    ├ ▣ keep       24d    │                                        │
│    ├ ◆ crystallized 17d  │                                        │
│    └ ⇡ injected   19h    │                                        │
│                          │                                        │
│  Schedule                │                                        │
│    next_due = -7d ago    │                                        │
│    ease     = 2.80       │                                        │
│    interval = 7.4d       │                                        │
│    last_inject = 19h ago │                                        │
│    last_used   = 18h ago │                                        │
│    reinforce   = 7       │                                        │
│    injections  = 12      │                                        │
├──────────────────────────┴────────────────────────────────────────┤
│ p promote  P unpin  e edit  o open  g goto  d diff  f forget      │
│ : cmd   ? help   q back                                           │
└───────────────────────────────────────────────────────────────────┘
```

---

## Data queries (all read from existing DB via Peewee models)

### Primary record
```python
from core.models import Memory
m = Memory.get_by_id(id)   # or prefix match on id[:8]
```

### Timeline events
Two sources, merged and sorted by timestamp:

```python
from core.models import ConsolidationLog, RetrievalLog

# Stage transitions + consolidation decisions
events_c = (ConsolidationLog
    .select()
    .where(ConsolidationLog.memory_id == m.id)
    .order_by(ConsolidationLog.timestamp))

# Injection events (retrieval_type = 'instinctive'|'crystallized'|'fts')
events_r = (RetrievalLog
    .select()
    .where(RetrievalLog.memory_id == m.id)
    .order_by(RetrievalLog.timestamp))
```

Event display type logic:
- ConsolidationLog.action in ('promoted', 'demoted') → `◆` + stage color
- ConsolidationLog.action in ('kept', 'reinforce') → `▣` + green
- ConsolidationLog.action in ('subsumed', 'merged') → `▣` + yellow
- ConsolidationLog.action == 'pruned' → `▣` + red
- RetrievalLog rows where `was_used=1` → `⇡ injected` + muted

### Related memories (rels tab)
```python
from core.models import MemoryEdge
edges = (MemoryEdge
    .select()
    .where((MemoryEdge.source_id == m.id) | (MemoryEdge.target_id == m.id)))
```

### Observations (prov tab)
```python
from core.models import Observation
obs = Observation.select().where(Observation.memory_id == m.id)
# also: parse m.linked_observation_ids (JSON list of UUIDs)
```

### Diff (diff tab)
Compare current `m.content` against most recent prior ConsolidationLog.llm_response or a shadow copy. If no prior version exists in log, show "No prior version recorded."

### History (history tab)
All ConsolidationLog rows for this memory, showing: timestamp, session_id, action, from_stage→to_stage, rationale, tokens (if present).

---

## 8 Detail tabs

| # | Name | Content | Data source |
|---|---|---|---|
| 1 | raw | Full `m.content` as markdown | `m.content` |
| 2 | why | Extracted `## Why` section, or fallback to `m.summary` | parse `m.content` |
| 3 | classify | kind, knowledge_type, subject, work_event, tags, importance, confidence, affect_valence, temporal_scope, polarity, revisable | `m.*` fields |
| 4 | prov | Source session, actor, observations list, files_modified, cwd | `m.*` + Observation query |
| 5 | rels | Edge list: type, target/source id (short), weight | MemoryEdge query |
| 6 | log | Full ConsolidationLog rows for this memory | ConsolidationLog query |
| 7 | history | Same as log but rendered as narrative timeline | ConsolidationLog query |
| 8 | diff | Unified diff: latest content vs prior llm_response in log | ConsolidationLog + difflib |

---

## Keybindings

| Key | Action |
|---|---|
| `tab` | Switch focus between ContextPane and DetailPane |
| `1`–`8` | Jump to detail tab N |
| `p` | Promote memory one stage (requires confirmation) |
| `P` | Toggle `is_pinned` |
| `e` | Open `m.content` in `$EDITOR`, save on exit |
| `o` | Open `m.cwd` or first file in `m.files_modified` |
| `g` | Go to related memory (prompt for ID, re-launch inspect) |
| `d` | Jump to tab 8 (diff) |
| `f` | Forget (archive) memory — requires confirmation modal |
| `:` | Open command input bar |
| `?` | Show keybinding overlay |
| `q` / `Escape` | Quit |

Write actions (`p`, `P`, `e`, `f`) go through `MemoryStore` — never direct SQLite.

---

## File layout

```
scripts/
  inspect.py          # entry point + InspectApp class
  inspect.tcss        # Textual CSS (colors, layout)
```

No new `core/` modules needed. All data access via existing models.

---

## Work units & parallelization

### Wave 0 — Unblocks everything (must be first, sequential)

**W0-A: Dependency + scaffold** (`pyproject.toml`, `scripts/inspect.py` skeleton, `scripts/inspect.tcss` color vars only)
- Add `textual>=0.80.0` and `rich>=13.0` to pyproject.toml
- `InspectApp(App)` with `compose()` returning empty `Horizontal`
- CLI entry: `argparse` for `<id>` arg + optional `--db` + prefix-match lookup
- DB init via `init_db(project_context=os.getcwd())`
- Verify: `python3 scripts/inspect.py <any_id>` launches without crash

---

### Wave 1 — Parallel after W0

**W1-A: ContextPane widget** (`scripts/inspect.py`)
- Static widget, no interactivity needed yet
- Renders: stage badge, id, Lifecycle ladder, Timeline tree, Schedule k=v block
- Uses Rich `Text` objects for colored segments
- Input: single `Memory` instance passed at construction
- Dependency: W0-A

**W1-B: DetailPane shell + tab switching** (`scripts/inspect.py`, `scripts/inspect.tcss`)
- `TabbedContent` with 8 named `TabPane`s (content = placeholder `Static` for now)
- Tab titles: `raw why classify prov rels log history diff`
- Keyboard: `1`–`8` bindings jump to tab by index
- `tab` key switches focus between ContextPane and DetailPane
- Dependency: W0-A

---

### Wave 2 — Parallel after W1

**W2-A: Tab 1 (raw) + Tab 2 (why)** (`scripts/inspect.py`)
- Tab 1: render `m.content` as `Markdown` widget inside `VerticalScroll`
- Tab 2: parse `m.content` for `## Why` section; fallback to `m.summary`; render as `Markdown`
- Dependency: W1-B

**W2-B: Tab 3 (classify) + Tab 4 (prov)** (`scripts/inspect.py`)
- Tab 3: Rich table of all classification fields (`kind`, `knowledge_type`, `subject`, `work_event`, `tags`, `importance`, `confidence`, `affect_valence`, `temporal_scope`, `polarity`, `revisable`, `is_pinned`, `expires_at`, `source`)
- Tab 4: source session, actor, `cwd`, `files_modified` list, linked observations (query `Observation` by `memory_id` and parse `linked_observation_ids`)
- Dependency: W1-B

**W2-C: Tab 5 (rels) + Tab 6 (log)** (`scripts/inspect.py`)
- Tab 5: Rich table of `MemoryEdge` rows; edge_type, peer id (short), weight, metadata preview
- Tab 6: Rich table of `ConsolidationLog` rows; timestamp, session, action (colored), from→to stage, rationale (truncated), token counts if present
- Dependency: W1-B

**W2-D: Tab 7 (history) + Tab 8 (diff)** (`scripts/inspect.py`)
- Tab 7: Narrative timeline rendering of ConsolidationLog (same data as tab 6, different presentation — flowing prose with dates)
- Tab 8: `difflib.unified_diff` between `m.content` and most recent `ConsolidationLog.llm_response`; render with `Syntax("diff", ...)` from Rich; fallback message if no prior version
- Dependency: W1-B

---

### Wave 3 — Parallel after W2

**W3-A: Write actions** (`scripts/inspect.py`)
- `p` promote: call `MemoryStore.advance_stage(m.id)` wrapped in confirmation modal; refresh ContextPane on success
- `P` toggle pin: direct field update via `Memory.update(is_pinned=...).where(...).execute()`; refresh
- `f` forget: confirmation modal → `Memory.update(archived_at=now).where(...).execute()`; quit app
- `e` edit: write `m.content` to `tempfile`, `subprocess.run([$EDITOR, path])`, read back and save via `MemoryStore`
- All write ops: import and use `MemoryStore` not raw SQL
- Dependency: W2-A (need working display to verify actions)

**W3-B: Footer, `?` help overlay, `:` command bar** (`scripts/inspect.py`, `scripts/inspect.tcss`)
- `Footer` widget with key hint rendering
- `?` key: show/hide `ModalScreen` with full keybinding list
- `:` key: open `Input` widget at bottom; supported commands: `goto <id>`, `promote`, `forget`
- `g` goto: prompt for ID → `subprocess.run([sys.executable, __file__, new_id])`
- Dependency: W1-B

---

### Wave 4 — Polish (sequential, after W3)

**W4: CSS polish + prefix matching**
- Finalize `inspect.tcss`: borders, spacing, badge backgrounds with opacity, timeline indentation
- Prefix-match lookup: if `len(id_arg) < 36`, query `Memory.select().where(Memory.id.startswith(id_arg))`; error on 0 or >1 matches
- `o` open: `subprocess.run(['open', path])` on macOS, `xdg-open` on Linux; falls back to printing path
- Add `python3 scripts/inspect.py` to plugin.json scripts if desired

---

## Notes

- `MemoryStore` import: `from core.storage import MemoryStore` (verify exact module name before W3-A)
- Design established in @./memesis-design.html
- `init_db` must be called with `project_context=os.getcwd()` to resolve correct DB path; add `--db` flag as override
- Textual's `Markdown` widget handles GitHub-flavored markdown well; no custom renderer needed for tabs 1/2
- Rich `Syntax` with `lexer="python"` handles syntax-highlighted code blocks in tab 1 if content contains fenced blocks — acceptable to let Markdown handle it
- Timeline: use box-drawing chars directly in Rich `Text` (not Rich `Tree`) for exact design fidelity
- No tests required for this script per project conventions (scripts/ is not in test discovery)
