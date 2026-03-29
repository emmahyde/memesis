---
phase: 08-prompt-aware-tier-2-injection
plan: "01"
subsystem: hooks/retrieval
tags: [tier-2, crystallized, hybrid-rrf, user-prompt-inject, tdd]
dependency_graph:
  requires:
    - 07-hybrid-rrf-retrieval/07-02-PLAN.md
  provides:
    - Tier 2 crystallized retrieval wired into UserPromptSubmit hook
  affects:
    - hooks/user_prompt_inject.py
    - tests/test_user_prompt_inject.py
tech_stack:
  added: []
  patterns:
    - Embedding computed once in hook, passed to both Tier 2 and Tier 3 calls
    - Tier 2 crystallized memories prioritised before Tier 3 JIT in merged result
key_files:
  created: []
  modified:
    - hooks/user_prompt_inject.py
    - tests/test_user_prompt_inject.py
decisions:
  - search_and_inject computes embedding once and passes it to both get_crystallized_for_context and hybrid_search — no extra latency
  - Tier 2 results fill slots first; Tier 3 JIT candidates must not duplicate Tier 2 IDs or already_injected IDs
  - token_limit=TOKEN_BUDGET_CHARS passed to get_crystallized_for_context so it respects the 2000-char hook budget (not the engine default)
metrics:
  duration: 2 minutes
  completed: 2026-03-29
  tasks_completed: 1
  files_modified: 2
---

# Phase 08 Plan 01: Prompt-Aware Tier 2 Injection Summary

Wired UserPromptSubmit hook to call Tier 2 crystallized retrieval (`get_crystallized_for_context`) with prompt-derived query and embedding, so crystallized memories matching the user's prompt surface via the project-context-boosted, token-budgeted hybrid RRF path rather than only through the all-stage JIT search.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 (RED) | Add failing Tier 2 tests | a70950c | tests/test_user_prompt_inject.py |
| 1 (GREEN) | Wire Tier 2 into search_and_inject | 6ced4a2 | hooks/user_prompt_inject.py |

## What Was Built

`search_and_inject()` in `hooks/user_prompt_inject.py` now calls two retrieval paths:

1. **Tier 2** — `engine.get_crystallized_for_context(query=fts_query, query_embedding=query_embedding, project_context=project_context, token_limit=TOKEN_BUDGET_CHARS)` — crystallized-only, project_context-boosted, token-budgeted hybrid RRF.
2. **Tier 3 JIT** — `engine.hybrid_search(...)` across all stages — supplements Tier 2 with non-crystallized memories.

The embedding is computed once before both calls and reused; no extra `embed_text` call is added.

Merge order: Tier 2 results come first (higher priority), Tier 3 JIT fills remaining slots. Deduplication excludes Tier 2 IDs from Tier 3 candidates and skips already-injected memories in both.

`TestTier2PromptInjection` (5 new tests) was added to `tests/test_user_prompt_inject.py`:
- Spy confirms `get_crystallized_for_context` is called with prompt-derived query and embedding
- Crystallized memories surface when they match the prompt
- Already-injected memories are deduplicated
- FTS query passes through when embedding fails (query_embedding=None fallback)
- 500ms latency budget test with 1000-memory corpus

## Verification Results

```
tests/test_user_prompt_inject.py — 30 passed (25 existing + 5 new Tier 2)
tests/test_retrieval.py — 60 passed
tests/ full suite — 574 passed, 7 skipped
rg "get_crystallized_for_context" hooks/user_prompt_inject.py — confirmed
```

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- [x] `hooks/user_prompt_inject.py` exists and contains `get_crystallized_for_context` call
- [x] `tests/test_user_prompt_inject.py` exists and contains `TestTier2PromptInjection` class
- [x] Commit a70950c exists (RED tests)
- [x] Commit 6ced4a2 exists (GREEN implementation)
- [x] 574 tests pass, 0 regressions
