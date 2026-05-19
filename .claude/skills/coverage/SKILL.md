---
name: coverage
description: >
  Test coverage gap analysis for memesis modules. Enumerates behaviors for a given module,
  checks which are tested, identifies missing edge cases, and checks shadow-mode gaps. Use
  before a refactor, after an incident where tests didn't catch a bug, or when planning tests
  for a new feature. Triggers on: "test coverage", "coverage gap", "what's tested", "missing
  tests", "before I refactor", "test planning", /memesis:coverage.
---

# Test Coverage Gap Analysis

**Invoked as:** `/memesis:coverage`

For a module: enumerate its public behaviors, verify each is covered, identify what's missing or shadow-mode-dependent.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

---

## Module → Test File Map

| Module | Test File |
|--------|----------|
| `core/lifecycle.py` | `tests/test_lifecycle.py` |
| `core/consolidator.py` | `tests/test_consolidator.py` (80KB) |
| `core/retrieval.py` | `tests/test_retrieval.py` (89KB) |
| `hooks/` | `tests/test_hooks.py` |
| `core/linking.py` | `tests/test_linking.py` |
| `core/self_reflection.py` | `tests/test_self_reflection.py` |
| `core/observability.py` | `tests/test_observability.py` |
| `core/migrations/` | `tests/test_migrations.py`, `tests/test_migration_w5.py` |
| `core/models.py` | `tests/test_models.py` |
| `core/schemas.py` | `tests/test_schemas.py` |
| `core/prompts.py` | `tests/test_prompts.py` |

---

## Workflow

### Step 1 — Map the module's public behaviors

Read the module's docstring and class-level docs. List every public method and edge case:
- Happy path
- Boundary conditions (empty input, zero counts, threshold exactly at limit)
- Error paths (invalid stage, missing memory, DB constraint violations)
- Shadow-mode-specific behavior (`SHADOW_ONLY=True`)

### Step 2 — Scan the test file for each behavior

```bash
uv run pytest tests/test_lifecycle.py -v --collect-only 2>&1 | grep "test_"
```

Check: does a test exist? Does it assert the observable artifact, or just that no exception was raised? Is it isolated (uses `tmp_path` fixtures)?

### Step 3 — Check shadow-mode gaps

`SHADOW_ONLY=True` in `core/observability.py` means pruning is logged but NOT executed. Tests that assert a pruned memory is hard-deleted are testing incorrect behavior. Shadow-mode tests should assert the JSONL log was written, not DB deletion.

### Step 4 — Run and check

```bash
uv run pytest tests/test_<module>.py -v --tb=short 2>&1 | tail -40
```

### Step 5 — Report gaps by severity

| Severity | Description |
|----------|------------|
| Critical | No test for a behavior that is complex and load-bearing |
| High | Edge cases untested; only happy path covered |
| Medium | Happy path tested; error paths not |
| Low | Minor variations untested; main contracts covered |

---

## Common Gap Patterns

- **Shadow mode assumptions:** Tests that expect hard deletes when `SHADOW_ONLY=True`
- **Ordinal mismatch:** Tests that join `Observation.ordinal` directly to LLM `obs_ids` without the +1 offset
- **Peewee singleton violations:** Tests that call `sqlite3.connect()` directly
- **No test for `auto_promote_if_dupe`:** The cosine dedup path in consolidation is often untested in integration

---

## Reporting Format

```
## Coverage Gap Analysis: [Module]

### Behaviors Enumerated
[List with covered/uncovered status]

### Gaps
[Each gap with severity and specific missing test description]

### Shadow-Mode Issues
[Any tests that incorrectly assume live pruning]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]
```
