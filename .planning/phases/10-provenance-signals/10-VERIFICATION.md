---
phase: 10-provenance-signals
verified: 2026-03-29T23:30:00Z
status: passed
score: 4/4 must-haves verified
---

# Phase 10: Provenance Signals Verification Report

**Phase Goal:** Injected memories include human-readable provenance metadata — "established across N sessions over M weeks" — computed from session and timestamp data at injection time
**Verified:** 2026-03-29T23:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                            | Status     | Evidence                                                                                                                        |
| --- | -------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Every injected Tier 2 memory block includes a provenance line below the title    | ✓ VERIFIED | `inject_for_session` lines 129-130: `if memory.id in provenance_map: sections.append(f"*{provenance_map[memory.id]}*")` — Test 5 (test_injection_format_provenance_between_title_and_summary) PASSED |
| 2   | Multi-session memories show "Established across N sessions over M weeks" with real data | ✓ VERIFIED | `_compute_provenance_batch` queries `RetrievalLog` with `COUNT(DISTINCT session_id)` and `MIN(timestamp)` — Tests 1, 4a, 4b all PASSED |
| 3   | Single-session or zero-session memories show "First observed" with relative time | ✓ VERIFIED | `_compute_provenance_batch` falls back to `Memory.created_at` via `_relative_time()` — Tests 2 and 3 PASSED |
| 4   | Provenance is suppressed when the provenance_signals flag is disabled            | ✓ VERIFIED | Flag guard at retrieval.py line 117: `if _get_flag("provenance_signals")` → `provenance_map = {}` — Test 6 (test_flag_disabled_no_provenance) PASSED |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact                   | Expected                                                                | Status     | Details                                                                                    |
| -------------------------- | ----------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| `core/retrieval.py`        | `_compute_provenance` method on RetrievalEngine                         | ✓ VERIFIED | `_compute_provenance_batch` at line 434, `_relative_time` static helper at line 515       |
| `core/retrieval.py`        | Provenance line inserted into Tier 2 formatting in inject_for_session   | ✓ VERIFIED | Lines 116-130 show flag guard, provenance map computation, and insertion between title and summary |
| `tests/test_retrieval.py`  | Tests for provenance signal computation and injection formatting         | ✓ VERIFIED | `TestProvenanceSignals` class with 8 tests at line 1267; all 8 PASSED                    |

### Key Link Verification

| From                                         | To                          | Via                                                            | Status     | Details                                                                                         |
| -------------------------------------------- | --------------------------- | -------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------- |
| `core/retrieval.py (_compute_provenance_batch)` | `core/models.py (RetrievalLog)` | `RetrievalLog.select(...COUNT(DISTINCT)...MIN...).group_by()` | ✓ WIRED    | Line 454: `RetrievalLog.select(RetrievalLog.memory_id, fn.COUNT(...).alias("session_count"), fn.MIN(...).alias("earliest")).where(...).group_by(...)` |
| `core/retrieval.py (inject_for_session)`     | `core/retrieval.py (_compute_provenance_batch)` | provenance line inserted into Tier 2 section formatting loop  | ✓ WIRED    | Line 118: `provenance_map = self._compute_provenance_batch([m.id for m in tier2])` called inside Tier 2 block |
| `core/retrieval.py (_compute_provenance_batch)` | `core/flags.py`            | `get_flag('provenance_signals')` guard                         | ✓ WIRED    | Line 117: `if _get_flag("provenance_signals"):` — flag imported as `_get_flag` at line 116; `provenance_signals: True` confirmed in `DEFAULTS` dict in flags.py line 27 |

### Requirements Coverage

| Requirement | Source Plan   | Description                                                                   | Status      | Evidence                                                                                                |
| ----------- | ------------- | ----------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------- |
| FOUND-04    | 10-01-PLAN.md | Add provenance signals at injection time — "established across N sessions over M weeks" metadata in injection format | ✓ SATISFIED | `_compute_provenance_batch` + Tier 2 loop wiring fully implements the spec; all 8 provenance tests pass; REQUIREMENTS.md traceability table marks FOUND-04 Phase 10 as Complete |

No orphaned requirements found. REQUIREMENTS.md traceability table maps FOUND-04 exclusively to Phase 10, and the plan's `requirements` field declares exactly that ID.

### Anti-Patterns Found

None. No TODO/FIXME/HACK/PLACEHOLDER comments, no stub return patterns, no empty handlers found in either `core/retrieval.py` or `tests/test_retrieval.py`.

### Human Verification Required

None. All success criteria are programmatically verifiable and confirmed by the passing test suite.

### Test Suite Results

```
tests/test_retrieval.py::TestProvenanceSignals  8/8 PASSED
tests/test_retrieval.py (full)                 75/75 PASSED
```

No regressions in pre-existing tests.

### Implementation Notes

One minor deviation documented in the SUMMARY: the plan's end-to-end smoke script asserted `'Established across 3 sessions'` but the live system produces 4 sessions because `inject_for_session` logs the current session to `RetrievalLog` *before* computing provenance. This is correct behavior (the injecting session is real evidence). The test suite accounts for this correctly — `test_injection_format_provenance_between_title_and_summary` asserts the presence of either "Established across" or "First observed" rather than an exact count, and the unit tests for `_compute_provenance_batch` use pre-created `RetrievalLog` entries that establish the expected counts before calling the method directly.

---

_Verified: 2026-03-29T23:30:00Z_
_Verifier: Claude (gsd-verifier)_
