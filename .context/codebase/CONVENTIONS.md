# Code Conventions

## Style

- **Python**: No linter config present in `pyproject.toml` or a standalone `setup.cfg`/`.flake8`. `pyproject.toml` declares only `pytest>=8.0` in `dev` dependencies — no `ruff`, `flake8`, `black`, or `mypy`. Formatting is consistent but unconfigured. Python version: `requires-python = ">=3.10"` (`pyproject.toml`), cpython 3.14 is in use per `__pycache__` bytecode filenames (`.cpython-314`).

## Naming

- **Classes/Modules**: `PascalCase` — `MemoryStore`, `Consolidator`, `LifecycleManager`, `RelevanceEngine`, `RetrievalEngine`, `Crystallizer`, `FeedbackLoop`, `ManifestGenerator`, `SelfReflector`, `NativeMemoryIngestor` (`core/*.py`)
- **Methods/Functions**: `snake_case` throughout — `consolidate_session`, `track_usage`, `filter_privacy`, `compute_relevance`, `ensure_instinctive_layer` (`core/*.py`)
- **Variables**: `snake_case` — `memory_id`, `session_id`, `project_context`, `content_hash`, `reinforcement_count`
- **Constants**: `SCREAMING_SNAKE_CASE` — `ARCHIVE_THRESHOLD`, `REHYDRATE_THRESHOLD`, `RECENCY_HALF_LIFE`, `CONSOLIDATION_PROMPT`, `EMOTIONAL_STATE_PATTERNS` (`core/relevance.py`, `core/prompts.py`)
- **Private helpers**: single leading underscore — `_call_llm`, `_fts_insert`, `_fts_delete`, `_build_manifest_summary`, `_compute_usage_score`, `_has_spaced_reinforcement` (`core/consolidator.py`, `core/storage.py`, `core/feedback.py`)
- **Files**: `snake_case.py` — `pre_compact.py`, `consolidate_cron.py`, `self_reflection.py`, `user_prompt_inject.py`
- **Test files**: `test_<module>.py` — `test_storage.py`, `test_consolidator.py`, `test_relevance.py`, etc. (`tests/`)
- **Eval files**: `<topic>_test.py`, `<topic>_audit.py`, `<topic>_recall.py` — `needle_test.py`, `curation_audit.py`, `spontaneous_recall.py` (`eval/`)

## Patterns

### Error handling

- **ValueError for domain errors**: used consistently for not-found, invalid-stage, duplicate-content, and invalid-action conditions. Callers catch `ValueError` at the boundary (hooks, scripts) and log warnings. Example: `raise ValueError(f"Memory not found: {memory_id}")` (`core/storage.py:510`), `raise ValueError(f"Invalid stage: {stage}")` (`core/storage.py:298`).
- **Swallowed expected errors in teardown**: `except sqlite3.OperationalError: pass` in `close()` and `__del__` to tolerate already-cleaned-up databases (`core/storage.py:67-70`).
- **Non-fatal errors in hooks**: `pre_compact.py` wraps crystallization, thread-building, and self-reflection in individual try/except blocks with `print(..., file=sys.stderr)` so one subsystem failure never blocks the hook from completing (`hooks/pre_compact.py:114-153`).
- **LLM JSON parse retry**: `_call_llm` in `Consolidator` retries once on `json.JSONDecodeError`/`KeyError`/`TypeError`, appending explicit JSON instructions to the prompt. Both attempts failing raises `ValueError` (`core/consolidator.py:212-265`). The same fence-stripping parse pattern (`_parse_decisions`, `_call_resolution_llm`, `_parse_response`) is duplicated across `Consolidator`, `Crystallizer`, and `SelfReflector` — no shared utility function.
- **Graceful self-reflection fallback**: `SelfReflector._call_llm` returns `{"observations": [], "deprecated": []}` on JSON parse failure rather than raising, to avoid blocking periodic maintenance (`core/self_reflection.py:436-440`).

### Logging

- **`logging` module** used in `core/` modules via `logger = logging.getLogger(__name__)` — present in `consolidator.py`, `relevance.py`, `ingest.py`, `self_reflection.py`.
- **`print(..., file=sys.stderr)`** used in hooks (`pre_compact.py`, `session_start.py`) for operational summaries visible to Claude Code's hook output. This is deliberate: hooks must write nothing to stdout (Claude Code reads stdout as hook output), so all diagnostic output goes to stderr.
- **JSONL event log**: `FeedbackLoop.log_event()` appends structured JSON lines to `meta/retrieval-log.jsonl` for `memory_used` and `importance_updated` events (`core/feedback.py:245-258`).
- No logging configuration is set up — the library leaves configuration to the caller (correct for library code).

### Configuration

- **Environment variables** drive LLM routing: `CLAUDE_CODE_USE_BEDROCK` (truthy → use `AnthropicBedrock` client with `us.anthropic.claude-sonnet-4-6` model, otherwise use `anthropic.Anthropic()` with `claude-sonnet-4-6`). Checked inline in each `_call_llm` method in `consolidator.py`, `crystallizer.py`, and `self_reflection.py`.
- **`CLAUDE_SESSION_ID`** read from environment in `pre_compact.py:55` for session tracking.
- **Default paths**: `~/.claude/memory` (global store) or `~/.claude/projects/<hash>/memory` (project-scoped), computed in `MemoryStore.__init__` (`core/storage.py:46-54`).
- **Project path hashing**: `re.sub(r'[^a-zA-Z0-9-]', '-', path)` — slashes become hyphens. This must match Claude Code's convention exactly (`core/storage.py:83`).
- **No config file**: all numeric thresholds (`ARCHIVE_THRESHOLD = 0.15`, `REHYDRATE_THRESHOLD = 0.30`, `RECENCY_HALF_LIFE = 60`, `token_budget_pct = 0.08`) are module-level constants or constructor defaults, not config files.

## Common Idioms

### Dual-write atomicity (file + DB in one transaction)

Every `create()` and `update()` writes the markdown file atomically first (via `tempfile.mkstemp` + `shutil.move` to same-directory target), then inserts/updates the SQLite row and FTS entry in a single `with sqlite3.connect(...) as conn` block. The DB is treated as source of truth; file move for stage changes happens **after** `conn.commit()` (`core/storage.py:323-355`, `core/storage.py:457-459`).

### Per-operation SQLite connections

All `MemoryStore` methods open a new `sqlite3.connect(self.db_path)` context per call. WAL mode and `synchronous=NORMAL` are set once at `_init_db()` and persist across connections. No connection pool; no `busy_timeout` set (noted as a gap in `ecosystem-pitfalls.md`).

### Schema migrations via try/except ALTER TABLE

Additive column migrations use `try: conn.execute("ALTER TABLE ... ADD COLUMN ...") except sqlite3.OperationalError: pass` — safe for idempotent re-runs. Three migrations are present for `archived_at`, `subsumed_by`, and `project_context` (`core/storage.py:150-169`).

### Tags stored as JSON strings

Tags are stored as `TEXT` in SQLite (JSON-encoded list) and decoded on every read with `json.loads(result['tags']) if result['tags'] else []`. Raw JSON is also passed to FTS insert for content-based search. This means tag queries use Python-side filtering (linear scan) rather than SQL (`core/storage.py:612-618`).

### FTS sync via delete-then-insert

FTS5 updates use the shadow-table delete command (`INSERT INTO memories_fts(...) VALUES('delete', ...)`) followed by a fresh insert. There are no database triggers; sync is manual in Python. Any direct SQL write to `memories` that bypasses `MemoryStore` will leave FTS stale — the `ecosystem-stack.md` recommends adding a `rebuild_fts()` method.

### Markdown frontmatter as `name`/`description`/`type`

The stored markdown format writes only three frontmatter keys (`name`, `description`, `type`) regardless of the richer metadata available in the DB. `_format_markdown` enforces this subset (`core/storage.py:266-278`). The `_extract_frontmatter` parser handles arbitrary key/value pairs.

### Privacy filter as line-level regex scan

`Consolidator.filter_privacy()` splits on `splitlines(keepends=True)` and drops any line matching the `EMOTIONAL_STATE_PATTERNS` compiled regex list. Applied before the consolidation prompt is built, so filtered content never reaches the LLM (`core/consolidator.py:159-181`, `core/prompts.py:196-201`).

### LLM response always `temperature=0, max_tokens` capped

All LLM calls use `temperature=0` for determinism. `max_tokens` is 2048 for consolidation/self-reflection and 1024 for resolution/crystallization. No `timeout` or `max_retries` overrides — relies on SDK defaults (`core/consolidator.py:231-236`, `core/crystallizer.py:81-86`).

### Importance as a float in [0.1, 1.0]

All importance updates are bounded: `max(0.1, ...)` on decay, `min(1.0, ...)` on boost (`core/feedback.py:192-194`). Crystallized memories start at `importance=0.75`, instinctive seeds at `0.80`–`0.95` (`core/crystallizer.py:267`, `core/self_reflection.py:343`).
