---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: planning
stopped_at: Completed 11-orientingdetector-01-PLAN.md
last_updated: "2026-03-29T23:33:00.787Z"
last_activity: 2026-03-29 — Phases 1-6 (cleanup) complete, gold set eval wired into report
progress:
  total_phases: 27
  completed_phases: 5
  total_plans: 6
  completed_plans: 6
  percent: 33
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-29)

**Core value:** When a memory is injected, it should feel like recognition — not lookup.
**Current focus:** Phase 7 — Hybrid RRF Retrieval

## Current Position

Phase: 7 of 20 (Hybrid RRF Retrieval)
Plan: 0 of ? in current phase
Status: Ready to plan
Last activity: 2026-03-29 — Phases 1-6 (cleanup) complete, gold set eval wired into report

Progress: [███░░░░░░░] 33%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 00.5-ai-eval-harness P01 | 5 | 2 tasks | 8 files |
| Phase 00.5-ai-eval-harness P02 | 2 | 1 tasks | 2 files |
| Phase 00.5-ai-eval-harness P03 | 2 | 2 tasks | 3 files |
| Phase 07-hybrid-rrf-retrieval P01 | 15 | 1 tasks | 2 files |
| Phase 07-hybrid-rrf-retrieval P02 | 5 | 2 tasks | 4 files |
| Phase 08-prompt-aware-tier-2-injection P01 | 2 | 1 tasks | 2 files |
| Phase 09-thompson-sampling-selection P01 | 12 | 1 tasks | 2 files |
| Phase 10-provenance-signals P01 | 3 | 1 tasks | 2 files |
| Phase 11-orientingdetector P01 | 3 | 2 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Pre-Phase 1]: Peewee ORM over raw sqlite3 — legibility, convention over configuration (pending commit)
- [Pre-Phase 1]: Hybrid RRF over pure vector search — FTS catches exact terms, vec catches semantics (pending impl)
- [Pre-Phase 1]: Thompson sampling over UCB1 — handles cold-start via Beta prior, no hyperparameters, stdlib-only
- [Phase 00.5-ai-eval-harness]: prune_accuracy precision denominator is |kept| (not |true_keep|) - measures quality of kept set
- [Phase 00.5-ai-eval-harness]: conditional import guard pattern with _CORE_STORAGE_AVAILABLE flag established for all eval/ files
- [Phase 00.5-ai-eval-harness]: Case-insensitive substring match for LongMemEval answer scoring - matches benchmark loose evaluation protocol
- [Phase 00.5-ai-eval-harness]: Retrieval callable interface pattern established: retrieval_fn(query str) -> list[str] for deferred Phase 7 wiring
- [Phase 00.5-ai-eval-harness]: Baseline captured with all-zero metrics (captured_without_core_storage: true) - correct starting point before Phase 1 ORM migration
- [Phase 00.5-ai-eval-harness]: verify_phase.py uses tempfile for current snapshot to avoid overwriting stored baseline during verification runs
- [Phase 00.5-ai-eval-harness]: Regression threshold 0.05 - minor drops tolerated, meaningful regressions caught
- [Phase 07-hybrid-rrf-retrieval]: RRF uses position-based rank, not raw BM25/distance scores
- [Phase 07-hybrid-rrf-retrieval]: VecStore accepted as parameter to avoid circular imports - keeps hybrid_search independently testable
- [Phase 07-hybrid-rrf-retrieval]: Fallback to FTS-only when vec_store is None, unavailable, or query_embedding is None
- [Phase 07-hybrid-rrf-retrieval]: query=None preserves backward-compatible static sort in get_crystallized_for_context
- [Phase 07-hybrid-rrf-retrieval]: inject_for_session does not call embed_text - embedding responsibility belongs to the hook caller for 500ms budget
- [Phase 07-hybrid-rrf-retrieval]: active_search calls embed_text lazily - Tier 3 latency acceptable, Tier 2 is not
- [Phase 07-hybrid-rrf-retrieval]: project_context boost = 1/(60+0.5) added to RRF score - keeps local memories competitive without overriding strong semantic matches
- [Phase 08-prompt-aware-tier-2-injection]: Embedding computed once in search_and_inject, reused by both Tier 2 get_crystallized_for_context and Tier 3 hybrid_search — no extra latency
- [Phase 08-prompt-aware-tier-2-injection]: Tier 2 results prioritised before Tier 3 JIT in merged candidates; Tier 2 IDs excluded from Tier 3 to prevent duplication
- [Phase 08-prompt-aware-tier-2-injection]: token_limit=TOKEN_BUDGET_CHARS (2000) passed to get_crystallized_for_context overriding engine default to respect hook budget
- [Phase 09-thompson-sampling-selection]: Thompson sampling re-orders ranked list, does not replace ranking - exploration layered on top of RRF or static sort
- [Phase 09-thompson-sampling-selection]: Existing deterministic-order tests patched with thompson_sampling=False via monkeypatch to isolate static code path
- [Phase 10-provenance-signals]: Provenance computed after injection logging so current session is included in session count - reflects actual retrieval history
- [Phase 10-provenance-signals]: Batch query uses peewee fn.COUNT(field.distinct()) + fn.MIN in single SELECT GROUP BY - avoids N+1 queries for provenance computation
- [Phase 10-provenance-signals]: Memory.created_at used as fallback for single/zero-session memories; week phrase is floor(days/7) with 'over less than a week' when 0
- [Phase 11-orientingdetector]: OrientingDetector is stateless - detect() takes text + optional message_lengths, no internal state - caller manages message history
- [Phase 11-orientingdetector]: importance_boost is max across signals not sum - prevents over-boosting when multiple categories fire simultaneously

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 1 (CLEAN-01) is the gate for phases 2-6: all cleanup phases depend on the ORM migration being committed cleanly first.
- Phase 7 (Hybrid RRF) is the gate for phases 8-10: all foundation phases require the retrieval layer to exist.
- Phases 11-14 (Observation Quality) are independent of phases 7-10 and can run in parallel if desired.

## Session Continuity

Last session: 2026-03-29T23:31:07.398Z
Stopped at: Completed 11-orientingdetector-01-PLAN.md
Resume file: None
