---
phase: 11-orientingdetector
plan: 01
subsystem: memory-quality
tags: [orienting-detector, regex, feature-flags, tdd, observation-quality]

# Dependency graph
requires:
  - phase: 10-provenance-signals
    provides: feature flag pattern (core/flags.py DEFAULTS + get_flag())
provides:
  - OrientingDetector class with detect() returning OrientingResult
  - OrientingSignal dataclass with signal_type, confidence, matched_text, importance_boost
  - OrientingResult dataclass with signals list and importance_boost (max not sum)
  - orienting_detector feature flag in core/flags.py DEFAULTS
  - 29-test suite for all four signal categories, flag guard, and edge cases
affects: [12-observation-quality, 13-consolidation-quality, 14-consolidation-wiring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Stateless detector pattern: detect(text, message_lengths=None) → result, caller manages state"
    - "Max-not-sum importance_boost: correction+emphasis gives 0.3, not 0.5, preventing over-boosting"
    - "Break-after-first-match per category: prevents double-counting when multiple patterns fire"
    - "Feature flag guard at top of detect(): early return if flag disabled"

key-files:
  created:
    - core/orienting.py
    - tests/test_orienting.py
  modified:
    - core/flags.py

key-decisions:
  - "OrientingDetector is stateless — detect() takes text + optional message_lengths, no internal state; caller manages message history"
  - "importance_boost is max across signals, not sum — prevents over-boosting when multiple categories fire simultaneously"
  - "Break after first match per signal category — no double-counting when multiple patterns match within same category"
  - "PACING_BREAK_RATIO=0.4: message must be below 40% of recent average to trigger — avoids false positives on short acknowledgements in normal flow"

patterns-established:
  - "Stateless detector pattern: detect(text, optional_context) → structured result"
  - "TDD with monkeypatch on flags_module._cache for feature flag isolation"

requirements-completed: [OBSV-01]

# Metrics
duration: 3min
completed: 2026-03-29
---

# Phase 11 Plan 01: OrientingDetector Summary

**Stateless rule-based OrientingDetector using regex pattern matching to flag corrections, emphasis, error spikes, and pacing breaks with tiered importance_boost values**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-29T23:26:59Z
- **Completed:** 2026-03-29T23:29:51Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- OrientingDetector class with four signal types: correction (0.3), emphasis (0.2), error_spike (0.2), pacing_break (0.1)
- Pure regex + arithmetic — zero LLM calls, sub-millisecond detection
- Feature flag guard wired to core/flags.py orienting_detector entry
- 29 tests covering all signal categories, importance boost values, flag guard, and edge cases (empty string, None, case insensitivity, word boundaries)
- No regressions: 618 tests passed across full suite

## Task Commits

Each task was committed atomically:

1. **Task 1: Add orienting_detector feature flag** - `06dfa5a` (feat)
2. **Task 2: TDD RED — failing tests** - `4c30bbf` (test)
3. **Task 2: TDD GREEN — implementation** - `ba50f17` (feat)

**Plan metadata:** pending (docs: complete plan)

_Note: TDD tasks have multiple commits (test → feat)_

## Files Created/Modified

- `core/orienting.py` — OrientingDetector, OrientingResult, OrientingSignal classes; 195 lines
- `tests/test_orienting.py` — 29 unit tests in TestOrientingDetector class; 230 lines
- `core/flags.py` — Added `"orienting_detector": True` to DEFAULTS dict

## Decisions Made

- **Stateless design:** detect() takes text + optional message_lengths, no internal state. Callers are responsible for tracking recent message lengths for pacing break detection. Avoids coupling the detector to session lifecycle.
- **Max not sum for importance_boost:** A message with both correction and emphasis gets boost=0.3 (the correction boost), not 0.5. This keeps the importance score within a predictable range even when multiple orienting signals fire at once.
- **Break after first match per category:** Once a correction pattern matches, no further correction patterns are checked. Prevents over-counting when a single message has both "actually" and "I said".
- **PACING_BREAK_RATIO=0.4:** Message must be below 40% of recent average to trigger. A message of 190 chars in a 200-char-average conversation won't fire; only abrupt gear-shifts like a 15-char reply in a 200-char flow will.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- OrientingDetector is ready to be wired into the observation ingestion pipeline (Phase 14)
- detect() signature is stable: detect(text: str | None, message_lengths: list[int] | None = None) → OrientingResult
- Importance boost values are calibrated: 0.3 correction, 0.2 emphasis/error_spike, 0.1 pacing_break
- All downstream phases (12-14) can import from core.orienting

---
*Phase: 11-orientingdetector*
*Completed: 2026-03-29*

## Self-Check: PASSED

- core/orienting.py — FOUND
- tests/test_orienting.py — FOUND
- 11-01-SUMMARY.md — FOUND
- Commit 06dfa5a (feat flag) — FOUND
- Commit 4c30bbf (test RED) — FOUND
- Commit ba50f17 (feat GREEN) — FOUND
