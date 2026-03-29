# Technology Stack

## Languages & Runtime

- **Python**: 3.10+ required (`requires-python = ">=3.10"` in `pyproject.toml`); runtime Python 3.14.2 observed on this machine (from `python3 --version`). No `.python-version` file in the repo.
- **CPython**: Only runtime in use. No PyPy or alternate interpreter support.

## Frameworks

- **No web framework**: This is a library + hook runner, not a server. All execution paths are short-lived CLI scripts or Claude Code hooks invoked as subprocesses.
- **pytest**: Test framework. Configured via `pytest.ini` with `testpaths = tests eval`. Version pinned to `>=7.0.0` in `requirements.txt` and `>=8.0` in `pyproject.toml` dev extras. Both `tests/` and `eval/` are discovered — the `eval/conftest.py` extends collection with a custom `pytest_collect_file` hook to pick up `*_audit.py` and `*_recall.py`.

## Key Dependencies

| Dependency | Purpose | Config Location |
| ---------- | ------- | --------------- |
| `anthropic>=0.40.0` | Anthropic Messages API client — used for consolidation, crystallization, self-reflection, and narrative synthesis | `pyproject.toml` `[project.dependencies]` |
| `sqlite3` | Standard-library SQLite — WAL-mode DB for memory index, FTS5 search, retrieval log, consolidation log, narrative threads | `core/storage.py` |
| `inspect-ai` | Evaluation harness (optional, `eval` extra) | `pyproject.toml` `[project.optional-dependencies]` |
| `ragas` | Retrieval evaluation metrics (optional, `eval` extra) | `pyproject.toml` |
| `deepeval` | LLM output evaluation (optional, `eval` extra) | `pyproject.toml` |
| `pytest>=8.0` | Unit and eval testing (dev extra) | `pyproject.toml` `[project.optional-dependencies]` |

All non-test, non-eval runtime dependencies are stdlib + `anthropic`. There is no `httpx`, `pydantic`, `fastapi`, or any other third-party dependency in the production path.

## Build & Dev

- **Build backend**: `setuptools>=68` via `setuptools.build_meta` (`pyproject.toml` `[build-system]`)
- **Package manager**: pip (no poetry, no hatch, no conda)
- **Install dev**: `pip install -e ".[dev]"`
- **Install eval**: `pip install -e ".[eval]"`
- **Run tests**: `pytest` (resolves config from `pytest.ini`)
- **Run hooks manually**: `python3 hooks/session_start.py`, `python3 hooks/pre_compact.py`, etc.
- **Cron job**: `python3 hooks/consolidate_cron.py` — installed via `crontab -e` (see comment at top of `hooks/consolidate_cron.py`)
- **Diagnostics**: `python3 scripts/diagnose.py [project_context]`
- **Backfill pipeline**: `scripts/scan.py → scripts/reduce.py → scripts/consolidate.py → scripts/seed.py`

## Configuration Files

- `pyproject.toml`: Package metadata, dependencies, build system, setuptools package discovery. Includes `core*` and `hooks*` packages; `eval` and `scripts` are NOT in the installed package (though `eval/__init__.py` exists).
- `pytest.ini`: Test discovery config — `testpaths = tests eval`, file patterns `test_*.py *_test.py`.
- `requirements.txt`: Minimal dev requirements (`pytest>=7.0.0`); exists alongside the `pyproject.toml` dev extras (minor duplication risk).
- `hooks/hooks.json`: Claude Code hook registration — maps `SessionStart`, `PreCompact`, and `UserPromptSubmit` events to their respective Python scripts with timeouts (5s, 30s, 3s).

## Storage Layout

All persistent state lives under `~/.claude/memory/` (global) or `~/.claude/projects/{path_hash}/memory/` (project-scoped). The hash is a slug produced by replacing non-alphanumeric characters with `-`, matching Claude Code's own convention (`core/storage.py` `_hash_project_path`).

```
{base_dir}/
    index.db          # SQLite: memories, memories_fts, retrieval_log,
                      #         consolidation_log, narrative_threads, thread_members
    MEMORY.md         # Auto-generated index (written by ManifestGenerator)
    ephemeral/        # Session scratch buffers (session-YYYY-MM-DD.md)
    consolidated/     # LLM-curated memories
    crystallized/     # Synthesized pattern-level insights
    instinctive/      # Always-injected behavioral guidelines
    archived/         # Soft-deleted memories (not in injection pool)
    meta/
        retrieval-log.jsonl        # FeedbackLoop event log
        consolidation-count.json   # PreCompact counter for self-reflection cadence
```

## SQLite Schema Details

Defined in `core/storage.py` `_init_db()`:

- `memories`: Primary row store. Key fields: `id` (UUID), `file_path`, `stage`, `title`, `summary`, `tags` (JSON array), `importance` (0–1), `reinforcement_count`, `injection_count`, `usage_count`, timestamps, `project_context`, `content_hash` (MD5), `archived_at`, `subsumed_by`.
- `memories_fts`: FTS5 virtual table over `title`, `summary`, `tags`, `content` with `content='memories'` (external content table). Sync is manual via `_fts_insert`/`_fts_delete` — no triggers.
- `retrieval_log`: Records every injection and active-search retrieval. `was_used` updated by `FeedbackLoop.track_usage`.
- `consolidation_log`: Audit trail for every lifecycle action (kept, pruned, promoted, demoted, merged, deprecated, subsumed).
- `narrative_threads` + `thread_members`: Many-to-many ordered thread membership.
- WAL mode enabled at init: `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`. No `busy_timeout` is set (potential lock contention under concurrent cron + hook).
