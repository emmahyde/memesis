# Python Best Practices

## Adherence Summary

| Practice | Status | Evidence |
| --- | --- | --- |
| Type hints on public methods | Established | 95 annotated return types across 11 `core/` files |
| `Optional` vs `X \| None` syntax | Inconsistent | Old-style `Optional[X]` from `typing` vs modern `X \| None` mixed in same module |
| Module-level docstrings | Established | All `core/` modules have triple-quoted module docstrings |
| Method-level docstrings | Established | All public methods have Args/Returns/Raises sections |
| `pathlib.Path` throughout | Established | `Path` used consistently in `core/` and `hooks/` |
| Explicit `encoding="utf-8"` on writes | Established | All `os.write()` and atomic write paths encode explicitly |
| Explicit `encoding="utf-8"` on reads | Inconsistent | `file_path.read_text()` without encoding in 4 places in `core/storage.py` |
| Atomic file writes | Established | `mkstemp + shutil.move` pattern in `storage.py`, `manifest.py` |
| `busy_timeout` on SQLite connections | Missing | `_init_db()` sets WAL + `synchronous=NORMAL` but omits `busy_timeout` |
| FTS5 input sanitization | Missing | `search_fts()` passes raw query string directly to MATCH |
| pytest `tmp_path` fixture | Inconsistent | `conftest.py` uses `tempfile.mkdtemp()` manually; test files use `monkeypatch.setenv` correctly |
| Parametrized tests | Inconsistent | `test_skills.py` uses `@pytest.mark.parametrize` widely; other test files do not |
| Broad `except Exception` | Inconsistent | Acceptable in hook entry points; silently swallowed in `relevance.py` |
| Virtual env / dependency pinning | Missing | `requirements.txt` has only `pytest>=7.0.0`; no lockfile |
| `pyproject.toml` completeness | Inconsistent | Missing `license`, no upper bound on `requires-python` |
| Logging vs print | Inconsistent | `core/` uses `logging` correctly; hooks use `print(..., file=sys.stderr)` |
| `import` inside functions | Anti-pattern | `crystallizer.py` defers `import anthropic` and `import re` inside methods |
| SQLite WAL version safety | Missing | SQLite 3.50.4 in use; ecosystem doc identifies 3.7.0–3.51.2 as a vulnerable range |

---

## Established Practices

**Module-level docstrings:** Every file in `core/` opens with a triple-quoted docstring explaining purpose and design rationale. `core/relevance.py` even documents the full scoring formula inline. Implementers must continue this — the docstrings serve as the primary design record.

**Method docstrings with structured sections:** All public methods carry `Args:`, `Returns:`, and `Raises:` sections. Example: `core/storage.py` `create()`, lines 280–292. Continue this pattern for any new public API.

**Return type annotations:** 95 annotated return types found across 11 `core/` modules. `core/storage.py` annotates every public method (`-> str`, `-> None`, `-> dict`, `-> list[dict]`). This is the expected baseline for new code.

**`pathlib.Path` usage:** All file I/O uses `Path` objects. No raw string path concatenation appears in `core/`. The `str()` cast to `Path` is applied only at persistence boundaries (e.g., `str(file_path)` when storing in SQLite).

**Atomic file writes via `mkstemp + shutil.move`:** Both `core/storage.py` `create()` / `update()` and `core/manifest.py` `write_manifest()` use the canonical POSIX-atomic write pattern: `tempfile.mkstemp(dir=target.parent, suffix='.tmp')`, write, close fd, then `shutil.move`. The `dir=target.parent` constraint is correctly applied in all three call sites, ensuring same-filesystem rename atomicity.

**Explicit `encoding='utf-8'` on writes:** Every `os.write(tmp_fd, content.encode('utf-8'))` and every `Path.write_text(..., encoding='utf-8')` in `hooks/` specifies encoding. `core/consolidator.py` line 76 also uses `encoding="utf-8"` when reading ephemeral content.

**Relative imports within `core/`:** All intra-package imports in `core/` use relative form (e.g., `from .storage import MemoryStore`, `from .lifecycle import LifecycleManager`). `hooks/` and `scripts/` use absolute imports after inserting the project root via `sys.path.insert(0, ...)`. This is consistent and correct for the package layout.

**`logging` in library code:** `core/consolidator.py`, `core/relevance.py`, `core/lifecycle.py` (indirectly), and `core/self_reflection.py` all use `logger = logging.getLogger(__name__)` and call `logger.info/warning/debug`. No `print()` in `core/`.

**Exception specificity in core library code:** `core/storage.py` raises `ValueError` for domain errors (invalid stage, memory not found, duplicate content). Callers can catch `ValueError` precisely without catching unrelated errors.

**`conn.row_factory = sqlite3.Row`:** Consistently set before queries that return results, enabling dict-style access (`row['id']`) throughout `core/storage.py`.

**`store.close()` in test teardown:** `tests/conftest.py` calls `store.close()` in the `memory_store` fixture teardown (line 33), correctly checkpointing the WAL. This prevents `-wal` and `-shm` file accumulation across the 431 test functions.

---

## Inconsistent Practices

**`Optional[X]` vs `X | None` (Python 3.10+):** The project requires Python 3.10+ (`pyproject.toml` line 9), which means the modern union syntax `X | None` is valid everywhere. However, 7 `core/` files still import `Optional` from `typing` and use `Optional[str]`, `Optional[int]` etc. — while `core/consolidator.py` and `core/feedback.py` correctly use `str | None` (Python 3.10+ syntax). New code should use `X | None`; existing code can be migrated opportunistically.
- Old style: `core/storage.py` line 17: `from typing import Optional`; `core/relevance.py` line 26
- New style: `core/consolidator.py` line 295: `def _execute_keep(...) -> str | None:`

**Explicit `encoding=` on reads:** `os.write()` and `write_text()` paths all specify `encoding='utf-8'`, but `file_path.read_text()` without encoding appears in four production read paths in `core/storage.py`:
- Line 386: `old_file_content = file_path.read_text()` (in `update()`)
- Line 485: `file_content = file_path.read_text()` (in `delete()`)
- Line 520: `result['content'] = file_path.read_text()` (in `get()`)
- Line 588: `result['content'] = file_path.read_text()` (in `search_fts()`)

On macOS and Linux with UTF-8 locales this is harmless. On systems with non-UTF-8 locale defaults (some CI environments, Docker images) these will silently misread non-ASCII content.

**`pytest.mark.parametrize` usage:** `tests/test_skills.py` uses parametrize heavily (7 parametrized test functions covering all skill names). Other test files use repetitive separate test functions for equivalent cases. For example, `tests/test_storage.py` has `test_create_memory`, `test_create_with_invalid_stage`, `test_create_duplicate_content` as three separate functions where a parametrized structure would reduce duplication. This is inconsistent but not harmful.

**`tempfile.mkdtemp()` vs `tmp_path`:** `tests/conftest.py` uses `tempfile.mkdtemp()` + `shutil.rmtree` in the `temp_dir` fixture. Newer tests use `monkeypatch.setenv` (correctly using pytest primitives). The ecosystem research (`ecosystem-stack.md` section 4) recommends migrating `mkdtemp` to pytest's built-in `tmp_path` to get auto-cleanup on test process kill and to remove the manual `shutil.rmtree` boilerplate.

**Broad `except Exception` scope:** Used appropriately in `hooks/pre_compact.py` (lines 118, 125, 152, 180) where the hook must never crash Claude Code's session — the pattern is intentional and commented. Used less appropriately in `core/relevance.py` line 289 (`except Exception: return []`) which silently swallows all FTS errors including schema corruption or a locked database. The distinction: hook entry points may swallow to protect the calling process; library code should let the caller decide.

**Logging in hooks:** `hooks/pre_compact.py`, `hooks/session_start.py`, `hooks/user_prompt_inject.py` all use `print(..., file=sys.stderr)` for error output and summary lines. `hooks/consolidate_cron.py` correctly uses `logging`. For hooks that run as subprocesses the `print` approach is workable, but mixing the two styles makes filtering log output harder.

---

## Missing Practices

**`busy_timeout` on SQLite connections:** `core/storage.py` `_init_db()` sets `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` but never sets `PRAGMA busy_timeout`. With no timeout, any concurrent write (e.g., `consolidate_cron.py` and `pre_compact.py` overlapping) will immediately raise `OperationalError: database is locked` rather than waiting. The ecosystem research (`ecosystem-pitfalls.md` section 1) flags this as a MEDIUM risk. Fix: add `conn.execute("PRAGMA busy_timeout=5000")` to `_init_db()`.

**FTS5 query sanitization:** `core/storage.py` `search_fts()` (line 556) and all callers including `core/retrieval.py` `active_search()` pass the raw query string directly to `WHERE memories_fts MATCH ?`. While this is not SQL injection (bound parameter), FTS5 operators in the query string (`OR`, `AND`, `*`, `^`, `:`, `"..."`) will be interpreted by the FTS5 engine. User-controlled queries (e.g., via `user_prompt_inject.py`) can accidentally trigger `OperationalError` with a bare `*` or silently broaden results. The ecosystem research (`ecosystem-pitfalls.md` section 2) provides a mitigation: wrap user input in `"doubled-quote-escaped"` phrase queries before passing to FTS5.

**FTS rebuild utility:** The content table `content='memories'` requires manual FTS sync. Any direct SQL write to the `memories` table that bypasses the Python layer (migrations, seed scripts, `backfill-output/` import) will leave FTS stale. There is no `rebuild_fts()` method on `MemoryStore`. The ecosystem research (`ecosystem-stack.md` section 5) provides the one-liner: `conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")`.

**Lockfile for memory lifecycle:** `requirements.txt` pins only `pytest>=7.0.0`. The production dependency `anthropic>=0.40.0` has no upper bound and no lockfile (`pip freeze` output or `requirements.lock`). Anthropic SDK minor versions have changed response structures in the past. Without a lockfile, a `pip install` in a new environment may pull a breaking version. The recommended approach for this project type (scripts + hooks, not a library) is a `requirements.lock` generated from `pip freeze`.

**Virtual environment documentation:** No `.python-version` file, no `venv/` directory tracked, no Makefile/justfile target for `venv` setup. New contributors have no guided path to a reproducible environment. Minimum: add a `.python-version` file for `pyenv` (or document the exact Python version in README) and a `just setup` or `make venv` target.

**`pyproject.toml` `license` field:** PEP 621 makes `license` optional but its absence causes `unknown license` in metadata. Even for an internal project, `license = {text = "MIT"}` (or the appropriate SPDX identifier) avoids warnings. `pyproject.toml` also has no upper bound on `requires-python = ">=3.10"` — this is a deliberate common choice but means the package will be offered to Python 4.x.

**`pytest.ini_options` in `pyproject.toml`:** The project has a standalone `pytest.ini`, which is correct and functional. It is not consolidated into `pyproject.toml`'s `[tool.pytest.ini_options]` table. This is a minor quality-of-life gap — a single `pyproject.toml` is easier to maintain. Note: `requirements.txt` (`pytest>=7.0.0`) and `pyproject.toml` dev extra (`pytest>=8.0`) differ by a major version — a minor duplication risk.

---

## Anti-Patterns Found

**Deferred imports inside methods — `core/crystallizer.py`:** The module-level `_call_llm()` function (line 70) does `import anthropic` inside the function body. `_crystallize_group()` (line 257) does `import re` inside the method body. Both `anthropic` and `re` are available at module level throughout the codebase. Deferred imports obscure the module's dependencies, add per-call overhead (the `sys.modules` lookup), and prevent import-time errors from surfacing early.
- Severity: low
- Fix: move both to the top of `core/crystallizer.py`

**`search_by_tags` full-table scan — `core/storage.py` lines 604–619:** The implementation fetches every row (`SELECT * FROM memories`) and filters in Python. With a few hundred memories this is negligible; with thousands of memories accumulated over time it becomes a linear scan. Tags are stored as a JSON array in a TEXT column. A JSON-indexed approach or a normalized `memory_tags` join table would allow SQL-level filtering, but at the current scale this is a code-smell rather than a performance emergency.
- Severity: low
- Note: this anti-pattern also bypasses the `archived_at IS NULL` filter, so `search_by_tags` returns archived memories — inconsistent with `list_by_stage` and `search_fts` behavior.

**Duplicate client instantiation logic — `core/consolidator.py` and `core/crystallizer.py`:** The Bedrock/direct client selection logic is copy-pasted verbatim in `_call_llm` (consolidator, lines 225–229), `_call_resolution_llm` (consolidator, lines 524–528), and `_call_llm` module function (crystallizer, lines 73–78). Three copies of the same 5-line pattern. A single `_make_client() -> anthropic.Anthropic` helper in `core/` would eliminate the duplication and make the Bedrock toggle testable in one place.
- Severity: low

**`ValueError` used for not-found vs invalid-input — `core/storage.py`:** `get()`, `update()`, `delete()` raise `ValueError("Memory not found: ...")` for missing records, while `create()` raises `ValueError("Invalid stage: ...")` for bad input. These are semantically distinct: "not found" is a lookup miss; "invalid stage" is a contract violation. Callers that want to handle only missing records must parse the error message string. A custom `MemoryNotFoundError(ValueError)` subclass would let callers catch specifically without message inspection.
- Severity: low

**Module-level `os.environ.pop` in `tests/conftest.py` line 12:** `os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)` runs at import time, affecting the entire test process globally and permanently. If a future test needs to verify Bedrock-vs-direct branching, this makes that impossible without re-setting the env var inside the test. The correct approach is `monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)` in a `conftest.py` autouse fixture scoped to `session`, which is also reversible.
- Severity: low

---

## Recommendations

1. **Add `busy_timeout=5000` to `_init_db()`** — `core/storage.py` line 102. The cron job (`hooks/consolidate_cron.py`) and the PreCompact hook (`hooks/pre_compact.py`) can overlap. Without a timeout, whichever arrives second silently drops its write. One line fix; prevents data loss under realistic concurrent usage. Evidence: `ecosystem-pitfalls.md` section 1, "SQLITE_BUSY Handling."

2. **Sanitize FTS5 queries before `MATCH`** — `core/storage.py` `search_fts()` and `core/relevance.py` `find_rehydration_by_observation()`. The latter already partially mitigates by filtering to `isalpha()` words, but `search_fts` is called directly from user-facing paths in `hooks/user_prompt_inject.py` with no sanitization. Add a `_sanitize_fts(query: str) -> str` helper that wraps user input in double-quoted phrase syntax. Evidence: `ecosystem-pitfalls.md` section 2.

3. **Fix missing `encoding=` on four `read_text()` calls** — `core/storage.py` lines 386, 485, 520, 588. Replace `file_path.read_text()` with `file_path.read_text(encoding="utf-8")`. All corresponding write paths already specify UTF-8. This is a latent cross-platform bug that will manifest in CI environments with non-UTF-8 locale defaults.

4. **Extract a `_make_client()` factory** — consolidate the three copies of Bedrock/direct client selection in `core/consolidator.py` and `core/crystallizer.py` into a single `core/_client.py` or module-level helper. This also makes the Bedrock path unit-testable without `os.environ` mutations.

5. **Add `rebuild_fts()` to `MemoryStore`** — protects against FTS desync after migrations or direct SQL writes. One line: `conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")`. Call it in `scripts/seed.py` after bulk imports. Evidence: `ecosystem-stack.md` section 5, "Missing: FTS Rebuild Utility."

6. **Migrate `Optional[X]` to `X | None`** — the project already requires Python 3.10+. Using the old `typing.Optional` is not wrong, but 7 of 11 `core/` modules still import it. Migrate opportunistically when touching those files; enforce in new code. No behavior change — purely cosmetic modernization.

7. **Replace `tempfile.mkdtemp` + manual `shutil.rmtree` with `tmp_path` in `tests/conftest.py`** — `tmp_path` is a pytest built-in that auto-cleans, avoids `OSError` on cleanup, and removes the boilerplate `shutil.rmtree` from the `temp_dir` fixture. Evidence: `ecosystem-stack.md` section 4.

8. **Add a lockfile** — run `pip freeze > requirements.lock` in the development environment and commit it. The production dependency chain (`anthropic>=0.40.0` + its transitive deps) should be reproducible. This is especially important for the `eval` extras (`inspect-ai`, `ragas`, `deepeval`) which have complex dependency trees.
