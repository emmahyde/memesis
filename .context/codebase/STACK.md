# Technology Stack

## Languages & Runtime

- **Python**: 3.10+ required (`requires-python = ">=3.10"` in `pyproject.toml`). The `scripts/install-deps.sh` installs Python 3.13 via `mise`. No `.python-version` file in the repo.
- **CPython**: Only runtime in use. No PyPy or alternate interpreter support.
- **Python 3.10+ features**: Union type syntax (`bytes | None`) partially adopted; `match`/`case` not yet used. See `.context/research/ecosystem-stack.md` "Python 3.10+ Feature Adoption" table for details.

## Frameworks

- **No web framework**: This is a library + hook runner, not a server. All execution paths are short-lived CLI scripts or Claude Code hooks invoked as subprocesses.
- **pytest**: Test framework. Configured via `pytest.ini` with `testpaths = tests eval`. Version pinned to `>=8.0` in `pyproject.toml` dev extras.

## Key Dependencies

| Dependency | Purpose | Config Location |
| ---------- | ------- | --------------- |
| `anthropic>=0.40.0` | Anthropic Messages API client — consolidation, crystallization, self-reflection, reconsolidation, narrative synthesis | `pyproject.toml` `[project.dependencies]` |
| `peewee>=3.17` | ORM for SQLite — deferred `SqliteDatabase(None)` pattern, all relational reads/writes | `pyproject.toml`; models in `core/models.py`, init in `core/database.py` |
| `apsw>=3.46` | SQLite connection with extension loading — required because macOS system `sqlite3` lacks `enable_load_extension` | `pyproject.toml`; used in `core/vec.py` |
| `sqlite-vec>=0.1.6` | Vector KNN search via `vec0` virtual table — loaded as an apsw extension | `pyproject.toml`; schema + queries in `core/vec.py` |
| `boto3>=1.34` | AWS Bedrock runtime client for Titan Text Embeddings v2 | `pyproject.toml`; lazy-imported in `core/embeddings.py` |
| `nltk>=3.8` | Stopword removal + Porter stemming for keyword extraction | `pyproject.toml`; used in `core/feedback.py`, `core/relevance.py` |
| `scikit-learn>=1.4` | TF-IDF vectorizer + cosine similarity for near-duplicate detection and thematic clustering | `pyproject.toml`; lazy-imported in `scripts/reduce.py`, `scripts/consolidate.py` |
| `inspect-ai` | Evaluation harness (optional, `eval` extra) | `pyproject.toml` `[project.optional-dependencies]` |
| `ragas` | Retrieval evaluation metrics (optional, `eval` extra) | `pyproject.toml` |
| `deepeval` | LLM output evaluation (optional, `eval` extra) | `pyproject.toml` |
| `pytest>=8.0` | Unit and eval testing (dev extra) | `pyproject.toml` `[project.optional-dependencies]` |

### Dependency Architecture

The dependency graph has three distinct layers with graceful degradation:

1. **Required**: `anthropic`, `peewee` — core LLM calls and relational storage. No fallback.
2. **Soft-required**: `apsw`, `sqlite-vec`, `boto3` — vector subsystem. Guarded by `VecStore.available` property and try/except `ImportError`. System degrades to FTS-only hybrid search without these.
3. **Optional**: `nltk`, `scikit-learn` — text processing enhancements. Each lazy-imported with `try/except ImportError` returning empty results on failure.

## Build & Dev

- **Build backend**: `setuptools>=68` via `setuptools.build_meta` (`pyproject.toml` `[build-system]`)
- **Package manager**: pip (no poetry, no hatch, no conda)
- **Runtime manager**: `mise` (required by `scripts/install-deps.sh` for consistent Python version)
- **Plugin venv**: `scripts/install-deps.sh` creates `${CLAUDE_PLUGIN_DATA}/venv/` with all deps; hooks run from this venv
- **Install dev**: `pip install -e ".[dev]"`
- **Install eval**: `pip install -e ".[eval]"`
- **Run tests**: `python3 -m pytest tests/` from project root
- **Run hooks manually**: `python3 hooks/session_start.py`, `python3 hooks/pre_compact.py`, etc.
- **Cron job**: `python3 hooks/consolidate_cron.py` — installed via `crontab -e` at `7 * * * *`
- **Diagnostics**: `python3 scripts/diagnose.py [project_context]`
- **Backfill pipeline**: `scripts/scan.py` -> `scripts/reduce.py` -> `scripts/consolidate.py` -> `scripts/seed.py`

## Configuration Files

- `pyproject.toml`: Package metadata, dependencies, build system, setuptools package discovery. Includes `core*` and `hooks*` packages.
- `pytest.ini`: Test discovery config — `testpaths = tests eval`, file patterns `test_*.py *_test.py`.
- `requirements.txt`: Runtime dependency pins (mirrors `pyproject.toml` dependencies). Used by `scripts/install-deps.sh` for the plugin venv.
- `hooks/hooks.json`: Claude Code hook registration — maps `SessionStart`, `PreCompact`, and `UserPromptSubmit` events to Python scripts with timeouts (5s, 30s, 3s).
- `.claude-plugin/plugin.json`: Plugin metadata (name: `memesis`, version: `0.2.0`).
- `{base_dir}/flags.json`: Runtime feature flags for A/B testing (read by `core/flags.py`). 17 flags defined with defaults in `core/flags.py` `DEFAULTS` dict — all default to `True`.

## Storage Layout

All persistent state lives under `~/.claude/memory/` (global) or `~/.claude/projects/{path_hash}/memory/` (project-scoped). The path hash is produced by replacing non-alphanumeric characters with `-` in `core/database.py` `_resolve_db_path()`.

```
{base_dir}/
    index.db          # SQLite: memories, memories_fts (FTS5), vec_memories (vec0),
                      #         retrieval_log, consolidation_log, narrative_threads,
                      #         thread_members, memory_edges
    flags.json        # Feature flags (optional — defaults used if absent)
    MEMORY.md         # Auto-generated index (written by ManifestGenerator)
    ephemeral/        # Session scratch buffers (session-YYYY-MM-DD.md)
    consolidated/     # LLM-curated memories
    crystallized/     # Synthesized pattern-level insights
    instinctive/      # Always-injected behavioral guidelines
    archived/         # Soft-deleted memories (not in injection pool)
    meta/
        consolidation-count.json   # PreCompact/cron counter for self-reflection cadence
```

## SQLite Schema

Defined via Peewee models in `core/models.py`, initialized in `core/database.py` `init_db()`:

- **`memories`**: Primary row store. Key fields: `id` (UUID text PK), `stage`, `title`, `summary`, `content`, `tags` (JSON string), `importance` (float 0-1), `reinforcement_count`, `injection_count`, `usage_count`, timestamps (ISO text), `project_context`, `content_hash` (MD5), `archived_at`, `subsumed_by`, SM-2 fields (`next_injection_due`, `injection_ease_factor`, `injection_interval_days`), `echo_count`.
- **`memories_fts`**: FTS5 external content table over `title`, `summary`, `tags`, `content` with `content='memories'`. Sync is manual via `_fts_insert`/`_fts_delete` in `Memory.save()` / `Memory.delete_instance()` overrides — no SQL triggers.
- **`vec_memories`**: sqlite-vec `vec0` virtual table with `memory_id TEXT PRIMARY KEY` and `embedding float[512]`. Managed by `core/vec.py` `VecStore` via separate apsw connections.
- **`memory_edges`**: Graph edges between memories. Types: `thread_neighbor`, `tag_cooccurrence` (recomputable), `caused_by`, `refined_from`, `subsumed_into`, `contradicts`, `echo` (incremental).
- **`narrative_threads`** + **`thread_members`**: Many-to-many ordered thread membership with affect metadata.
- **`retrieval_log`**: Records every injection and active-search retrieval. `was_used` updated by `FeedbackLoop`.
- **`consolidation_log`**: Audit trail for lifecycle actions (kept, pruned, promoted, demoted, merged, deprecated, subsumed, archived).
- **WAL mode**: `journal_mode=wal`, `synchronous=normal`, `busy_timeout=5000` — set in `core/database.py` `init_db()`.

## Dual-Connection Architecture

The database uses two separate SQLite connection paths to the same `index.db` file:

1. **Peewee** (`core/models.py` `db = SqliteDatabase(None)`): Handles all relational operations. Deferred init pattern — bound at runtime by `init_db()`.
2. **apsw** (`core/vec.py` `VecStore`): Handles all vector operations. Opens a new connection per operation to load the `sqlite-vec` extension. This separation exists because macOS system `sqlite3` lacks `enable_load_extension`.

Both connections share the same WAL file. See `.context/research/ecosystem-pitfalls.md` for concurrency risks (busy timeout mismatch, checkpoint behavior).
