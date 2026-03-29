# Context: Wire Narrative Threads into Retrieval Pipeline

**Date:** 2026-03-28
**Mode:** Panel discussion (--auto)
**Slug:** thread-retrieval

## Work Description

Wire narrative threads into the memesis retrieval pipeline as first-class, budget-aware participants. Replace the N+1 piggyback retrieval with a batch query, add a dedicated thread budget, and add `last_surfaced_at` tracking for future decay decisions.

## Locked Decisions

### D1: Membership-based injection only
Threads are injected because their member memories were selected by Tier 2 — not independently scored or FTS-matched. The membership signal ("you're seeing memory X, and X belongs to this arc") is semantically stronger than keyword matching. A thread whose members aren't in context is noise, not signal.

### D2: Batch query replaces N+1 loop
Add `get_threads_for_memories_batch(memory_ids)` to MemoryStore. Single SQL query with `WHERE tm.memory_id IN (...)` returning full thread rows including narrative. Replaces both the per-memory `get_threads_for_memory()` loop and the per-thread `get_thread()` lookup in `_get_thread_narratives()`.

### D3: Separate thread budget (THREAD_BUDGET_CHARS)
Thread narratives get their own character budget, separate from the Tier 2 token budget. Set at 8,000 chars (~2K tokens) — conservative to avoid silent context expansion. Greedy selection sorted by narrative length ascending (shortest first, maximizes distinct arcs). Individual narratives also capped at 1,000 chars as a guardrail.

### D4: Add `last_surfaced_at` column to narrative_threads
Schema migration via try/except ALTER TABLE pattern. Updated lazily when threads are surfaced during injection. No stored `relevance_score` — just a timestamp for future decay/archival decisions. No `surface_count` for now.

### D5: No FTS on threads (deferred)
No `narrative_threads_fts` table in this iteration. Thread FTS in active_search is a future enhancement. The membership-based batch query is sufficient for current needs.

### D6: No independent thread scoring (deferred)
No `get_threads_for_context()`, no composite relevance formula for threads, no thread-level archival in this iteration. These require feedback data we don't have yet.

## Conventions to Enforce

- Per-operation SQLite connections (`with sqlite3.connect(self.db_path) as conn:`)
- Parameterized IN clause for batch query (no string interpolation of IDs)
- Manual FTS sync pattern if any FTS is added (delete-then-insert)
- ValueError for not-found thread IDs
- Budget enforcement uses greedy loop pattern from `get_crystallized_for_context`

## Concerns to Watch

- Batch query must return full thread rows including `narrative` — not stubs that trigger N+1 fallback
- `last_surfaced_at` update must not add write latency to SessionStart hot path (lazy update in `_get_thread_narratives`, not in `inject_for_session`)
- Individual narrative cap (1,000 chars) must not silently truncate mid-sentence — truncate at last sentence boundary if possible
- Existing tests for thread injection (`test_threads.py::TestRetrievalThreadInjection`) must continue passing

## Reusable Code

- `get_threads_for_memory` SQL (storage.py:923) — adapt for batch IN clause
- `get_crystallized_for_context` greedy budget loop (retrieval.py:255-268) — pattern for thread budget
- `_fts_insert` / `_fts_delete` helpers (storage.py:211-223) — if FTS is added later
- `search_fts` JOIN pattern (storage.py:556) — template for any future thread FTS

## Canonical References

- `core/retrieval.py` — RetrievalEngine, inject_for_session, _get_thread_narratives
- `core/storage.py` — MemoryStore, narrative_threads table, thread CRUD methods
- `core/threads.py` — ThreadDetector, ThreadNarrator, build_threads
- `tests/test_threads.py` — TestRetrievalThreadInjection, TestThreadCRUD
- `tests/test_retrieval.py` — inject_for_session tests
