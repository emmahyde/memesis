# Testing

## Framework

- **pytest**: `pytest>=8.0` declared in `[project.optional-dependencies] dev` (`pyproject.toml:27`). Config in `pytest.ini`.
- **No async test framework**: no `pytest-asyncio`. All tests are synchronous.
- **No parallelism**: no `pytest-xdist`. Tests run in a single process.
- **Runner**: `pytest` from the project root.

## Structure

| Test Type   | Location  | Pattern                                     | Count |
|-------------|-----------|---------------------------------------------|-------|
| Unit        | `tests/`  | `test_<module>.py` (class-grouped)          | ~910  |
| Integration | `tests/`  | `test_integration.py`                       | 5 classes |
| Eval        | `eval/`   | `*_test.py`, `*_audit.py`, `*_recall.py`    | varies |

- **`tests/`** contains ~22 test modules, one per `core/` module or hook, plus `test_integration.py` and `test_scripts.py`.
- **`eval/`** contains quality-oriented eval harnesses: `needle_test.py`, `staleness_test.py`, `continuity_test.py`, `curation_audit.py`, `spontaneous_recall.py`, `live_retrieval_test.py`, `metrics_test.py`, `longmemeval_test.py`.
- **`eval/conftest.py`** extends pytest collection via `pytest_collect_file` to include `*_audit.py` and `*_recall.py` files (`eval/conftest.py:18-23`).
- **`pytest.ini`** configuration (`pytest.ini:1-6`):
  ```ini
  [pytest]
  testpaths = tests eval
  python_files = test_*.py *_test.py
  python_classes = Test*
  python_functions = test_*
  ```

## Patterns

### Mocking

- **Anthropic API always mocked**: no test hits a real API endpoint. `tests/conftest.py:12` pops `CLAUDE_CODE_USE_BEDROCK` from `os.environ` at import time to prevent accidental Bedrock calls.
- **`unittest.mock.patch` on `call_llm`**: the standard pattern patches the transport function at the caller's module: `with patch("core.reconsolidation.call_llm", return_value=llm_response):`. The mock returns pre-built JSON strings that the caller parses as normal. Example: `tests/test_reconsolidation.py:46`.
- **`patch.object` for internal methods**: integration tests patch higher-level methods like `consolidator._call_llm` directly to skip the JSON-parse layer. Example: `tests/test_integration.py`.
- **`side_effect` for multi-call sequences**: retry tests use `side_effect = [bad_response, good_response]` to simulate first-call failure and second-call success (`tests/test_consolidator.py`).
- **Capture closures**: some tests capture the exact prompt text sent to the LLM by using a closure as `side_effect` to record call arguments (`tests/test_consolidator.py`).
- **No Bedrock embedding mocks**: tests that would exercise `embed_for_memory` skip or mock at the `get_vec_store()` level since Bedrock calls are not mocked at the boto3 layer.

### Fixtures

- **Shared fixtures in `tests/conftest.py`** (`tests/conftest.py:20-50`):
  - `temp_dir` -- `function`-scoped, `tempfile.mkdtemp()` + `shutil.rmtree` teardown.
  - `memory_store` -- calls `init_db(base_dir=str(temp_dir))` + `close_db()` on teardown. Yields the `base_dir` Path.
  - `project_memory_store` -- mutates `os.environ['HOME']` with try/finally restore; uses `init_db(project_context='/Users/test/my-project')`.

- **Local `base` fixtures** redefined in most test modules using pytest's built-in `tmp_path`:
  ```python
  @pytest.fixture
  def base(tmp_path):
      init_db(base_dir=str(tmp_path / "memory"))
      yield
      close_db()
  ```
  This pattern is used in `test_retrieval.py`, `test_consolidator.py`, `test_reconsolidation.py`, `test_models.py`, `test_graph.py`, `test_causal_edges.py`, and others. Each test module defines its own `base` fixture for DB isolation.

- **Eval fixtures in `eval/conftest.py`** (`eval/conftest.py:248-361`):
  - `eval_store` -- clean Peewee database per eval.
  - `seeded_store` -- pre-seeded with 20 synthetic memories across 4 stages (5 per stage).
  - `live_store` -- seeded from real observations DB (`eval/eval-observations.db`); skips if DB not found.
  - `eval_engine` / `seeded_engine` / `live_engine` -- `RetrievalEngine` bound to respective stores.
  - `lifecycle` -- `LifecycleManager` bound to `eval_store`.
  - `FIXED_SEED = 42` constant for reproducible data generation.

- **Helper factory functions**: most test modules define `_create_memory()` or `_make_memory()` helpers that call `Memory.create(...)` with sensible defaults. These are module-local, not shared fixtures. Examples in `test_retrieval.py:45-64`, `test_consolidator.py:62-74`, `test_integration.py:33-46`.

### Test class organization

Tests are grouped into `class Test<Feature>:` blocks named after the capability being tested. Examples:
- `test_retrieval.py`: `TestHybridSearch`, `TestCrystallizedHybrid`, `TestThompsonSampling`, `TestProvenanceSignals`, `TestActiveTensions`, `TestAffectAwareThreadOrdering`
- `test_consolidator.py`: `TestConsolidateKeep`, `TestConsolidatePrune`, `TestConsolidatePromote`, `TestContradictionResolution`, `TestMalformedJsonHandling`
- `test_causal_edges.py`: `TestSchemaMigration`, `TestReconsolidationCausalEdges`, `TestContradictionEdges`, `TestArcAffect`
- `test_models.py`: `TestDatabaseInit`, `TestMemoryCRUD`, `TestMemorySearch`, `TestFTS`, `TestContentHash`

Integration tests use `class Test<Scenario>:` with descriptive scenario names: `TestFullLifecycleEphemeralToCrystallized`, `TestLearnMemoryExplicitly`, `TestForgetMemory` (`tests/test_integration.py`).

### Database isolation pattern

Every test gets its own SQLite database via `tmp_path`. The standard teardown calls `close_db()` which issues `PRAGMA wal_checkpoint(TRUNCATE)` before closing. This prevents WAL file handle leaks across tests.

### Timestamp manipulation for time-dependent tests

Tests that need to prove multi-day behavior (e.g., spacing effect, spaced repetition) backdate timestamps via raw SQL or direct field assignment since tests complete in milliseconds. Example: `test_integration.py` backdates `consolidation_log.timestamp` entries.

## Running Tests

- **All tests (unit + eval)**: `pytest`
- **Unit tests only**: `pytest tests/`
- **Eval suite only**: `pytest eval/`
- **Single file**: `pytest tests/test_retrieval.py`
- **Single class**: `pytest tests/test_consolidator.py::TestContradictionResolution`
- **Single test**: `pytest tests/test_consolidator.py::TestConsolidateKeep::test_keep_calls_store_create`
- **Watch mode**: `watchexec -e py -- pytest tests/`
- **Verbose output**: `pytest -v`
- **Stop on first failure**: `pytest -x`
- **No real API calls**: guaranteed by `os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)` in `tests/conftest.py:12` and the mock pattern in every test that exercises LLM code paths.

## Coverage Gaps

- **Hooks**: `hooks/append_observation.py`, `hooks/consolidate_cron.py`, `hooks/session_start.py` are tested indirectly via `tests/test_hooks.py` (which covers `pre_compact.py` and `user_prompt_inject.py` directly).
- **Scripts**: `scripts/scan.py`, `scripts/reduce.py`, `scripts/consolidate.py` have partial coverage via `tests/test_scripts.py`. `scripts/diagnose.py`, `scripts/heartbeat.py`, `scripts/seed.py`, `scripts/dashboard.py`, `scripts/cost.py`, `scripts/compare.py` have no test coverage.
- **Embedding/VecStore integration**: vector operations are tested only via `test_models.py::TestVecEnabled` and `test_models.py::TestVecUnavailableFallback`. No tests exercise `VecStore.search_vector` with actual embeddings (Bedrock calls not mocked at boto3 layer).
- **Thompson sampling non-determinism**: `_thompson_rerank` uses `random.betavariate` without seeding. Tests that depend on retrieval order will produce non-deterministic results. Current tests avoid asserting ranked order.
- **Concurrent write contention**: no tests exercise dual-connection (peewee + apsw) contention scenarios.
