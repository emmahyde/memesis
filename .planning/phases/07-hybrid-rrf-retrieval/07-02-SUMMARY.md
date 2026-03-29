---
phase: 07-hybrid-rrf-retrieval
plan: "02"
subsystem: retrieval
tags: [rrf, hybrid-search, wiring, tier2, tier3, user-prompt-inject, tdd]

# Dependency graph
requires:
  - hybrid_search() from 07-01
provides:
  - get_crystallized_for_context() with hybrid RRF path when query is provided
  - inject_for_session() with query + query_embedding forwarding to Tier 2
  - active_search() using hybrid_search with lazy embed_text import
  - search_and_inject() in user_prompt_inject.py using RetrievalEngine.hybrid_search
  - Performance validated: hybrid_search on 1000 memories < 500ms (FTS-only)
affects:
  - 08-prompt-aware-injection
  - 09-thompson-sampling
  - eval/retrieval harness (retrieval_fn now backed by hybrid search)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy embed_text import inside method body — avoids import-time Bedrock dependency"
    - "query=None guards hybrid path — no-query calls preserve static three-pass sort exactly"
    - "project_context boost = 1/(rrf_k + 0.5) ≈ 0.01639 added to RRF score for matching project"
    - "k=50 over-fetch in _crystallized_hybrid gives token budget room to pack from larger candidate set"
    - "FTS query (sanitized terms) for FTS leg, raw prompt[:500] for embedding leg"

key-files:
  created: []
  modified:
    - core/retrieval.py (get_crystallized_for_context, inject_for_session, active_search, _crystallized_hybrid)
    - hooks/user_prompt_inject.py (search_and_inject uses RetrievalEngine.hybrid_search)
    - tests/test_retrieval.py (TestCrystallizedHybrid + TestHybridPerformance classes)
    - tests/test_user_prompt_inject.py (TestSearchAndInjectHybrid + TestHybridPerformanceUserPrompt classes)

key-decisions:
  - "query=None preserves backward-compatible static sort — SessionStart injection has no query and must not regress"
  - "inject_for_session does NOT call embed_text itself — caller (hooks) pre-computes embedding to stay within 500ms budget"
  - "active_search calls embed_text lazily — Tier 3 is agent-initiated so ~200-400ms latency is acceptable"
  - "project_context boost applied after RRF — small additive bonus keeps project-relevant memories competitive without overriding strong semantic matches"
  - "_crystallized_hybrid over-fetches k=50 from hybrid_search then applies greedy token packing"

requirements-completed: [FOUND-01]

# Metrics
duration: ~5min
completed: 2026-03-29
---

# Phase 7 Plan 02: Hybrid RRF Wiring Summary

**All three retrieval tiers wired to hybrid_search: Tier 2 crystallized injection, Tier 3 active search, and UserPromptSubmit hook now use RRF-fused retrieval instead of static sort or raw FTS**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-29T22:14:10Z
- **Completed:** 2026-03-29
- **Tasks:** 2 (TDD — RED + GREEN for each)
- **Files modified:** 4

## Accomplishments

- Wired `get_crystallized_for_context` to hybrid RRF path when `query` is provided; preserved static three-pass sort for backward compat (no-query path)
- Added `_crystallized_hybrid` private method: k=50 over-fetch, project_context boost of ~0.0164, greedy token packing
- Extended `inject_for_session` with `query` + `query_embedding` params forwarded to Tier 2
- Replaced raw `Memory.search_fts` in `active_search` with `hybrid_search`; lazy `embed_text` import
- Replaced raw `Memory.search_fts` in `search_and_inject` with `RetrievalEngine.hybrid_search`; embed_text attempted on raw prompt[:500]
- 16 new tests (8 in `TestCrystallizedHybrid`, 1 in `TestHybridPerformance`, 5 in `TestSearchAndInjectHybrid`, 1 in `TestHybridPerformanceUserPrompt`) — all pass
- Performance test confirmed: hybrid_search on 1000 memories completes in < 500ms (FTS-only, no embedding API)
- Zero new dependencies added

## Task Commits

Each task was committed atomically with TDD discipline:

1. **Task 1 RED: Failing tests for hybrid wiring into crystallized + active_search** - `680c452` (test)
2. **Task 1 GREEN: Wire hybrid_search into get_crystallized_for_context and active_search** - `9345573` (feat)
3. **Task 2 RED: Failing tests for hybrid wiring in user_prompt_inject + perf test** - `5a00294` (test)
4. **Task 2 GREEN: Wire hybrid_search into UserPromptSubmit hook** - `7817647` (feat)

## Files Created/Modified

- `core/retrieval.py` — `get_crystallized_for_context` hybrid path, `_crystallized_hybrid` private method, `inject_for_session` query forwarding, `active_search` hybrid wiring; `get_vec_store` import added
- `hooks/user_prompt_inject.py` — `search_and_inject` uses `RetrievalEngine.hybrid_search`; imports `get_vec_store`, `RetrievalEngine`; lazy `embed_text` import
- `tests/test_retrieval.py` — Appended `TestCrystallizedHybrid` (8 tests) and `TestHybridPerformance` (1 test)
- `tests/test_user_prompt_inject.py` — Appended `TestSearchAndInjectHybrid` (5 tests) and `TestHybridPerformanceUserPrompt` (1 test)

## Decisions Made

- `query=None` preserves backward-compatible static sort — SessionStart injection has no query and must not regress on the existing 35+ injection tests
- `inject_for_session` does NOT call `embed_text` itself — the embedding responsibility belongs to the hook caller to keep Tier 2 within the 500ms budget. The vector leg is optional; when `query_embedding=None`, FTS-only hybrid runs
- `active_search` calls `embed_text` lazily (inside method body) — Tier 3 is agent-initiated so ~200-400ms embedding latency is acceptable to the user
- `project_context boost = 1/(60 + 0.5) ≈ 0.01639` — small additive bonus competitive with being ranked #1 in a single RRF leg; preserves semantic match order for strong hits
- `k=50` over-fetch in `_crystallized_hybrid` — gives the greedy token packer a wider candidate pool

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- `test_hooks.py::TestPreCompactConsolidation::test_manifest_written_after_consolidation` was failing before this plan and remains a pre-existing failure (unrelated to retrieval). Confirmed as out-of-scope per deviation rules — same as identified in Phase 07-01.

## Verification Results

- `hybrid_search` in `dir(RetrievalEngine)` → True
- `query` in `inspect.signature(RetrievalEngine.get_crystallized_for_context).parameters` → True
- `tests/test_retrieval.py tests/test_user_prompt_inject.py`: 85 passed
- Full suite: 117 passed, 1 pre-existing unrelated failure

## Next Phase Readiness

- All three retrieval tiers now use hybrid RRF when a query is available
- Phase 8 (prompt-aware injection) can now pass `query` and pre-computed embeddings to `inject_for_session` for full semantic Tier 2 injection
- The `retrieval_fn(query str) -> list[str]` callable interface from Phase 00.5 can now delegate to `active_search` or `hybrid_search` directly

---
*Phase: 07-hybrid-rrf-retrieval*
*Completed: 2026-03-29*

## Self-Check PASSED

- FOUND core/retrieval.py
- FOUND hooks/user_prompt_inject.py
- FOUND tests/test_retrieval.py
- FOUND tests/test_user_prompt_inject.py
- FOUND .planning/phases/07-hybrid-rrf-retrieval/07-02-SUMMARY.md
- Commits verified: 680c452 (test), 9345573 (feat), 5a00294 (test), 7817647 (feat)
