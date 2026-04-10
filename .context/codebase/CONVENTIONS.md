# Code Conventions

## Style

- **Python 3.10+**: `requires-python = ">=3.10"` in `pyproject.toml`. No linter, formatter, or type checker configured -- no `ruff`, `black`, `flake8`, `mypy`, or `isort` in `pyproject.toml` or standalone config files. Formatting is consistent but enforced only by convention. Python 3.10+ syntax (`X | Y` union types, `tuple[A, B]` lowercase generics) is partially adopted in newer files; older files still use `Optional[X]`.

## Naming

- **Classes/Modules**: `PascalCase` -- `Consolidator`, `Crystallizer`, `LifecycleManager`, `RelevanceEngine`, `RetrievalEngine`, `FeedbackLoop`, `ManifestGenerator`, `SelfReflector`, `NativeMemoryIngestor`, `VecStore` (`core/*.py`)
- **Methods/Functions**: `snake_case` -- `consolidate_session`, `inject_for_session`, `compute_edges`, `embed_for_memory`, `reconsolidate`, `build_threads` (`core/*.py`)
- **Variables**: `snake_case` -- `memory_id`, `session_id`, `project_context`, `content_hash`, `reinforcement_count`, `injection_ease_factor`
- **Constants**: `SCREAMING_SNAKE_CASE` -- `CONTEXT_WINDOW_CHARS`, `DEFAULT_DIMENSIONS`, `ARCHIVE_THRESHOLD`, `REHYDRATE_THRESHOLD`, `RECONSOLIDATION_PROMPT`, `_MAX_CAUSAL_EDGES` (`core/retrieval.py`, `core/embeddings.py`, `core/relevance.py`, `core/reconsolidation.py`)
- **Private helpers**: single leading underscore -- `_call_llm`, `_fts_insert`, `_fts_delete_from_db`, `_record_injection`, `_resolve_db_path`, `_get_rowid`, `_connect` (`core/models.py`, `core/retrieval.py`, `core/database.py`, `core/vec.py`)
- **Files**: `snake_case.py` -- `pre_compact.py`, `consolidate_cron.py`, `self_reflection.py`, `user_prompt_inject.py`
- **Test files**: `test_<module>.py` in `tests/` -- `test_consolidator.py`, `test_retrieval.py`, `test_models.py`
- **Eval files**: `<topic>_test.py`, `<topic>_audit.py`, `<topic>_recall.py` in `eval/` -- `needle_test.py`, `curation_audit.py`, `spontaneous_recall.py`

## Patterns

### Error handling

- **ValueError for domain errors**: raised for not-found, invalid-stage, and invalid-action conditions. Callers catch `ValueError` at the boundary (hooks, scripts) and log. Example pattern: `raise ValueError(f"Memory not found: {memory_id}")` (`core/lifecycle.py`).
- **Non-fatal errors in hooks**: `hooks/pre_compact.py` wraps each subsystem (crystallization, thread-building, self-reflection, reconsolidation) in individual try/except blocks with `print(..., file=sys.stderr)` so one failure never blocks the hook from completing (`hooks/pre_compact.py`).
- **LLM JSON parse with retry**: `Consolidator` retries once on `json.JSONDecodeError`/`KeyError`/`TypeError`, appending explicit JSON instructions to the prompt. Failure on both attempts raises `ValueError` (`core/consolidator.py`). The same fence-stripping pattern (`strip_markdown_fences`) is centralized in `core/llm.py`.
- **Graceful degradation on optional dependencies**: all optional imports (`sklearn`, `sqlite_vec`, `boto3`, `nltk`) use try/except with capability flags or early return. Example: `VecStore.__init__` sets `self._available = False` on any failure (`core/vec.py`); sklearn in `scripts/reduce.py` returns empty list on `ImportError`.
- **Self-reflection fallback**: `SelfReflector._call_llm` returns `{"observations": [], "deprecated": []}` on JSON parse failure to avoid blocking periodic maintenance (`core/self_reflection.py`).

### Logging

- **`logging` module** in `core/` via `logger = logging.getLogger(__name__)` -- present in `core/consolidator.py`, `core/relevance.py`, `core/vec.py`, `core/embeddings.py`, `core/database.py`, `core/reconsolidation.py`, `core/flags.py`.
- **`print(..., file=sys.stderr)`** in hooks -- hooks must write nothing to stdout (Claude Code reads stdout as hook output), so all diagnostic output goes to stderr (`hooks/pre_compact.py`, `hooks/session_start.py`).
- No logging configuration is set up -- the library correctly leaves configuration to the caller.

### Configuration

- **Environment variables**: `CLAUDE_CODE_USE_BEDROCK` (truthy -> `AnthropicBedrock` client with `us.anthropic.claude-sonnet-4-6`; falsy -> `anthropic.Anthropic()` with `claude-sonnet-4-6`). Routed centrally in `core/llm.py` via `_make_client()`. `CLAUDE_SESSION_ID` read in `hooks/pre_compact.py`. `AWS_REGION` and `AWS_PROFILE` for Bedrock embeddings in `core/embeddings.py`.
- **Model constants centralized**: `DEFAULT_MODEL` and `BEDROCK_MODEL` defined in `core/llm.py`. Callers either use `call_llm()` (which selects automatically) or pass model overrides.
- **Default paths**: `~/.claude/memory` (global) or `~/.claude/projects/<hash>/memory` (project-scoped), resolved in `core/database.py:_resolve_db_path()`.
- **Project path hashing**: `re.sub(r'[^a-zA-Z0-9-]', '-', path)` -- slashes become hyphens (`core/database.py:42`).
- **Feature flags**: `core/flags.py` reads from `{base_dir}/flags.json`, merging with `DEFAULTS` dict. All flags default to `True`. Usage: `from core.flags import get_flag; if get_flag("thompson_sampling"): ...`. Cached after first load; `reload()` clears cache.
- **No config file for thresholds**: all numeric thresholds (`ARCHIVE_THRESHOLD = 0.15`, `REHYDRATE_THRESHOLD = 0.30`, `token_budget_pct = 0.08`, `_MAX_CAUSAL_EDGES = 3`) are module-level constants or constructor defaults.

## Common Idioms

### Deferred singleton database

`db = SqliteDatabase(None)` in `core/models.py`, bound at runtime by `init_db()` in `core/database.py` with WAL pragmas. All Peewee models inherit from `BaseModel` which references this deferred `db`. The `VecStore` singleton is initialized alongside (`core/database.py:99`).

### FTS sync via delete-then-insert in save() override

`Memory.save()` overrides peewee's `save()` to keep the FTS5 index in sync: within `db.atomic()`, it deletes the old FTS row (using DB values, not in-memory) then inserts the new one. `Memory.delete_instance()` also removes the FTS entry. Any code that bypasses `save()` (e.g., `Memory.insert_many()`, `bulk_create()`, raw SQL) will leave FTS stale (`core/models.py:184-197`).

### Connection-per-operation for VecStore

`VecStore` opens and closes an apsw connection for every `store_embedding`, `search_vector`, and `get_embedding` call, loading the sqlite-vec extension each time. Always uses `try/finally: conn.close()` (`core/vec.py:65-76`).

### Schema migrations via try/except ALTER TABLE

Additive column migrations use `db.execute_sql(f"ALTER TABLE ... ADD COLUMN ...")` wrapped in try/except. Idempotent on re-run. Seven column migrations for `memories`, plus migrations for `retrieval_log`, `narrative_threads`, `memory_edges`, and `consolidation_log` CHECK constraint rebuild (`core/database.py:149-244`).

### Tags stored as JSON strings

Tags are `TextField` containing JSON-encoded lists. Decoded via `Memory.tag_list` property using `json.loads()`. Raw JSON also passed to FTS insert. Tag queries use Python-side filtering via `json.loads()`, not SQL (`core/models.py:164-176`).

### LLM calls: centralized transport, distributed parsing

`core/llm.py:call_llm()` handles client selection, model routing, and markdown fence stripping. Callers (`consolidator.py`, `crystallizer.py`, `reconsolidation.py`, `self_reflection.py`, `threads.py`) own their JSON parsing and error handling because expected response shapes differ per caller.

### Importance bounded to [0.1, 1.0]

All importance updates use `max(0.1, ...)` on decay and `min(1.0, ...)` on boost. Crystallized memories start at `importance=0.75`; instinctive seeds at `0.80`-`0.95` (`core/feedback.py`, `core/crystallizer.py`, `core/self_reflection.py`).

### Privacy filter as line-level regex scan

`Consolidator.filter_privacy()` drops lines matching emotional-state patterns before building prompts. Filtered content never reaches the LLM (`core/consolidator.py`).

### Embedding availability guard

`VecStore.available` property checked before any vector operation. All callers gracefully degrade to FTS-only retrieval when embeddings are unavailable. Example: `core/retrieval.py` checks `vs.available` before calling `search_vector`.

### Feature flag guards for optional behavior

New optional behaviors are gated behind `get_flag()` checks. Examples: `get_flag("thompson_sampling")`, `get_flag("reconsolidation")`, `get_flag("contradiction_tensors")`, `get_flag("affect_awareness")` (`core/retrieval.py`, `core/reconsolidation.py`).
