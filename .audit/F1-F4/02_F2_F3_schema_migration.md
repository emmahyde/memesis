# Audit: F2 Migration 0008 + F3 Schema Change

**Verdict: CONCERN** — migration is sound; `obs_ids` schema field has two unguarded failure modes that degrade silently rather than raising.

---

## 1. Migration 0008 — Idempotency and Coverage

**File:** `core/migrations/sql/20260514_0008_add_consolidation_compression_ratio.sql:7`

```sql
ALTER TABLE consolidation_log ADD COLUMN compression_ratio REAL;
```

**Naming convention:** Runner (`core/migrations/__init__.py:74-81`) sorts files lexicographically by stem. `20260514_0008_` sorts after all existing migrations correctly.

**Idempotency:**
- On already-patched DBs: `_apply_sql` wraps each statement in try/except and silently ignores `"duplicate column"` errors (`__init__.py:101-112`). Safe.
- On fresh DBs: Column doesn't exist, ALTER executes cleanly. Safe.
- On DBs at `SEED_THRESHOLD` (user_version >= 2): The seeding path (`__init__.py:147-167`) only seeds stems listed in `_LEGACY_MIGRATION_STEMS` (`__init__.py:40-43`), which contains only `20260507_0001_initial_alters` and `20260507_0002_consolidation_log_check`. Migration 0008 is **not** in that set, so it will execute normally even in seed mode. Correct — this is a new migration, not a legacy one.

**Conclusion:** Migration is fully idempotent across all three entry states.

---

## 2. Prior Migrations Touching consolidation_log

Three prior migrations touch the table:

- `20260507_0001_initial_alters.sql:55-62` — adds `prompt`, `llm_response`, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `input_observation_refs` via individual ALTER TABLE statements.
- `20260507_0002_consolidation_log_check.py:39-58` — DROP + CREATE TABLE (rebuilds schema to add `subsumed`/`archived` to the action CHECK constraint). This is the only destructive DDL in the migration history. It recreates the table with a **narrow column set** (7 columns, no observer instrumentation columns), relying on 0001 having previously added them and the rebuild SELECT only preserving the 7 pre-instrumentation columns.
- `20260514_0008_...sql:7` — this migration, adds `compression_ratio`.

**Risk:** Migration 0002 (`consolidation_log_check.py:35`) reads only `timestamp, session_id, action, memory_id, from_stage, to_stage, rationale` and recreates the table with just those columns. If 0001 ran first (correct order), the observer columns exist at DROP time and are silently lost. Because 0001 and 0002 are both in `_LEGACY_MIGRATION_STEMS`, they are seeded (not executed) on existing DBs with user_version >= 2, so this sequence only matters for fresh DB bootstraps. On fresh DBs, both run in order: 0001 adds observer columns, 0002 drops and recreates — **stripping them**. Then 0001's columns must be re-added by subsequent ADD COLUMN statements in 0001 which won't re-run (already recorded). This is a latent ordering bug unrelated to 0008, but worth flagging for completeness.

---

## 3. models.py Type Compatibility

`core/models.py:613`:
```python
compression_ratio = FloatField(null=True)
```

Migration 0008 uses `REAL` (SQLite's 64-bit IEEE float). Peewee `FloatField` maps to Python `float` / SQLite `REAL`. These are identical types. **Compatible.**

The field is `null=True`, which matches the migration (no DEFAULT, no NOT NULL), so legacy rows retain NULL and new rows populated by the consolidator will write a float. No mismatch.

---

## 4. Schema Change: obs_ids in ConsolidationDecision

**File:** `core/schemas.py:148`
```python
obs_ids: list[int] = Field(default_factory=list)
```

**Case-by-case:**

**Missing obs_ids field (back-compat):** `default_factory=list` means Pydantic substitutes `[]` when the LLM omits the field entirely. The consolidator at `core/consolidator.py:173` does `decision.get("obs_ids") or []` and falls back to `_refs_for_observation` when empty. Back-compat path is intact.

**obs_ids as strings ["1","2"] instead of ints:** Pydantic v2 with `list[int]` will coerce `"1"` → `1` via its default `int` validator when the input is a numeric string. This succeeds silently. However, if the LLM sends non-numeric strings like `["obs_1", "obs_2"]`, Pydantic raises `ValidationError`, the per-decision try/except in `_parse_decisions` (`consolidator.py:674`) catches it, logs a warning, and the decision is **silently dropped**. This means a valid keep/promote decision can be lost without operator visibility beyond a single WARNING log line.

**obs_ids referencing out-of-range ordinals (e.g., [99] when only 5 blocks exist):** `_refs_for_obs_ids` (`consolidator.py:395-397`) silently skips unknown ordinals — `if oid in ordinal_to_id`. The result is an empty `refs` list. The decision still executes (it's not rejected), but `_mark_observations` receives no IDs to mark, so the observation rows remain in `pending` state indefinitely. **Silent data integrity failure.**

**Empty list:** Falls through to `_refs_for_observation` fallback (`consolidator.py:176-177`). Behaves like legacy pre-F3 path. Safe.

**No field_validator on obs_ids:** There is no validator that checks element range, deduplication, or non-negativity. The field accepts `[0, -1, 999]` without complaint.

---

## 5. validate_decisions / Pydantic Mismatch Handling

`_parse_decisions` (`consolidator.py:667-679`) validates each decision individually. On `PydanticValidationError`, it logs a warning and **skips the decision entirely** — it does not raise, does not mark the observation as failed, and does not surface the drop to the caller. The captured observation_refs for that decision will remain `pending` unless a later consolidation session picks them up. This is a silent loss path.

There is no aggregate validation (e.g., "at least N decisions parsed from M inputs"). A response where the LLM returns 10 decisions and 9 fail Pydantic validation returns a single-element list with no error raised.

---

## 6. Recommendations

1. **Add a range validator for obs_ids** in `ConsolidationDecision` — reject ordinals <= 0, deduplicate. Ordinal bounds can't be checked at parse time (block count unknown), but the `_refs_for_obs_ids` path should log a WARNING when it silently drops an unresolved ordinal rather than failing silently.

2. **Mark observations `failed` on Pydantic skip** — when `_parse_decisions` drops a decision due to `ValidationError`, the observation refs in that dict are already present in the raw dict (`rd`). Extract and mark them failed to prevent `pending` accumulation.

3. **Add a field_validator coercing string integers** — explicitly handle `["1","2"]` → `[1,2]` rather than relying on Pydantic's implicit coercion behavior, which differs between strict and lax mode and could change in a minor Pydantic update.

4. **Migration 0002 ordering hazard** — document (or fix) the fact that 0002's DROP+CREATE drops all columns added by 0001 on fresh DB bootstraps. The columns are re-added only if 0001 runs after 0002, which the lexicographic sort prevents. On seeded DBs this is benign, but the migration-only bootstrap path is fragile.
