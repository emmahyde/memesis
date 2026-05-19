# Audit: Test Coverage and Failure Modes — F1–F4

**Verdict: CONCERN**

The suite is 2182 passing tests but has 19 failures; 11 of those are the entire `test_migrations.py` class, which is the primary regression gate for F2 (migration 0008). F1, F3, and F4 fixes are UNCOVERED at the unit level. The migration test failures expose a real fixture gap, not a pre-existing condition.

---

## 1. Coverage Classification per Fix

### F1 — `_split_observation_blocks` (consolidator.py:1443)

**UNCOVERED.**

No test in `tests/` calls `_split_observation_blocks` directly or indirectly in a way that would catch a regression. Searched all test files: zero matches for `_split_observation_blocks`, `split_observation`, `OBSERVATION_ID`, `inject_observation_ids`. `test_consolidator.py` exercises `consolidate_session` end-to-end, but all inputs are single-observation ephemeral strings; none verify block-count arithmetic. A silent change to `_split_observation_blocks`'s header-detection regex would produce wrong ordinals with no test catching it.

The prior audit (01_F1_splitter_correctness.md) recommended three tests; none have been added.

### F2 — Migration 0008 (`20260514_0008_add_consolidation_compression_ratio.sql`)

**THIN → effectively FAILING.**

`test_migrations.py` contains a general forward-run class (`TestRunMigrationsForwardRun`) that verifies all migration files are recorded and schema changes are applied. `test_migration_files_recorded` at line 67 asserts `path.stem in applied` for every file returned by `_migration_files()`, which includes `20260514_0008_add_consolidation_compression_ratio`. That test **is** a regression gate for 0008.

However, all 11 tests in `TestRunMigrationsForwardRun`, `TestRunMigrationsSeedingPath`, and `TestIdempotency` are currently FAILING with:

```
peewee.OperationalError: no such table: observations
```

Root cause: migration `20260513_0006_add_project_column.sql` executes `ALTER TABLE observations ADD COLUMN project TEXT` (line 2 of that file). The `empty_db` fixture in `test_migrations.py:61-132` does not create an `observations` table — it creates `memories`, `retrieval_log`, `narrative_threads`, `memory_edges`, and `consolidation_log`. Migration 0006 then fails, the `atomic()` transaction rolls back, and `_record_version` is never called. Every subsequent migration test fails in cascade.

This fixture gap predates 0008 and would have caught 0008 only if 0006 were fixed. As written, migration 0008 has **no passing test gate**.

### F3 — `_inject_observation_ids` / `_refs_for_obs_ids` / `obs_ids` schema field

**UNCOVERED (all three sub-components).**

- `_inject_observation_ids` (consolidator.py:399): no test calls it directly or constructs an ephemeral file and asserts `OBSERVATION_ID:` prefixes in the rendered prompt.
- `_refs_for_obs_ids` (consolidator.py:387): no test exercises the ordinal-lookup path. End-to-end `consolidate_session` tests in `test_consolidator.py` mock the LLM transport and return decision dicts that always omit `obs_ids`; the code falls through to `_refs_for_observation` on every test run.
- `obs_ids` schema field (schemas.py:148): `test_schemas.py`'s `test_promote_decision_parses` (line 446) does not pass `obs_ids`; the field is never asserted. `test_extra_fields_silently_allowed` (line 457) confirms forward-compat but does not test `obs_ids` parsing.

The entire F3 code path — from ordinal injection through LLM echo to observation marking — runs zero times in the test suite.

### F4 — PROMOTE-vs-KEEP prompt softening

**THIN.**

`test_prompts.py:test_behavioral_gate_language` (line 195) asserts "would I do something wrong" appears in `CONSOLIDATION_PROMPT`. `test_no_most_should_die_language` (line 199) asserts "MOST SHOULD DIE" is absent. `test_behavioral_gate_in_consolidation` (line 378) checks for "behavioral gate" in the prompt.

These tests lock the **presence** of specific strings; they do not verify gate ordering, LLM routing correctness, or the BEHAVIORAL GATE vs SELECTIVITY tension. The "Most observations should KEEP" selectivity line (prompts.py:208) and the PROMOTE exactness requirement (prompts.py:149-163) have no dedicated assertions. A refactor that swaps KEEP/PRUNE trigger language would pass all tests if it preserved the three target substrings.

`test_consolidator.py:TestConsolidatePromote` (line 249) has three tests that exercise the PROMOTE code path but they mock LLM responses with explicit `reinforces` UUIDs — they do not test the prompt's routing gate, only the executor.

---

## 2. Test Run Results

```
uv run pytest tests/ -q 2>&1 | tail -30
```

```
19 failed, 2182 passed, 173 warnings in 131.11s (0:02:11)
```

Failures:
- `test_integration.py::TestFullLifecycleEphemeralToCrystallized::test_full_lifecycle_ephemeral_to_crystallized` — 1
- `test_migrations.py` — 11 (all in `TestRunMigrationsForwardRun`, `TestRunMigrationsSeedingPath`, `TestIdempotency`, `TestPyMigration`)
- `test_skills.py` — 7 (all `[usage]` parameterized variants, unrelated to F1–F4; the `usage` skill file was renamed to `retrieval` per the recent `refactor(skills): rename usage → retrieval` commit)

All migration failures trace to the same root: `empty_db` fixture missing `observations` table.

---

## 3. Failure Modes Still Possible

**LLM returns malformed JSON:** `_call_llm` (consolidator.py:147) enters a retry loop; two consecutive bad responses raise and trigger `_mark_observations(captured_ids, "failed")` (consolidator.py:150-157). Covered by `test_consolidator.py:test_retry_on_malformed_json` (line 377) and `test_raises_on_two_consecutive_bad_responses` (line 402). Adequately handled.

**LLM returns valid JSON with `obs_ids` referencing out-of-range ordinals:** `_refs_for_obs_ids` silently returns `[]` (consolidator.py:397). The decision executes, a memory is created or reinforcement count is bumped, but the source observation row is never marked — it stays `pending` indefinitely and eventually becomes `orphaned`. No log line is emitted at the point of the silent drop. No test covers this path.

**`format_observation` changes shape:** If `format_observation` (prompts.py:79) switches its header from `## [timestamp]` to anything else, `_split_observation_blocks`'s `startswith('## ')` guard fails to split, the entire file becomes one block, one observation row is created, and ordinals misalign silently. Zero tests cross-exercise `format_observation` output with `_split_observation_blocks` input.

**Ephemeral `.md` edited by hand between extraction and consolidation:** If the file is modified after `_record_observations` captures the refs but before `_inject_observation_ids` reads it, the two calls produce different block counts. The LLM sees different ordinals than those in the ref dict. OBSERVATION_ID echoes from the LLM will resolve to wrong observation rows, or fall out of range and silently produce `[]`. No test covers this time-of-check-to-time-of-use gap.

**Two concurrent ingests on the same `project_memory_dir`:** `Consolidator` is not a singleton; two instances share the SQLite WAL-mode DB (WAL + `busy_timeout=5000` per CLAUDE.md). Both `_record_observations` calls can succeed independently with overlapping ordinals since ordinals are local to the in-memory ref list, not the DB. Both ingest runs then inject OBSERVATION_ID prefixes independently, producing two sets of `OBSERVATION_ID: 1..N` that refer to different DB rows. If any result is replayed, ordinals from run A will be looked up against run B's refs. No concurrency test covers this.

**Migration 0008 fails partway:** `_apply_sql` wraps each statement in a `try/except` that re-raises anything other than `"duplicate column"`, `"already exists"`, or `"no such column"` (migrations/__init__.py:101-112). The `with conn.atomic()` block at line 172 rolls back on exception and `_record_version` is never called (line 180 is inside the `with` block). A subsequent `run_migrations` call will re-attempt the failed migration. If the failure is transient (disk full, lock contention), the migration is correctly retried. If the failure is permanent (schema invariant violated), the runner will raise on every future startup. There is no partial-apply risk within a single statement.

---

## 4. Top 5 Tests to Add (Ordered by Leverage)

**1. Round-trip split count for `_split_observation_blocks`**
Code being tested: `core/consolidator.py:1443–1475` (`_split_observation_blocks`) and `core/prompts.py:79–103` (`format_observation`).
Construct N ≥ 3 calls to `format_observation`, concatenate, pass to `_split_observation_blocks`, assert `len(result) == N`. Locks the header-delimiter contract. Catches any future `format_observation` format change or splitter regex change. Currently zero tests cross-exercise these two functions.

**2. `_inject_observation_ids` ordinal alignment**
Code being tested: `core/consolidator.py:399–415` (`_inject_observation_ids`) and `core/consolidator.py:349–385` (`_record_observations`).
Given a two-observation ephemeral string and a populated `refs` list (from `_record_observations`), call `_inject_observation_ids` and assert that block `i` in the output contains prefix `OBSERVATION_ID: {i+1}` and that ordinal `i+1` in refs maps to the corresponding observation row. Catches the cross-split skew described in F3 audit §7.

**3. Fix `empty_db` fixture to include `observations` table; add 0008-specific column assertion**
Code being tested: `core/migrations/sql/20260514_0008_add_consolidation_compression_ratio.sql:7` and `core/migrations/__init__.py:85–113` (`_apply_sql`).
The fixture at `test_migrations.py:61` must add `CREATE TABLE IF NOT EXISTS observations (...)`. After `run_migrations`, assert `compression_ratio` exists in `PRAGMA table_info(consolidation_log)`. Currently the migration runner tests are ALL failing; no F2 regression gate is active.

**4. `_refs_for_obs_ids` with out-of-range ordinal logs a warning**
Code being tested: `core/consolidator.py:387–397` (`_refs_for_obs_ids`).
Call `_refs_for_obs_ids([99], [{"ordinal": 1, "id": 1}])` and assert result is `[]`. Then assert (with `caplog`) that a WARNING is emitted. Currently `_refs_for_obs_ids` returns silently; the only way to detect the silent drop is to notice that observation rows stay `pending`. This test both covers the ordinal path and gates the logging fix recommended in audit 02 §6.

**5. `obs_ids` round-trip through `ConsolidationDecision` and consolidator PROMOTE path**
Code being tested: `core/schemas.py:148` (`obs_ids` field) and `core/consolidator.py:173–177` (dispatch to `_refs_for_obs_ids` vs fallback).
Construct a `consolidate_session` call where the mocked LLM response includes `"obs_ids": [1]` in a KEEP decision. Assert that `_mark_observations` is called with the observation row IDs corresponding to ordinal 1 (not the fallback substring-match result). Currently no test exercises the `obs_ids`-present branch; every test hits the fallback because all mock decisions omit `obs_ids`.

---

## 5. Deferred Concerns from F3 + F4 Reports Now Requiring Lock-In

From **audit 03** (F3 obs_ids pairing): The DB `Observation.ordinal` column stores 0-indexed values (consolidator.py:369: `ordinal=index`) while all in-memory references use 1-indexed ordinals (consolidator.py:380: `"ordinal": index + 1`). Any diagnostic query or backfill script that joins on `Observation.ordinal` to reconstruct pairing will be silently off by one. This should be locked with either a test asserting `obs.ordinal == 0` for the first observation (documenting the intent) or a migration that makes the DB column 1-indexed to match the ref dict.

From **audit 02** (F2/F3 schema): `_parse_decisions` (consolidator.py:667–679) drops Pydantic-invalid decisions silently — no observation rows are marked `failed`, no caller-visible error is raised. If a large LLM response has 10 decisions and 9 fail validation (e.g., all include `"obs_ids": ["obs_1"]` with non-numeric strings), only 1 memory is created and nothing surfaces the loss. This needs either a metric (decisions_dropped counter) or a test that constructs a multi-decision response with one invalid entry and asserts that the valid decision succeeds AND that dropped-decision observation rows are marked `failed` rather than left `pending`.

From **audit 04** (F4 prompt bias): The `BEHAVIORAL GATE vs SELECTIVITY` tension (prompts.py:115 vs 208) has no empirical test. A parameterized pytest fixture that feeds a curated observation to a real LLM call (or a deterministic mock that replays a known response) and asserts `action == "keep"` for borderline observations would lock the routing intent. Without it, a prompt edit that removes the SELECTIVITY line (line 208) would pass all current tests.
