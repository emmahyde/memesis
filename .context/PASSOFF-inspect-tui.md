# Agent Passoff: Implement `memesis inspect` TUI

## Task

Implement `scripts/inspect.py` — a standalone Textual TUI that runs as:

```
python3 scripts/inspect.py <memory-id-or-prefix>
python3 scripts/inspect.py <id> --db /path/to/index.db
```

This is a terminal app, completely outside Claude Code. No skill integration, no hooks.

The full spec is at `.context/SPEC-inspect-tui.md`. Read it before writing any code.

---

## Repo context

Working directory: `/Users/emmahyde/projects/memesis`

Key files to read before starting:
- `.context/SPEC-inspect-tui.md` — complete spec (layout, color palette, all 8 tabs, keybindings, wave plan)
- `core/models.py` — `Memory`, `ConsolidationLog`, `RetrievalLog`, `MemoryEdge`, `Observation` Peewee models
- `core/database.py` — `init_db(project_context=...)` — call this with `os.getcwd()` to resolve DB path
- `core/lifecycle.py` — `LifecycleManager.promote(memory_id, rationale)` — use this for `p` action
- `scripts/dashboard.py` — existing Textual app in this repo; read for structural patterns (CSS, layout, keybindings)
- `pyproject.toml` — add `textual>=0.80.0` and `rich>=13.0` to `[project.dependencies]`

**There is no `core/storage.py` or `MemoryStore` class.** The spec mentions it — ignore that note. Write actions use:
- Promote: `LifecycleManager().promote(m.id, rationale="manual")`
- Pin toggle: `Memory.update(is_pinned=1-m.is_pinned).where(Memory.id == m.id).execute()`
- Archive/forget: `Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == m.id).execute()` + log to `ConsolidationLog`
- Edit: write to tempfile, `subprocess.run`, read back and `Memory.update(content=new_content).where(...).execute()`

All DB writes must happen inside a `db.atomic()` context from `core.models`.

---

## Output files

Create exactly two files:
1. `scripts/inspect.py`
2. `scripts/inspect.tcss`

Do not create new `core/` modules. Do not modify existing `core/` files except to note the dependency.

---

## Implementation order

Follow the wave plan in the spec exactly. Each wave builds on the previous.

**Wave 0 (do first, verify before proceeding):**
- Add deps to `pyproject.toml`
- Scaffold `InspectApp(App)` — CLI arg parsing, `init_db`, prefix-match lookup, empty `compose()`
- Run `python3 scripts/inspect.py <any-real-id>` — must launch without error

**Wave 1 (both can be written in one pass, no parallelism needed):**
- `ContextPane` widget: stage badge, id, Lifecycle ladder, Timeline (merged `ConsolidationLog` + `RetrievalLog` sorted by timestamp), Schedule k=v block
- `DetailPane` shell: `TabbedContent` with 8 `TabPane`s (placeholder content), tab-switch bindings `1`–`8`, `tab` for pane focus

**Wave 2 (fill all 8 tabs):**
- Tab 1 raw: `Markdown(m.content)` in `VerticalScroll`
- Tab 2 why: parse `## Why` section from `m.content`; fallback `m.summary`
- Tab 3 classify: Rich table of classification fields
- Tab 4 prov: source session, actor, cwd, files_modified, linked observations
- Tab 5 rels: `MemoryEdge` query, Rich table
- Tab 6 log: `ConsolidationLog` query, Rich table with colored action column
- Tab 7 history: narrative prose rendering of ConsolidationLog rows
- Tab 8 diff: `difflib.unified_diff` of `m.content` vs latest `ConsolidationLog.llm_response`; Rich `Syntax("diff")`

**Wave 3:**
- Write actions: `p` promote (confirmation modal), `P` pin toggle, `f` forget (confirmation), `e` editor
- Footer widget + `?` help modal + `:` command bar + `g` goto (re-exec subprocess)

**Wave 4:**
- CSS polish: exact colors from spec, badge backgrounds with opacity, timeline indentation
- Prefix-match: `Memory.select().where(Memory.id.startswith(id_arg))` — error on 0 or >1 matches
- `o` open: `subprocess.run(['open' if sys.platform=='darwin' else 'xdg-open', path])`

---

## Critical constraints

1. **Color palette is exact** — hex values from the spec, defined as Textual CSS custom properties in `inspect.tcss`. No approximations.

2. **Timeline rendering** — use Rich `Text` with box-drawing chars directly (`├ ◆ └ ▣ ⇡`), not Rich `Tree`. The spec's ASCII layout is the target.

3. **Lifecycle ladder** — four rows, always shown. Current stage uses `●`, completed stages `✓`, future stages empty. Color each row by stage color.

4. **Prefix match** — short IDs (8 chars like `9c4e21bb`) must resolve. Error clearly if ambiguous or not found.

5. **DB path** — `init_db(project_context=os.getcwd())` resolves to `~/.claude/projects/<hash>/memory/index.db`. The `--db` flag should call `init_db(base_dir=path)` instead.

6. **No tests required** — `scripts/` is not in pytest discovery.

---

## Verification steps

After each wave, confirm:

- Wave 0: `python3 scripts/inspect.py <id>` opens without traceback
- Wave 1: Both panes visible, timeline populated, tabs exist (even if empty)
- Wave 2: Each tab shows real data for a known memory ID
- Wave 3: `p` → confirmation prompt appears; `q` quits cleanly
- Wave 4: Colors match spec; short IDs resolve correctly

Report: for each wave, paste the exact command run and one-line result.
