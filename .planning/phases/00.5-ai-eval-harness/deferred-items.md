# Deferred Items — Phase 00.5-ai-eval-harness

## Out-of-scope issues discovered during execution

### [Discovered in 00.5-02] staleness_test.py missing skip guard

**File:** eval/staleness_test.py
**Test:** test_single_memory_update_v1_to_v2 (line ~192)
**Issue:** Calls `MemoryStore(base_dir=...)` directly without checking `_CORE_STORAGE_AVAILABLE`, causing `TypeError: NoneType is not callable` when core.storage is unavailable.
**Other tests** in staleness_test.py use the `seeded_store` fixture which skips properly — this one test bypasses the fixture.
**Fix needed:** Add `if not _CORE_STORAGE_AVAILABLE: pytest.skip(...)` guard at the top of the test function.
**Discovered during:** Task 1 (run_fixture full eval suite run)
**Status:** Deferred — pre-existing, unrelated to this plan's changes
