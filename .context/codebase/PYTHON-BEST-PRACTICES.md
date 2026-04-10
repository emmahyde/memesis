# Python Best Practices

## Adherence Summary

| Practice | Status | Evidence |
| --- | --- | --- |
| Module-level docstrings | Established | All 26 `core/*.py` files have triple-quoted module docstrings |
| Method-level docstrings (Args/Returns) | Established | All public methods in `core/` have structured docstring sections |
| Return type annotations | Established | 130 annotated return types across `core/` (71% of functions); 53 functions lack annotations |
| `Optional[X]` vs `X \| None` syntax | Inconsistent | 8 files use `Optional[X]`; newer files use `X \| None` union syntax |
| `from __future__ import annotations` | Inconsistent | Only 4 files use it (`core/retrieval.py`, 3 eval files) |
| `pathlib.Path` for file I/O | Established | All `core/` and `hooks/` code uses `Path`; `str()` cast only at persistence boundaries |
| `logging.getLogger(__name__)` | Established | 16 of 16 non-trivial `core/` modules define `logger` at module level |
| Deferred database initialization | Established | `SqliteDatabase(None)` + `db.init()` pattern in `core/models.py` / `core/database.py` |
| Graceful degradation for optional deps | Established | `sklearn`, `sqlite_vec`, `boto3`, `nltk`, `numpy` all guarded by try/except ImportError |
| Feature flag guards | Established | `from .flags import get_flag` inside functions for all optional behaviors |
| Relative imports within `core/` | Established | All intra-package imports use `from .module import ...` |
| `sys.path.insert` in scripts/hooks/tests | Anti-pattern | 42 files use `sys.path.insert(0, str(Path(__file__).parent.parent))` |
| Broad `except Exception` | Inconsistent | 29 instances in `core/`; appropriate in hooks, questionable in library code |
| Naive vs UTC datetime | Inconsistent | 36 `datetime.now()` calls vs 1 `datetime.now(timezone.utc)` call |
| `global` mutable singletons | Inconsistent | 5 global statements across `core/`; used for lazy init of singletons |
| Test isolation with temp DB | Established | `conftest.py` fixtures init/close DB per test with temp directories |
| `busy_timeout` on Peewee connection | Established | `busy_timeout: 5000` set in `init_db()` pragmas |
| `busy_timeout` on apsw connection | Missing | `VecStore._connect()` opens apsw with no `setbusytimeout()` |
| FTS5 query sanitization | Established | `Memory.sanitize_fts_term()` and `tokenize_fts_query()` quote all user input |
| Lockfile / dependency pinning | Missing | `requirements.txt` mirrors `pyproject.toml` floor pins; no lockfile |
| `match`/`case` (Python 3.10+) | Missing | Not used anywhere; `if/elif` chains for action routing |

---

## Established Practices

Patterns the codebase follows consistently. Implementers MUST continue these.

- **Module-level docstrings with design rationale:** Every `core/*.py` file opens with a docstring explaining purpose and architectural decisions. `core/relevance.py` documents the full scoring formula inline. `core/reconsolidation.py` explains the neuroscience metaphor. These serve as the primary design record.

- **Structured method docstrings:** All public methods carry `Args:`, `Returns:`, and `Raises:` sections. Example: `core/llm.py` `call_llm()` documents all parameters, return value, and that `anthropic.APIError` propagates uncaught.

- **Return type annotations on public methods:** 130 annotated return types across 26 `core/` modules. `core/affect.py` alone has 15. `core/retrieval.py` annotates all 7 public methods. New code must annotate all public methods.

- **Centralized LLM transport:** `core/llm.py` provides `call_llm()` and `strip_markdown_fences()` as the single call path for all Anthropic API usage. Model constants (`DEFAULT_MODEL`, `BEDROCK_MODEL`) are defined once. All callers (`core/consolidator.py`, `core/crystallizer.py`, `core/threads.py`, `core/self_reflection.py`, `core/reconsolidation.py`, `core/coherence.py`) import from `core/llm.py`.

- **Graceful degradation for optional dependencies:** All optional imports follow try/except with a capability flag or early return:
  - `core/vec.py`: `try: import sqlite_vec` sets `_SQLITE_VEC_AVAILABLE` flag; `VecStore.available` property guards all operations
  - `core/embeddings.py`: `try: import boto3` returns `None` from `_get_bedrock_client()`
  - `core/threads.py`: `try: import numpy as np` inside `_get_embeddings()` returns `None`
  - `scripts/reduce.py`, `scripts/consolidate.py`: `try: from sklearn...` returns empty results
  - `core/feedback.py`, `core/relevance.py`: NLTK `LookupError` caught with fallback to empty stopwords

- **Feature flag guards:** `core/flags.py` provides a JSON-file-backed flag system with 17 flags defaulting to `True`. All optional behaviors check `get_flag("flag_name")` before executing. The import is deferred to function bodies (`from .flags import get_flag`) to avoid circular imports. Examples: `core/reconsolidation.py` line 72, `core/spaced.py` line 31.

- **Deferred database singleton:** `db = SqliteDatabase(None)` in `core/models.py` with `db.init(path, pragmas={...})` in `core/database.py` `init_db()`. No database path is resolved at import time. This is the canonical Peewee pattern for library/tool code.

- **WAL pragmas on Peewee connection:** `init_db()` sets `journal_mode: wal`, `synchronous: normal`, `busy_timeout: 5000`. These are correct and complete for WAL mode per SQLite docs.

- **`logging.getLogger(__name__)` in all core modules:** 16 of 16 non-trivial `core/` modules define a module-level logger. No `print()` statements in `core/` library code.

- **Relative imports within `core/` package:** All intra-package imports use `from .module import ...` form (e.g., `from .models import Memory`, `from .llm import call_llm`). This is consistent across all 26 `core/` files.

- **`pathlib.Path` throughout:** All file I/O uses `Path` objects. `str()` cast applied only at persistence boundaries (e.g., `self._db_path = str(db_path)` in `core/vec.py` for apsw which requires string paths).

- **Atomic FTS sync in `Memory.save()`:** FTS index updates are wrapped in `db.atomic()` with a DELETE-then-INSERT pattern that uses DB-side values for the delete (`_fts_delete_from_db`), preventing stale in-memory data from corrupting the index. Found in `core/models.py` lines 184-197.

- **Lazy singleton initialization:** `core/embeddings.py` (`_client`), `core/database.py` (`_vec_store`), `core/flags.py` (`_cache`), `core/feedback.py` (`_STOPWORDS`, `_STEMMER`) all use lazy init with `global` + None guard. Correct for a hook-subprocess runtime where modules load on every invocation.

---

## Inconsistent Practices

Patterns that appear in some places but not others. Flag for discussion.

- **`Optional[X]` vs `X | None` (Python 3.10+):** The project requires `>=3.10` (`pyproject.toml`). Eight files still import `Optional` from `typing` and use `Optional[Path]`, `Optional[dict]`, etc.:
  - Old style: `core/database.py` lines 28-29, `core/threads.py` line 21, `core/crystallizer.py` line 277, `core/lifecycle.py` line 10, `core/ingest.py` line 78
  - New style: `core/retrieval.py` line 78 (`bytes | None`), `core/reconsolidation.py` line 54 (`dict | None`), `core/llm.py` line 63 (`str | None`), `core/flags.py` line 44 (`dict | None`)
  - New code should use `X | None`; migrate existing files opportunistically.

- **`from __future__ import annotations`:** Only `core/retrieval.py` uses this in `core/`. With PEP 563, this enables forward references and deferred evaluation of annotations. Since the codebase already requires 3.10+, the benefit is minor (forward refs for `TYPE_CHECKING` imports). Either adopt it consistently or not at all.

- **Broad `except Exception` in library code:** 29 instances in `core/`. Appropriate in:
  - `core/database.py` migration code (6 instances) — migrations must not crash on unknown schema states
  - `core/vec.py` line 51 — extension loading failure should not prevent app startup
  - Hook entry points (`hooks/pre_compact.py`) — must never crash Claude Code

  Questionable in:
  - `core/relevance.py` lines 375, 385 — silently swallows FTS errors including schema corruption or locked database, returns empty results with no logging
  - `core/crystallizer.py` lines 297, 373 — catches all errors during crystallization without logging them
  - `core/feedback.py` line 31 — `except Exception: pass` when NLTK download fails, no log or warning

- **Naive vs UTC-aware datetimes:** 36 calls to `datetime.now().isoformat()` (naive, no timezone) across `core/`. Only 1 call to `datetime.now(timezone.utc).isoformat()` in `core/retrieval.py` line 382 (`_get_thread_narratives`). The naive datetimes produce strings like `"2026-03-30T14:00:00.123456"` while the UTC-aware one produces `"2026-03-30T14:00:00.123456+00:00"`. Mixed formats break lexicographic ordering used throughout the codebase for date comparisons. All code should use one or the other consistently.

- **`global` mutable singletons:** Five `global` statements in `core/`:
  - `core/embeddings.py` `_client` — Bedrock runtime client
  - `core/database.py` `_vec_store, _db_path, _base_dir` — three globals in one statement
  - `core/feedback.py` `_STOPWORDS, _STEMMER` — NLTK tools
  - `core/flags.py` `_cache` — flag cache (two statements: load and reload)

  These are correct for the single-process hook runtime. The alternative (a module-level class with class attributes) would be more testable and avoids `global` declarations, but is not worth retrofitting for the current scale.

- **Logging vs `print` in hooks:** `hooks/pre_compact.py`, `hooks/session_start.py`, `hooks/user_prompt_inject.py` use `print(..., file=sys.stderr)` for error output. `hooks/consolidate_cron.py` uses `logging`. For subprocess hooks, `print` to stderr is workable, but mixing styles makes filtering harder.

- **`pass` in except blocks without logging:** 17 `except ...: pass` patterns in `core/`. Some are intentional graceful degradation (migration checks in `core/database.py`), others silently discard useful diagnostic information (e.g., `core/manifest.py` line 168, `core/ingest.py` line 131). Adding `logger.debug(...)` to these blocks costs nothing and aids debugging.

---

## Missing Practices

Industry best practices for Python that are absent from the codebase. Not necessarily wrong — may be intentional. Flag for awareness.

- **`match`/`case` for action routing (Python 3.10+):** The `if action == "confirmed"` / `elif action == "contradicted"` chains in `core/reconsolidation.py` and similar patterns in `core/consolidator.py` are candidates for `match`/`case`. This is purely stylistic but improves readability when exhaustiveness matters.

- **Dependency lockfile:** `requirements.txt` mirrors `pyproject.toml` floor pins (e.g., `anthropic>=0.40.0`) but there is no `requirements.lock` or `pip freeze` output. The Anthropic SDK and scikit-learn have broken APIs across minor versions in the past. A lockfile ensures reproducible installs.

- **`.python-version` file:** The project requires `>=3.10` and `scripts/install-deps.sh` installs Python 3.13 via `mise`, but there is no `.python-version` file for `pyenv`/`mise` to discover automatically. Contributors may use an incompatible Python version without realizing it.

- **`apsw` busy timeout:** `VecStore._connect()` in `core/vec.py` opens apsw connections with no `setbusytimeout()` call. The Peewee connection has `busy_timeout: 5000`. If both write concurrently (e.g., embedding storage during a Peewee `db.atomic()` block), the apsw connection will raise `apsw.BusyError` immediately. Evidence: `.context/research/ecosystem-pitfalls.md` section 2.

- **Embedding dimension assertion:** `vec_memories` is declared `float[512]` but sqlite-vec accepts any blob length silently. If `DEFAULT_DIMENSIONS` in `core/embeddings.py` (512) ever drifts from the vec0 schema, KNN returns garbage distances with no error. An assert in `VecStore.store_embedding()` would catch this. Evidence: `.context/research/ecosystem-pitfalls.md` section 3.

- **Re-embedding on content change:** `Memory.save()` in `core/models.py` updates `content_hash` when content changes but does not trigger re-embedding. After reconsolidation refinements, the vector in `vec_memories` is stale. Evidence: `.context/research/ecosystem-pitfalls.md` section 4.

- **WAL checkpoint result inspection:** `close_db()` in `core/database.py` calls `PRAGMA wal_checkpoint(TRUNCATE)` but does not inspect the return value. If `busy > 0`, the checkpoint was incomplete. Evidence: `.context/research/ecosystem-pitfalls.md` section 1.

- **`consolidation_log` migration atomicity:** `_run_migrations()` in `core/database.py` rebuilds `consolidation_log` with DROP + CREATE + INSERT loop without wrapping in `db.atomic()`. A process kill during the loop permanently destroys the log. Evidence: `.context/research/ecosystem-pitfalls.md` section 2.

---

## Anti-Patterns Found

- **`sys.path.insert` in 42 files:** Every hook, script, test, and eval file starts with `sys.path.insert(0, str(Path(__file__).parent.parent))`. This is a symptom of not installing the package in development mode (`pip install -e .`). With the package installed, `import core.models` works without path manipulation. The pattern makes the import order fragile (whichever path is first wins) and prevents discovering import errors early.
  - Where: all files in `hooks/`, `scripts/`, `tests/`, `eval/`
  - Fix: `pip install -e .` and remove all `sys.path.insert` calls; or add the project root to `PYTHONPATH` in the test runner config
  - Severity: **med** — functional but fragile and clutters every file

- **Stopword set duplication:** Three separate stopword sources:
  1. NLTK `stopwords.words('english')` in `core/feedback.py` (line 45) and `core/relevance.py`
  2. Hardcoded `_STOP_WORDS` set (50+ words) in `core/models.py` `tokenize_fts_query()` (line 137)
  3. These are not identical sets. `_STOP_WORDS` includes contractions (`"s"`) that NLTK does not.
  - Where: `core/models.py`, `core/feedback.py`, `core/relevance.py`
  - Fix: extract a shared `core/nlp.py` with a single canonical stopword set
  - Severity: **low** — functional but risks drift

- **`rrf_k=60` defined in two places:** The RRF constant appears as a default argument in `hybrid_search()` and as `_RRF_K = 60` in `_crystallized_hybrid()` within `core/retrieval.py`.
  - Where: `core/retrieval.py`
  - Fix: single module-level `_RRF_K = 60` constant used by both
  - Severity: **low** — values match today but could drift

- **Silent `pass` in NLTK download failure:** `core/feedback.py` line 31-32: `except Exception: pass` when `nltk.download('stopwords')` fails. No log message. In airgap environments, the entire text normalization step silently degrades to a no-op with no diagnostic trace.
  - Where: `core/feedback.py` line 31
  - Fix: `except Exception as e: logger.warning("NLTK stopwords unavailable: %s", e)`
  - Severity: **low** — graceful degradation is correct, but silent failure is not

- **Module-level `os.environ.pop` in test conftest:** `tests/conftest.py` line 12 runs `os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)` at import time, permanently affecting the test process. If a future test needs to verify Bedrock branching, this makes it impossible without re-setting the env var.
  - Where: `tests/conftest.py` line 12
  - Fix: use `monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)` in an autouse session-scoped fixture
  - Severity: **low**

- **`_pk_exists()` issues full `get_by_id()` on every save:** `Memory._pk_exists()` in `core/models.py` calls `Memory.get_by_id(self.id)` which hydrates the entire row just to check existence. This doubles query count during batch saves (reconsolidation, crystallization).
  - Where: `core/models.py` line 206-212
  - Fix: `Memory.select(Memory.id).where(Memory.id == self.id).exists()` or use `force_insert=True` when the caller knows it is a new record
  - Severity: **low** — works correctly, just wasteful

---

## Recommendations

Prioritized list of improvements, grounded in what the codebase actually does:

1. **Add `conn.setbusytimeout(5000)` to `VecStore._connect()`** — `core/vec.py` line 56. The dual-connection architecture (Peewee + apsw to the same WAL file) means any concurrent write from the Peewee side will cause `apsw.BusyError` with no retry. One line fix; matches the Peewee-side `busy_timeout: 5000`. Evidence: `.context/research/ecosystem-pitfalls.md` section 2 "apsw connections have no busy timeout."

2. **Standardize all datetimes to UTC-aware** — 36 `datetime.now().isoformat()` calls produce naive strings; 1 call produces UTC-aware. Mixed formats break lexicographic ordering used for date comparisons (e.g., `core/spaced.py` `is_injection_eligible`, `core/coherence.py` `_recently_checked`). Replace all with `datetime.now(timezone.utc).isoformat()`. Evidence: `.context/research/ecosystem-pitfalls.md` section 2 "Naive vs. UTC datetime mixing."

3. **Assert embedding dimensions in `VecStore`** — `core/vec.py`. Add `assert len(embedding) == DEFAULT_DIMENSIONS * 4` before INSERT and before KNN query. sqlite-vec returns garbage distances on dimension mismatch with no error. Evidence: `.context/research/ecosystem-pitfalls.md` section 3.

4. **Wrap `consolidation_log` migration in `db.atomic()`** — `core/database.py` `_run_migrations()`. The DROP + CREATE + INSERT loop for schema migration is not transactional. A process kill during the loop permanently destroys the audit log. Evidence: `.context/research/ecosystem-pitfalls.md` section 2.

5. **Remove `sys.path.insert` from all files** — 42 files repeat the same pattern. Install the package with `pip install -e .` and rely on normal Python imports. This eliminates path-order fragility and reduces boilerplate. If subprocess hooks need it, set `PYTHONPATH` in the hook runner instead.

6. **Log NLTK and other silent `except` failures** — `core/feedback.py` line 31, `core/relevance.py` lines 375/385, `core/crystallizer.py` lines 297/373. Replace `pass` with `logger.debug(...)` or `logger.warning(...)`. Graceful degradation is correct; silent degradation makes debugging impossible.

7. **Consolidate stopword sets** — Extract `core/nlp.py` with a shared stopword set and stemmer. Currently three separate sources (`core/models.py`, `core/feedback.py`, `core/relevance.py`) with non-identical word lists.

8. **Migrate `Optional[X]` to `X | None`** — 8 `core/` files still import `Optional` from `typing`. The project requires Python 3.10+. Migrate when touching those files; enforce `X | None` in new code.

9. **Add a dependency lockfile** — `pip freeze > requirements.lock` and commit it. The `anthropic>=0.40.0` floor pin with no ceiling means a `pip install` in a fresh environment may pull a breaking SDK version. This is especially risky for the `eval` extras with complex dependency trees.

10. **Use `force_insert=True` in known-new-record paths** — When creating new memories (consolidation, crystallization), callers know the record is new. Passing `force_insert=True` to `save()` skips the `_pk_exists()` SELECT, halving query count in batch operations. Relevant in `core/consolidator.py` and `core/crystallizer.py`.
