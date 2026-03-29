---
phase: 09-thompson-sampling-selection
plan: "01"
subsystem: retrieval
tags: [thompson-sampling, beta-distribution, explore-exploit, feature-flags, retrieval, tdd]

# Dependency graph
requires:
  - phase: 07-hybrid-rrf-retrieval
    provides: RetrievalEngine with _crystallized_hybrid and get_crystallized_for_context
  - phase: 08-prompt-aware-tier-2-injection
    provides: query parameter wiring into Tier 2 retrieval
provides:
  - _thompson_rerank method on RetrievalEngine using random.betavariate
  - Thompson sampling wired into static retrieval path (after three-pass sort)
  - Thompson sampling wired into hybrid retrieval path (after RRF+project boost sort)
  - Feature flag guard on both paths via get_flag("thompson_sampling")
affects:
  - future retrieval quality phases
  - any phase that tests deterministic memory ordering in static path

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Thompson sampling over Beta(usage+1, unused+1) for explore/exploit memory selection"
    - "Monkeypatch _cache on core.flags for deterministic test isolation"
    - "b=max(injection_count-usage_count,0)+1 guard for data anomaly handling"

key-files:
  created: []
  modified:
    - core/retrieval.py
    - tests/test_retrieval.py

key-decisions:
  - "Thompson sampling re-orders the ranked list (RRF or static) — does not replace ranking, adds stochastic exploration on top"
  - "Existing tests testing deterministic sort order patched with thompson_sampling=False to remain valid and isolated"
  - "b parameter uses max(0,...)+1 guard so usage_count > injection_count anomaly gives b=1 not negative"

patterns-established:
  - "Monkeypatch flags_module._cache = {...} for feature flag test isolation in retrieval tests"

requirements-completed: [FOUND-03]

# Metrics
duration: 12min
completed: 2026-03-29
---

# Phase 09 Plan 01: Thompson Sampling Selection Summary

**Thompson sampling re-ranking via Beta(usage+1, unused+1) wired into both hybrid and static retrieval paths, guarded by the `thompson_sampling` feature flag**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-29T22:50:00Z
- **Completed:** 2026-03-29T23:02:00Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- `_thompson_rerank` method added to RetrievalEngine: samples Beta(a,b) for each memory, sorts by score descending
- Static path in `get_crystallized_for_context` calls `_thompson_rerank` after three-pass sort, before token budget packing
- Hybrid path in `_crystallized_hybrid` calls `_thompson_rerank` after RRF+project boost sort, before token budget packing
- Both paths guarded by `get_flag("thompson_sampling")` — flag=False preserves deterministic ordering
- 7 new tests in `TestThompsonSampling` covering: cold start, deterministic seed, statistical win rate, flag on/off in both paths, data anomaly guard
- 6 existing tests updated to patch `thompson_sampling=False` for deterministic ordering isolation

## Task Commits

1. **RED: Failing tests** - `edf7ddb` (test)
2. **GREEN: Implementation + regression fixes** - `cc2afff` (feat)

## Files Created/Modified

- `/Users/emma.hyde/projects/memesis/core/retrieval.py` - Added `_thompson_rerank` method; wired into static and hybrid retrieval paths with feature flag guard
- `/Users/emma.hyde/projects/memesis/tests/test_retrieval.py` - Added `TestThompsonSampling` class (7 tests); patched 6 existing deterministic-ordering tests to disable Thompson sampling

## Decisions Made

- Thompson sampling re-orders the ranked list — it does not replace ranking. The ranked order (RRF or static sort) is the input; Thompson sampling adds stochastic exploration on top.
- Existing tests that assert strict deterministic order are patched with `thompson_sampling=False` via `monkeypatch.setattr(flags_module, "_cache", {...})`. This keeps those tests valid as regression guards for the deterministic code path, while the new `TestThompsonSampling` tests verify the probabilistic behavior.
- b parameter uses `max(injection_count - usage_count, 0) + 1` to handle data anomalies where usage_count exceeds injection_count — gives b=1 (Beta prior of uniform) rather than an invalid negative value.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed 6 existing tests broken by Thompson sampling default-on**
- **Found during:** GREEN phase verification
- **Issue:** 6 existing tests assumed deterministic sort order (importance DESC, project context boost order). Thompson sampling is on by default and randomizes the final order, causing these tests to fail ~50% of the time.
- **Fix:** Added `monkeypatch.setattr(flags_module, "_cache", {"hybrid_rrf": ..., "thompson_sampling": False})` to each affected test. Tests now correctly isolate the deterministic code path.
- **Files modified:** tests/test_retrieval.py
- **Verification:** All 581 tests pass, 7 skipped (pre-existing)
- **Committed in:** cc2afff (GREEN phase commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - deterministic test isolation)
**Impact on plan:** Necessary correctness fix — the tests were testing a valid code path that still exists when the flag is off. No scope creep.

## Issues Encountered

None beyond the regression fix documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- FOUND-03 satisfied: Thompson sampling is wired and operational
- Feature flag `thompson_sampling` in `flags.py` DEFAULTS already set to `True`
- Both retrieval paths (hybrid and static) call `_thompson_rerank` before token budget packing
- Ready for Phase 10+ which may build on exploration/provenance signals

---
*Phase: 09-thompson-sampling-selection*
*Completed: 2026-03-29*
