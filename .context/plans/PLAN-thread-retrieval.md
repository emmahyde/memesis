# Implementation Plan: Wire Narrative Threads into Retrieval Pipeline

**Date:** 2026-03-28
**Context doc:** `.context/CONTEXT-thread-retrieval.md`
**Slug:** thread-retrieval

---

## Summary

Two waves. Wave 1 owns the storage layer: schema migration, batch query method, and storage-layer tests. Wave 2 owns the retrieval layer: replaces the N+1 loop in `_get_thread_narratives`, adds the thread budget, and adds retrieval-layer tests. The two waves are strictly sequential — the retrieval layer consumes the new `get_threads_for_memories_batch` method and the migrated schema, both of which Wave 1 delivers.

---

## Wave 1 — Storage Layer

### Task 1.1 — Schema migration: add `last_surfaced_at` to `narrative_threads`

**Summary:** Add the `last_surfaced_at` column to `narrative_threads` via the established try/except ALTER TABLE migration pattern.

**Files owned:**
- `core/storage.py`

**Depends on:** none

**Decisions:** D4

**What to do:**

Inside `_init_db`, immediately after the existing `narrative_threads` CREATE TABLE block (line ~195), add a migration guard following the same pattern as `archived_at`, `subsumed_by`, and `project_context`:

```python
# Migration: add last_surfaced_at for thread decay/archival decisions
try:
    conn.execute(
        "ALTER TABLE narrative_threads ADD COLUMN last_surfaced_at TEXT"
    )
except sqlite3.OperationalError:
    pass  # Column already exists
```

No default value. The column is nullable TEXT (ISO-8601 timestamp or NULL). The update happens lazily in `_get_thread_narratives` (Wave 2), not here.

**Acceptance criteria:**
- A freshly initialised store has `last_surfaced_at` in the `narrative_threads` schema (`PRAGMA table_info(narrative_threads)` returns the column).
- Re-initialising a store that already has the column does not raise or error.
- All existing thread CRUD tests (`TestThreadCRUD`) continue to pass without modification.

---

### Task 1.2 — Batch query: add `get_threads_for_memories_batch`

**Summary:** Add `MemoryStore.get_threads_for_memories_batch(memory_ids)` — a single SQL query replacing the per-memory N+1 loop in `_get_thread_narratives`.

**Files owned:**
- `core/storage.py`

**Depends on:** none (can run in parallel with Task 1.1 as a code change, but will share the file — see Cross-Wave Ownership Handoffs below)

**Decisions:** D2

**What to do:**

Add the method after `get_threads_for_memory` (line ~931). The method must:

1. Accept `memory_ids: list[str]`. Return `[]` immediately if the list is empty (avoids invalid SQL with an empty IN clause).
2. Build a parameterized IN clause — no string interpolation of IDs. Use `",".join("?" * len(memory_ids))` for the placeholder string.
3. Issue a single `SELECT DISTINCT nt.*` query joining `thread_members`:

```python
def get_threads_for_memories_batch(self, memory_ids: list[str]) -> list[dict]:
    """
    Return all narrative threads that contain any of the given memory IDs.

    Single query replacing the per-memory N+1 loop. Deduplication is done
    in SQL via SELECT DISTINCT. Returns full thread rows including narrative.

    Args:
        memory_ids: List of memory IDs to look up threads for.

    Returns:
        List of thread dicts (without member_ids, same shape as
        get_threads_for_memory). Returns [] if memory_ids is empty.
    """
    if not memory_ids:
        return []

    placeholders = ",".join("?" * len(memory_ids))
    with sqlite3.connect(self.db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            f"SELECT DISTINCT nt.* FROM narrative_threads nt "
            f"JOIN thread_members tm ON nt.id = tm.thread_id "
            f"WHERE tm.memory_id IN ({placeholders}) "
            f"ORDER BY nt.updated_at DESC",
            memory_ids,
        )
        return [dict(row) for row in cursor.fetchall()]
```

Note: the f-string is safe here — the only interpolated value is the placeholder string `"?,?,?"`, not user data. The actual `memory_ids` values are passed as the parameterized tuple.

**Acceptance criteria:**
- Calling with an empty list returns `[]` without hitting the database.
- Calling with one ID returns the same threads as `get_threads_for_memory` for that ID.
- Calling with multiple IDs where two memories share a thread returns that thread exactly once (DISTINCT deduplicates).
- Calling with IDs that have no threads returns `[]`.
- The returned dicts include the `narrative` field (full thread row, not a stub).
- The returned dicts include `last_surfaced_at` once Task 1.1's migration has run (column is present in `nt.*`).

---

### Task 1.3 — Storage tests

**Summary:** Add `TestBatchThreadQuery` and `TestLastSurfacedAtMigration` test classes to `tests/test_threads.py`.

**Files owned:**
- `tests/test_threads.py`

**Depends on:** Tasks 1.1 and 1.2 (tests verify the new method and schema column)

**Decisions:** D2, D4

**What to do:**

Append two new test classes to `tests/test_threads.py`. Do not modify any existing test class.

**`TestLastSurfacedAtMigration`** — mirrors the existing schema migration test pattern from `test_storage.py::TestMemoryStoreMetadata::test_schema_migration_adds_project_context_column`:

- `test_new_store_has_last_surfaced_at_column`: initialise a fresh `MemoryStore(tmp_path)`, query `PRAGMA table_info(narrative_threads)`, assert `last_surfaced_at` is present.
- `test_migration_is_idempotent`: initialise two `MemoryStore` instances pointing at the same `tmp_path`. Assert no exception is raised on the second init (column-already-exists path is exercised).
- `test_last_surfaced_at_defaults_null`: create a thread, fetch it back with `store.get_thread(...)`, assert `result["last_surfaced_at"]` is `None`.

**`TestBatchThreadQuery`**:

- `test_empty_list_returns_empty`: `store.get_threads_for_memories_batch([])` returns `[]`.
- `test_single_memory_matches_per_memory_method`: create two memories and one thread linking them; assert `get_threads_for_memories_batch([m1])` returns same thread as `get_threads_for_memory(m1)`.
- `test_multiple_memories_deduplicates_shared_thread`: create m1, m2 both members of one thread; assert `get_threads_for_memories_batch([m1, m2])` returns exactly one thread.
- `test_returns_full_narrative`: assert the returned dict contains the `narrative` field with the full text (not None, not truncated).
- `test_no_threads_returns_empty`: call with valid memory IDs that have no threads; assert `[]`.
- `test_cross_thread_union`: create m1 → thread A, m2 → thread B, m3 → both; assert `get_threads_for_memories_batch([m1, m2, m3])` returns exactly two distinct threads.

**Acceptance criteria:**
- All six new `TestBatchThreadQuery` tests pass.
- All three new `TestLastSurfacedAtMigration` tests pass.
- No existing tests in the file are modified or broken.

---

## Wave 2 — Retrieval Layer

### Task 2.1 — Refactor `_get_thread_narratives` with batch query and thread budget

**Summary:** Replace the N+1 loop in `_get_thread_narratives` with the new batch method, add `THREAD_BUDGET_CHARS` enforcement (greedy shortest-first), apply per-narrative 1,000-char cap, and lazily update `last_surfaced_at` on surfaced threads.

**Files owned:**
- `core/retrieval.py`

**Depends on:** Wave 1 Tasks 1.1 and 1.2 (requires `get_threads_for_memories_batch` and `last_surfaced_at` column)

**Decisions:** D1, D2, D3, D4

**What to do:**

**1. Add the module-level constant** near the top of `retrieval.py` alongside any existing budget constants:

```python
THREAD_BUDGET_CHARS = 8_000
_THREAD_NARRATIVE_CAP = 1_000  # per-narrative hard cap
```

**2. Replace `_get_thread_narratives`** entirely. The new implementation:

```python
def _get_thread_narratives(self, tier2_memories: list[dict]) -> list[dict]:
    """
    Find narrative threads whose members appear in tier2_memories.

    Uses a single batch query (no N+1). Applies a per-narrative character
    cap and a total THREAD_BUDGET_CHARS budget, selecting shortest-first
    to maximise the number of distinct arcs injected. Updates
    last_surfaced_at lazily for threads that are selected.
    """
    if not tier2_memories:
        return []

    memory_ids = [m["id"] for m in tier2_memories]
    candidates = self.store.get_threads_for_memories_batch(memory_ids)

    # Apply per-narrative cap before budget calculation (cap, don't skip)
    for t in candidates:
        narrative = t.get("narrative") or ""
        if len(narrative) > _THREAD_NARRATIVE_CAP:
            # Truncate at last sentence boundary within the cap
            truncated = narrative[:_THREAD_NARRATIVE_CAP]
            last_period = truncated.rfind(".")
            if last_period > _THREAD_NARRATIVE_CAP // 2:
                truncated = truncated[: last_period + 1]
            t["narrative"] = truncated

    # Greedy budget selection: shortest narrative first (maximises arc count)
    candidates_sorted = sorted(
        candidates, key=lambda t: len(t.get("narrative") or "")
    )

    budget_remaining = THREAD_BUDGET_CHARS
    selected = []
    for thread in candidates_sorted:
        cost = len(thread.get("narrative") or "")
        if cost <= budget_remaining:
            selected.append(thread)
            budget_remaining -= cost

    # Lazy update: record that these threads were surfaced
    if selected:
        now = datetime.utcnow().isoformat()
        self.store.update_threads_last_surfaced(
            [t["id"] for t in selected], now
        )

    return selected
```

**3. Add `update_threads_last_surfaced` to `MemoryStore`** (this method is small enough to implement here as part of the retrieval task, since it is a pure write helper with no schema dependency beyond Wave 1's column). Add it to `core/storage.py` — this file is owned by Wave 2 for this specific addition.

```python
def update_threads_last_surfaced(self, thread_ids: list[str], timestamp: str) -> None:
    """
    Lazily update last_surfaced_at for the given thread IDs.

    Called after thread narratives are selected for injection. Uses a
    single UPDATE ... WHERE id IN (...) for efficiency.
    """
    if not thread_ids:
        return
    placeholders = ",".join("?" * len(thread_ids))
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            f"UPDATE narrative_threads SET last_surfaced_at = ? "
            f"WHERE id IN ({placeholders})",
            [timestamp, *thread_ids],
        )
        conn.commit()
```

**Acceptance criteria:**
- `_get_thread_narratives` issues exactly one SQL query (the batch call) regardless of how many memories are in `tier2_memories`. No `get_threads_for_memory` or `get_thread` calls remain in the method.
- When total narrative length across all candidate threads exceeds 8,000 chars, only threads fitting within budget are returned.
- When a single narrative exceeds 1,000 chars, it is truncated at a sentence boundary before being returned; the full original text is not returned.
- After `_get_thread_narratives` returns, `last_surfaced_at` is set on selected threads in the DB. Non-selected candidate threads retain their previous `last_surfaced_at` value.
- `update_threads_last_surfaced([])` is a no-op (no SQL executed, no error).
- All existing `TestRetrievalThreadInjection` tests (`test_thread_narratives_injected`, `test_no_threads_no_section`, `test_thread_deduplication`) continue to pass without modification.

---

### Task 2.2 — Retrieval and budget tests

**Summary:** Add `TestThreadBudget` and `TestLastSurfacedAtTracking` test classes to `tests/test_retrieval.py`.

**Files owned:**
- `tests/test_retrieval.py`

**Depends on:** Wave 1 Tasks 1.1 and 1.2; Wave 2 Task 2.1

**Decisions:** D3, D4

**What to do:**

Append two new test classes to `tests/test_retrieval.py`. Do not modify existing tests.

**`TestThreadBudget`**:

- `test_short_threads_fit_within_budget`: create three crystallized memories and three threads with short narratives (~200 chars each); assert all three appear in `inject_for_session` output.
- `test_threads_over_budget_excluded`: create threads whose total narrative length exceeds 8,000 chars; assert only the subset fitting within budget appears (greedy shortest-first ordering).
- `test_single_thread_over_narrative_cap_is_truncated`: create a thread with a narrative of 1,200 chars (5 sentences); assert the narrative in the output is at most 1,000 chars and ends at a sentence boundary (`.`).
- `test_shortest_first_maximises_arc_count`: create four threads where the longest alone would exhaust the budget but three shorter ones fit together; assert three threads appear in output, not one.
- `test_budget_zero_excludes_all`: patch `THREAD_BUDGET_CHARS` to 0 via `monkeypatch.setattr`; assert no thread narratives appear in output.

**`TestLastSurfacedAtTracking`**:

- `test_surfaced_threads_get_timestamp`: create a memory and thread, call `engine.inject_for_session(...)`, then query `store.get_thread(thread_id)` and assert `last_surfaced_at` is a non-None ISO-8601 string.
- `test_non_surfaced_threads_unchanged`: create two threads where only one member is injected (the other's member is not in Tier 2); assert only the injected thread has a non-None `last_surfaced_at`.
- `test_update_threads_last_surfaced_noop_on_empty`: call `store.update_threads_last_surfaced([], "2026-03-28T00:00:00")` directly; assert no error and database is unmodified.

**Acceptance criteria:**
- All five `TestThreadBudget` tests pass.
- All three `TestLastSurfacedAtTracking` tests pass.
- No existing tests in `tests/test_retrieval.py` are modified or broken.
- `pytest tests/test_threads.py tests/test_retrieval.py` exits clean (0 failures, 0 errors).

---

## File Ownership Map

| File | Wave | Task | Mode |
|------|------|------|------|
| `core/storage.py` | 1 | 1.1, 1.2 | modify (schema migration + new method) |
| `core/storage.py` | 2 | 2.1 | modify (add `update_threads_last_surfaced`) |
| `core/retrieval.py` | 2 | 2.1 | modify (refactor `_get_thread_narratives`, add constant) |
| `tests/test_threads.py` | 1 | 1.3 | modify (append new test classes) |
| `tests/test_retrieval.py` | 2 | 2.2 | modify (append new test classes) |

---

## Cross-Wave Ownership Handoffs

| File | Wave 1 task | What Wave 1 does | Wave 2 task | What Wave 2 does | Constraint |
|------|-------------|------------------|-------------|------------------|------------|
| `core/storage.py` | 1.1 | Adds `last_surfaced_at` column via ALTER TABLE migration in `_init_db` | 2.1 | Adds `update_threads_last_surfaced` write helper | Wave 2 appends after Wave 1's changes; must not remove the migration block |
| `core/storage.py` | 1.2 | Adds `get_threads_for_memories_batch` method | 2.1 | Calls `self.store.get_threads_for_memories_batch(...)` from `_get_thread_narratives` | Wave 2 depends on the method signature Wave 1 establishes |

---

## Decision Traceability

| Decision | Implemented in | Notes |
|----------|---------------|-------|
| D1 Membership-based injection only | Wave 2 Task 2.1 | `_get_thread_narratives` still driven entirely by `tier2_memories` membership; no independent FTS or scoring |
| D2 Batch query replaces N+1 | Wave 1 Task 1.2 (method), Wave 2 Task 2.1 (call site) | `get_threads_for_memories_batch` replaces both `get_threads_for_memory` loop and `get_thread` lookup |
| D3 Separate thread budget (8000 chars, 1000/narrative cap, shortest-first) | Wave 2 Task 2.1, Wave 2 Task 2.2 | `THREAD_BUDGET_CHARS = 8_000`, `_THREAD_NARRATIVE_CAP = 1_000`, sort ascending by narrative length before greedy loop |
| D4 `last_surfaced_at` lazy update | Wave 1 Task 1.1 (schema), Wave 2 Task 2.1 (update call) | Column added in migration; `update_threads_last_surfaced` called after budget selection in `_get_thread_narratives`, not in `inject_for_session` |
| D5 No FTS on threads (deferred) | — | No `narrative_threads_fts` table added; no changes to search paths |
| D6 No independent thread scoring (deferred) | — | No `get_threads_for_context`, no composite relevance formula |
