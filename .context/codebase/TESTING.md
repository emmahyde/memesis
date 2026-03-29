# Testing

## Framework

- **pytest**: `pytest>=8.0` in `[project.optional-dependencies] dev` (`pyproject.toml:21`). Installed; `__pycache__` shows `cpython-314-pytest-9.0.2.pyc` bytecode, so `pytest 9.0.2` is in use at runtime.
- **No async test framework**: no `pytest-asyncio` dependency. All tests are synchronous.
- **No xdist / parallelism**: no `pytest-xdist` dependency. Tests run in a single process.
- **Runner**: `pytest` from the project root. `pytest.ini` sets `testpaths = tests eval`.

## Structure

| Test Type   | Location         | Pattern                         |
|-------------|------------------|---------------------------------|
| Unit        | `tests/`         | `test_*.py` (class-grouped)     |
| Integration | `tests/`         | `test_integration.py`           |
| Eval        | `eval/`          | `*_test.py`, `*_audit.py`, `*_recall.py` |

- **`tests/`** contains 16 test modules, one per `core/` module or hook, plus `test_integration.py`.
- **`eval/`** contains quality-oriented eval harnesses: `needle_test.py` (retrieval recall), `staleness_test.py`, `continuity_test.py`, `curation_audit.py`, `spontaneous_recall.py`.
- Eval files use custom naming patterns; `eval/conftest.py` extends pytest collection via `pytest_collect_file` to include `*_audit.py` and `*_recall.py` (`eval/conftest.py:16-21`).

## Patterns

### Mocking

- **Anthropic API**: always mocked; no test hits a real API endpoint. `tests/conftest.py` pops `CLAUDE_CODE_USE_BEDROCK` from `os.environ` at import time to prevent accidental Bedrock calls (`tests/conftest.py:12`).
- **`unittest.mock.patch`** patches `anthropic.Anthropic` at the module level: `with patch("core.consolidator.anthropic.Anthropic") as mock_cls:`. The mock chain is `mock_cls.return_value.messages.create.return_value = <mock_msg>`. Example: `tests/test_consolidator.py:176`.
- **`patch.object` for internal methods**: integration tests patch `consolidator._call_llm` directly as `with patch.object(consolidator, "_call_llm", return_value=decisions_list):` to skip the JSON-parse layer (`tests/test_integration.py:130`). This is the preferred pattern in integration tests where exact API call count is irrelevant.
- **`side_effect` for multi-call sequences**: retry tests use `mock_cls.return_value.messages.create.side_effect = [bad_response, good_response]` to simulate first-call failure and second-call success (`tests/test_consolidator.py:549`).
- **`wraps=` for spy pattern**: `patch.object(tmp_store, "update", wraps=tmp_store.update)` lets the real method run while recording call args (`tests/test_consolidator.py:376`).
- **`capture_create` side effect**: some tests capture the exact prompt text sent to the LLM by using a closure as `side_effect` (`tests/test_consolidator.py:509-515`).
- **`monkeypatch.setenv`**: used for `HOME` overrides in storage init tests (`tests/test_storage.py:22`). This is the pytest-native way; the `project_memory_store` fixture in `tests/conftest.py:36-50` uses raw `os.environ` mutation with manual restore instead (noted in `ecosystem-pitfalls.md` as a parallelism risk).

### Fixtures

- **`tests/conftest.py`** provides three shared fixtures:
  - `temp_dir` — `function`-scoped, `tempfile.mkdtemp()` + `shutil.rmtree` teardown. Note: this is the manual pattern; `tmp_path` (pytest built-in) is preferred in newer tests such as `test_consolidator.py`.
  - `memory_store` — `function`-scoped `MemoryStore(base_dir=str(temp_dir))` with `store.close()` in teardown. Calling `store.close()` is required to checkpoint the WAL and prevent EMFILE exhaustion under load.
  - `project_memory_store` — `function`-scoped, mutates `os.environ['HOME']` with try/finally restore. Uses a fixed project context of `/Users/test/my-project`.

- **`eval/conftest.py`** provides eval-specific fixtures:
  - `eval_store` — clean `MemoryStore` per eval, WAL checkpoint on teardown.
  - `seeded_store` — pre-seeded with 20 synthetic memories across all 4 stages (5 per stage), using `seed_store()` helper.
  - `eval_engine` / `seeded_engine` — `RetrievalEngine` bound to the respective stores.
  - `lifecycle` — `LifecycleManager` bound to `eval_store`.
  - `FIXED_SEED = 42` constant for reproducible data generation (not yet wired into Python `random`; present for future use).

- **Local `tmp_store` fixtures** redefined in test modules that prefer `tmp_path` over the shared `temp_dir`/`memory_store` chain: `tests/test_consolidator.py:31-33`, `tests/test_relevance.py:22-24`. This avoids the `tempfile.mkdtemp` lifecycle mismatch with pytest's cleanup.

### Test class organization

Unit tests are grouped into `class Test<Feature>:` blocks. Integration tests use `class Test<Scenario>:` with descriptive scenario names. This grouping is consistent throughout `test_storage.py` (`TestMemoryStoreCRUD`, `TestMemoryStoreSearch`, `TestMemoryStoreAtomicity`, `TestMemoryStoreFTS`, etc.) and `test_consolidator.py` (`TestConsolidateKeep`, `TestConsolidatePrune`, `TestConsolidatePromote`, `TestContradictionResolution`, etc.).

### Spacing effect: manual DB timestamp manipulation

The lifecycle integration test needs to prove that reinforcements span multiple calendar days. Since tests run in milliseconds, `test_integration.py` backdates `consolidation_log.timestamp` entries via raw SQL after the fact (`tests/test_integration.py:165-177`). This is an intentional test-only workaround; production sessions naturally span days.

### Schema migration tests

`test_storage.py::TestMemoryStoreMetadata::test_schema_migration_adds_project_context_column` manually creates an old-schema SQLite DB (without `project_context` in `retrieval_log`) then initialises a `MemoryStore` pointing at it and asserts the column is added. This validates the `ALTER TABLE ... ADD COLUMN` migration guard (`tests/test_storage.py:383-409`).

### Eval synthetic data

`eval/conftest.py` defines `SYNTHETIC_MEMORIES` — a list of 20 dicts covering all four stages (5 ephemeral, 5 consolidated, 5 crystallized, 5 instinctive) with realistic content. `seed_store()` creates them all in a fresh store. Eval fixtures use `seeded_store` for read-heavy recall/audit tests and `eval_store` for write-heavy tests that need a clean slate.

## Running Tests

- **All tests (unit + eval)**: `pytest` from project root. `pytest.ini` covers both `tests` and `eval` directories.
- **Unit tests only**: `pytest tests/`
- **Eval suite only**: `pytest eval/`
- **Single file**: `pytest tests/test_storage.py`
- **Single test**: `pytest tests/test_consolidator.py::TestConsolidateKeep::test_keep_calls_store_create`
- **Single class**: `pytest tests/test_consolidator.py::TestContradictionResolution`
- **Watch mode**: `watchexec -e py -- pytest tests/` (uses `watchexec` from `CLAUDE.md` tools)
- **Verbose output**: `pytest -v`
- **Stop on first failure**: `pytest -x`
- **No real API calls**: guaranteed by `os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)` in `tests/conftest.py:12` and the mock pattern used in every test that exercises LLM code paths.

## Coverage Gaps

The following areas have no dedicated test files:

- `hooks/append_observation.py`, `hooks/consolidate_cron.py`, `hooks/session_start.py` — tested only indirectly via `tests/test_hooks.py` (which covers `pre_compact.py` and `user_prompt_inject.py`).
- `scripts/` — `consolidate.py`, `diagnose.py`, `heartbeat.py`, `reduce.py`, `scan.py`, `seed.py` — no `tests/test_scripts.py`.
- `core/ingest.py` — has `tests/test_ingest.py` but `NativeMemoryIngestor.ingest()` depends on a real `~/.claude/memory` directory structure, so tests likely mock filesystem access.
- FTS5 query injection — `search_fts()` passes raw user-supplied query strings to FTS5 without sanitization. The `ecosystem-pitfalls.md` flags this as HIGH severity but there are no tests covering the injection boundary.
- `busy_timeout` absence — no test asserts that concurrent write operations behave correctly under contention; the missing `PRAGMA busy_timeout` (noted in `ecosystem-stack.md`) is untested.
