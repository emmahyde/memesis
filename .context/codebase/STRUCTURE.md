# Directory Structure

## Layout

```
/Users/emma.hyde/projects/memesis/
├── core/                         # Domain logic — no CLI, no hooks
│   ├── __init__.py
│   ├── consolidator.py           # LLM-based memory curation (PreCompact)
│   ├── crystallizer.py           # Episodic-to-semantic transformation
│   ├── feedback.py               # Usage tracking, importance scoring
│   ├── ingest.py                 # Native Claude Code memory bridge
│   ├── lifecycle.py              # Stage promotion/demotion state machine
│   ├── manifest.py               # MEMORY.md index generator
│   ├── prompts.py                # All LLM prompt templates
│   ├── relevance.py              # Scoring, archival, rehydration
│   ├── retrieval.py              # Three-tier session injection
│   ├── self_reflection.py        # Periodic self-model updates
│   ├── storage.py                # Dual-write CRUD (markdown + SQLite FTS5)
│   └── threads.py                # Narrative arc detection and synthesis
│
├── hooks/                        # Claude Code hook entry points
│   ├── __init__.py
│   ├── hooks.json                # Hook registration (SessionStart, PreCompact, UserPromptSubmit)
│   ├── append_observation.py     # Called by /memesis:learn skill
│   ├── consolidate_cron.py       # Hourly headless cron worker
│   ├── pre_compact.py            # PreCompact hook (30 s timeout)
│   ├── session_start.py          # SessionStart hook (5 s timeout)
│   └── user_prompt_inject.py     # UserPromptSubmit hook (3 s timeout)
│
├── tests/                        # pytest unit + integration tests
│   ├── conftest.py               # Shared fixtures: memory_store, project_memory_store
│   ├── test_consolidator.py
│   ├── test_crystallizer.py
│   ├── test_feedback.py
│   ├── test_hooks.py
│   ├── test_ingest.py
│   ├── test_integration.py
│   ├── test_lifecycle.py
│   ├── test_manifest.py
│   ├── test_relevance.py
│   ├── test_retrieval.py
│   ├── test_self_reflection.py
│   ├── test_skills.py
│   ├── test_storage.py
│   ├── test_threads.py
│   └── test_user_prompt_inject.py
│
├── eval/                         # Offline quality evaluation harness
│   ├── conftest.py
│   ├── continuity_test.py        # Memory continuity across sessions
│   ├── curation_audit.py         # Keep-rate and correction-rate analysis
│   ├── needle_test.py            # Retrieval precision for planted facts
│   ├── spontaneous_recall.py     # Unprompted memory surfacing quality
│   └── staleness_test.py         # Archival/rehydration timing checks
│
├── scripts/                      # Developer CLI utilities
│   ├── consolidate.py            # One-shot consolidation for a buffer
│   ├── diagnose.py               # Health check / store inspection
│   ├── heartbeat.py              # Liveness check for cron
│   ├── reduce.py                 # Bulk reduction / pruning tool
│   ├── scan.py                   # Scan memory dirs, print stats
│   └── seed.py                   # Seed a store with test memories
│
├── skills/                       # Claude skill definitions (SKILL.md files)
│   ├── backfill/SKILL.md
│   ├── forget/SKILL.md
│   ├── ideate/SKILL.md
│   ├── learn/SKILL.md
│   └── memory/SKILL.md
│
├── docs/
│   └── transcript-analysis-design.md
│
├── backfill-output/              # Backfill run artifacts (.jsonl, .db)
├── backtest-output/              # Backtest run artifacts (.jsonl)
│
├── pyproject.toml                # Build config + dependency declarations
├── pytest.ini                    # Test runner config
├── requirements.txt              # Pinned dev dependencies
├── AGENTS.md                     # Agent/skill index
└── README.md
```

## Key Locations

| What | Where |
| --- | --- |
| Source code (core library) | `core/` |
| Hook entry points | `hooks/` |
| Unit + integration tests | `tests/` |
| Eval harness (quality metrics) | `eval/` |
| Developer scripts | `scripts/` |
| Claude skill definitions | `skills/` |
| Hook registration config | `hooks/hooks.json` |
| Build / dependency config | `pyproject.toml` |
| Test runner config | `pytest.ini` |
| LLM prompt templates | `core/prompts.py` |
| Runtime memory storage | `~/.claude/projects/<hash>/memory/` (project-scoped) or `~/.claude/memory/` (global) |
| SQLite database | `{memory_dir}/index.db` |
| Ephemeral observation buffer | `{memory_dir}/ephemeral/session-YYYY-MM-DD.md` |
| Manifest index | `{memory_dir}/MEMORY.md` |
| Event log | `{memory_dir}/meta/retrieval-log.jsonl` |
| Consolidation counter | `{memory_dir}/meta/consolidation-count.json` |
| Cron log | `/tmp/memory-consolidation.log` (configured in crontab) |

## Naming Conventions

- **Files:** `snake_case.py` throughout. Hook scripts are verbs/actions (`pre_compact.py`, `session_start.py`, `user_prompt_inject.py`). Core modules are nouns (`storage.py`, `consolidator.py`, `lifecycle.py`).
- **Directories:** `snake_case` for Python packages (`core/`, `hooks/`, `tests/`). Skills use `kebab-case` (`backfill/`, `forget/`).
- **Modules:** Match file names exactly. No aliasing. All `core.*` imports use relative imports within the package (e.g., `from .storage import MemoryStore`). Hook scripts use `sys.path.insert(0, ...)` to resolve `core.*` without installation.
- **Classes:** `PascalCase`. One primary class per file in `core/` (e.g., `MemoryStore`, `Consolidator`, `RelevanceEngine`). `core/threads.py` is the exception: two classes (`ThreadDetector`, `ThreadNarrator`) plus a module-level helper function `build_threads()`.
- **Test files:** `test_{module_name}.py`, mirroring the `core/` module they cover.
- **Memory files on disk:** Written into `{stage}/{path}` within the store's `base_dir`. Stage directories are the four lifecycle stages: `ephemeral/`, `consolidated/`, `crystallized/`, `instinctive/`, plus `archived/` and `meta/`.
- **Temp files during atomic writes:** `{original_parent}/*.tmp` — same directory as the target to guarantee same-filesystem rename atomicity.
- **Project directory hashing:** `re.sub(r'[^a-zA-Z0-9-]', '-', project_path)` — replaces all non-alphanumeric-non-dash characters with `-`. This mirrors Claude Code's native convention. All project dirs therefore start with `-` (from the leading `/`); `consolidate_cron.py` uses this as a validity guard.
- **Memory IDs:** UUID4 strings, stored in the SQLite `memories.id` column and returned by all `store.create()` calls.
- **Pseudo-IDs for pruned observations:** `pruned-{md5[:8]}` — deterministic from content, used in consolidation log for audit tracing without creating a real memory record.
- **Python version:** See `pyproject.toml` (`requires-python = ">=3.10"`). Running interpreter at time of codebase snapshot: CPython 3.14 (inferred from `__pycache__` bytecode filenames `cpython-314`).
