# Plan: Relationship Engine — Phases 2 & 3 (minus Temporal Echoes)

**Source:** .context/CONTEXT-relationship-engine.md
**Generated:** 2026-03-29
**Status:** Ready for execution

## Overview

Implement Contradiction Tensors, Affect Signatures, and Adversarial Surfacing across 4 waves: foundation (flags + models + affect accessor), contradiction edge creation, affect coloring + retrieval tiers, and adversarial surfacing.

**Waves:** 4
**Total tasks:** 8

---

## Wave 1: Foundation — Flags, Affect Accessor, Relevance Integration

**Prerequisite:** none

### Task 1.1: Feature flags and relevance integration for contradiction edges

- **Summary:** Register three new feature flags and add `_has_contradiction_edges()` to the relevance engine's integration factor check.
- **Files owned:**
  - `core/flags.py`
  - `core/relevance.py`
  - `tests/test_causal_edges.py` (append new test class `TestRelevanceContradictionIntegration`)
- **Depends on:** none
- **Decisions:** D-01 (contradiction edges exist), D-02 (active tensions), D-06 (build order)
- **Acceptance criteria:**
  - [ ] `core/flags.py` DEFAULTS dict contains `contradiction_tensors`, `affect_signatures`, and `adversarial_surfacing`, all defaulting to `True`
  - [ ] `core/relevance.py` has a `_has_contradiction_edges()` static method that checks for `contradicts` edge type on a memory (both as source and target)
  - [ ] `compute_relevance()` calls `_has_contradiction_edges()` when `get_flag("contradiction_tensors")` is True, and includes it in the `connected` boolean alongside `has_causal`
  - [ ] Tests verify: flag exists, contradiction edge prevents isolation penalty, flag-disabled skips check
  - [ ] `pytest tests/test_causal_edges.py -x` passes

### Task 1.2: Affect state accessor and PreCompact bridge

- **Summary:** Add `current_state()` method to `InteractionAnalyzer` and wire session affect loading into `pre_compact.py`.
- **Files owned:**
  - `core/affect.py`
  - `hooks/pre_compact.py`
  - `tests/test_affect.py` (append new test class `TestCurrentState`)
- **Depends on:** none
- **Decisions:** D-05 (affect guard depends on affect state being available), D-06 (affect before adversarial)
- **Acceptance criteria:**
  - [ ] `InteractionAnalyzer.current_state()` returns an `AffectState` computed from internal state without requiring a new message
  - [ ] `pre_compact.py` loads session affect via `load_analyzer(base_dir, session_id)` and calls `current_state()` to get `session_affect`
  - [ ] `session_affect` is available in scope before reconsolidation and thread-building calls (it will be passed to them in later waves)
  - [ ] The `logger` reference in `pre_compact.py` (lines 123, 128) is fixed — either import logging and create a logger, or switch to `print(..., file=sys.stderr)` to match the rest of the file
  - [ ] `pytest tests/test_affect.py -x` passes

---

**Wave 1 status:** pending

---

## Wave 2: Contradiction Edge Creation

**Prerequisite:** Wave 1 (flags registered, relevance integration ready)

### Task 2.1: Contradiction edges from reconsolidation

- **Summary:** Create bidirectional `contradicts` edges in `reconsolidation.py` when contradicted memories are found, following the `_create_causal_edges()` pattern.
- **Files owned:**
  - `core/reconsolidation.py`
  - `tests/test_causal_edges.py` (append new test class `TestContradictionEdges`)
- **Depends on:** Task 1.1 (flag registered)
- **Decisions:** D-01 (create for all resolution types; superseded = resolved:true, scoped/coexist = resolved:false), D-02 (resolved field on metadata), D-03 (thread edges always resolved:true)
- **Acceptance criteria:**
  - [ ] New function `_create_contradiction_edges()` in `reconsolidation.py` creates bidirectional `contradicts` edges (A->B and B->A) between contradicted and confirmed memories
  - [ ] Edge metadata includes: `evidence`, `session_id`, `created_at`, `resolved` (bool), `resolution` (string or null)
  - [ ] Default weight is 0.7 for reconsolidation-sourced contradictions
  - [ ] Duplicate edge check prevents creating the same (source, target, edge_type) twice
  - [ ] Gated behind `get_flag("contradiction_tensors")`
  - [ ] Called after `_create_causal_edges()` in the `reconsolidate()` function
  - [ ] Tests: creates bidirectional edges, metadata has resolved field, no duplicates, flag-disabled creates no edges
  - [ ] `pytest tests/test_causal_edges.py -x` passes

### Task 2.2: Contradiction edges from thread narration

- **Summary:** After `build_threads()` creates a `correction_chain` thread, create resolved contradiction edges between early and late members.
- **Files owned:**
  - `core/threads.py`
  - `tests/test_causal_edges.py` (append new test class `TestThreadContradictionEdges`)
- **Depends on:** Task 1.1 (flag registered)
- **Decisions:** D-03 (weight 0.3, always resolved:true, resolution references thread), D-01 (contradiction edges for all types)
- **Acceptance criteria:**
  - [ ] `build_threads()` — after creating a thread with `arc_type == "correction_chain"`, creates `contradicts` edges between early members (low position) and late members (high position)
  - [ ] "Early" vs "late" split: use median position as the split point (Claude's discretion per CONTEXT doc)
  - [ ] Edge metadata includes: `thread_id`, `arc_type`, `resolved: true`, `resolution: "correction_chain"`, `created_at`
  - [ ] Weight is 0.3 (historical, not active tension)
  - [ ] Gated behind `get_flag("contradiction_tensors")`
  - [ ] Tests: correction_chain thread creates resolved edges, non-correction_chain thread creates no edges, early/late split is correct
  - [ ] `pytest tests/test_causal_edges.py -x` passes

---

**Wave 2 status:** pending

---

## Wave 3: Affect Coloring and Retrieval Tiers

**Prerequisite:** Wave 2 (contradiction edges exist to query)

### Task 3.1: Affect coloring on edges and threads

- **Summary:** Attach session affect metadata to reconsolidation edges and compute `arc_affect` trajectories for threads.
- **Files owned:**
  - `core/reconsolidation.py` (extend — accept `session_affect` param, include in edge metadata)
  - `core/threads.py` (extend — add `_compute_arc_affect()`, set `arc_affect` on threads)
  - `hooks/pre_compact.py` (extend — pass `session_affect` to reconsolidate call)
  - `tests/test_causal_edges.py` (append new test classes `TestAffectOnEdges`, `TestArcAffect`)
- **Depends on:** Task 1.2 (affect state available in pre_compact), Task 2.1 (reconsolidation edges exist), Task 2.2 (thread edges exist)
- **Decisions:** D-05 (affect is used for adversarial gating), D-06 (affect before adversarial)
- **Acceptance criteria:**
  - [ ] `reconsolidate()` accepts an optional `session_affect: dict | None` parameter
  - [ ] When `session_affect` is provided and `get_flag("affect_signatures")` is True, edge metadata includes an `affect` key with `frustration`, `momentum`, `dominant_valence`
  - [ ] `_create_causal_edges()` and `_create_contradiction_edges()` both receive and include affect in metadata
  - [ ] `pre_compact.py` passes `session_affect` dict (from `AffectState` fields) to `reconsolidate()`
  - [ ] `_compute_arc_affect(valences, arc_type)` in `threads.py` detects trajectories: `frustration_to_mastery`, `frustration_to_resolution`, `curiosity_to_mastery`, `discovery`, `sustained_struggle`
  - [ ] `build_threads()` calls `_compute_arc_affect()` after narrating each cluster and stores result in `NarrativeThread.arc_affect` as JSON
  - [ ] `_compute_arc_affect` gated behind `get_flag("affect_signatures")`
  - [ ] Tests verify affect metadata on edges, trajectory detection for each type, arc_affect stored on threads
  - [ ] `pytest tests/test_causal_edges.py -x` passes

### Task 3.2: Active Tensions tier (2.6) and affect-aware thread ordering

- **Summary:** Add Tier 2.6 (Active Tensions) to retrieval and implement affect-aware thread ordering in Tier 2.5.
- **Files owned:**
  - `core/retrieval.py`
  - `tests/test_retrieval.py` (append new test classes `TestActiveTensions`, `TestAffectAwareThreadOrdering`)
- **Depends on:** Task 2.1 (contradiction edges to query), Task 2.2 (thread edges to query), Task 3.1 (arc_affect on threads for ordering)
- **Decisions:** D-02 (only unresolved contradictions in Tier 2.6)
- **Acceptance criteria:**
  - [ ] New method `_get_active_tensions(tier2_memories)` queries `MemoryEdge` for unresolved `contradicts` edges where source or target is in the injected set
  - [ ] Returns formatted tension blocks with both positions and their context
  - [ ] `TENSION_BUDGET_CHARS = 2000` constant; greedy budget packing
  - [ ] Gated behind `get_flag("contradiction_tensors")`
  - [ ] Active Tensions section appears in `inject_for_session()` output after Tier 2.5 (thread narratives), with header `## Active Tensions (conflicting memories -- context determines which applies)`
  - [ ] `_get_thread_narratives()` accepts optional `session_affect` parameter; when frustration > 0.3 and `get_flag("affect_signatures")`, prioritizes `frustration_to_mastery` threads and deprioritizes `sustained_struggle` threads
  - [ ] Tests: tensions surface only unresolved edges, budget is respected, affect-aware ordering changes thread priority
  - [ ] `pytest tests/test_retrieval.py -x` passes

---

**Wave 3 status:** pending

---

## Wave 4: Adversarial Surfacing

**Prerequisite:** Wave 3 (Tier 2.6 exists, affect state available in retrieval)

### Task 4.1: Adversarial memory selection and Thompson sampling

- **Summary:** Implement adversarial surfacing as Tier 2.8 in retrieval — Thompson-sampled, affect-gated counterpoint injection.
- **Files owned:**
  - `core/retrieval.py` (extend — `_get_adversarial_memory()`, `_adversarial_thompson_draw()`, Tier 2.8 section)
  - `tests/test_retrieval.py` (append new test classes `TestAdversarialSurfacing`, `TestAdversarialThompsonSampling`)
- **Depends on:** Task 3.2 (retrieval tiers in place, session_affect plumbed)
- **Decisions:** D-04 (Beta(1,3) prior -- cautious), D-05 (affect guard: no challenges when frustration > 0.4)
- **Acceptance criteria:**
  - [ ] `_get_adversarial_memory(tier2_memories, session_affect)` finds counterpoints via: (1) unresolved contradicts edges to injected memories, (2) early members of correction_chain threads where injected memory is a late member
  - [ ] Selects at most 1 adversarial memory within `ADVERSARIAL_BUDGET_CHARS = 500`
  - [ ] Framed with header `### Counterpoint` in output
  - [ ] `_adversarial_thompson_draw()` reads/writes `meta/adversarial-sampling.json`, uses Beta(alpha, beta) with initial prior Beta(1,3)
  - [ ] Three gates: `get_flag("adversarial_surfacing")`, Thompson sampling draw, `session_affect.frustration <= 0.4`
  - [ ] Adversarial injections logged with `retrieval_type='adversarial'` in RetrievalLog
  - [ ] Tier 2.8 section appears in `inject_for_session()` after Tier 2.6
  - [ ] Tests: adversarial memory found via contradiction edges, found via correction_chain, blocked by frustration gate, blocked by Thompson draw, logged correctly
  - [ ] `pytest tests/test_retrieval.py -x` passes

### Task 4.2: Adversarial feedback loop — Thompson state updates

- **Summary:** Extend feedback tracking to detect adversarial memory usage and update Thompson sampling state.
- **Files owned:**
  - `core/feedback.py`
  - `tests/test_feedback.py` (append new test class `TestAdversarialFeedback`)
- **Depends on:** Task 4.1 (adversarial injections logged with retrieval_type='adversarial')
- **Decisions:** D-04 (Beta(1,3) prior), D-05 (double-gated)
- **Acceptance criteria:**
  - [ ] `track_usage()` detects adversarial injections by checking `retrieval_type='adversarial'` in RetrievalLog for the session
  - [ ] When an adversarial memory is used: increment `alpha` (successes) in `meta/adversarial-sampling.json`
  - [ ] When an adversarial memory is ignored: increment `beta` (failures) in `meta/adversarial-sampling.json`
  - [ ] When adversarial memories are consistently engaged (alpha / (alpha + beta) > 0.5 after 5+ trials), update the associated thread's `updated_at` to flag for re-narration
  - [ ] Tests: Thompson state file created, alpha incremented on use, beta incremented on ignore, thread flagged when engagement is high
  - [ ] `pytest tests/test_feedback.py -x` passes

---

**Wave 4 status:** pending

---

## File Ownership Map

| File | Wave 1 | Wave 2 | Wave 3 | Wave 4 |
|------|--------|--------|--------|--------|
| `core/flags.py` | 1.1 | | | |
| `core/relevance.py` | 1.1 | | | |
| `core/affect.py` | 1.2 | | | |
| `hooks/pre_compact.py` | 1.2 | | 3.1 | |
| `core/reconsolidation.py` | | 2.1 | 3.1 | |
| `core/threads.py` | | 2.2 | 3.1 | |
| `core/retrieval.py` | | | 3.2 | 4.1 |
| `core/feedback.py` | | | | 4.2 |
| `tests/test_causal_edges.py` | 1.1 | 2.1, 2.2 | 3.1 | |
| `tests/test_affect.py` | 1.2 | | | |
| `tests/test_retrieval.py` | | | 3.2 | 4.1 |
| `tests/test_feedback.py` | | | | 4.2 |

## Cross-Wave Ownership Handoffs

| File | Earlier Owner | Later Owner | Handoff Notes |
|------|--------------|-------------|---------------|
| `core/reconsolidation.py` | 2.1 (contradiction edges) | 3.1 (affect coloring) | 3.1 adds `session_affect` param to `reconsolidate()` and threads it into both `_create_causal_edges()` and `_create_contradiction_edges()`. Must preserve 2.1's contradiction edge logic. |
| `core/threads.py` | 2.2 (thread contradiction edges) | 3.1 (arc_affect computation) | 3.1 adds `_compute_arc_affect()` and wires it into `build_threads()`. Must preserve 2.2's correction_chain edge creation in `build_threads()`. |
| `hooks/pre_compact.py` | 1.2 (load session affect) | 3.1 (pass affect to reconsolidate) | 3.1 passes the `session_affect` dict loaded by 1.2 as a new kwarg to `reconsolidate()`. Must preserve 1.2's analyzer loading logic. |
| `core/retrieval.py` | 3.2 (Tier 2.6 + affect ordering) | 4.1 (Tier 2.8 adversarial) | 4.1 adds `_get_adversarial_memory()` and Tier 2.8 section in `inject_for_session()` after 3.2's Tier 2.6. Must preserve 3.2's active tensions and affect-aware ordering. |
| `tests/test_causal_edges.py` | 1.1 (relevance tests) | 2.1/2.2 (edge creation tests) then 3.1 (affect tests) | Each wave appends new test classes. Later waves must not modify earlier classes. The file grows additively. |
| `tests/test_retrieval.py` | 3.2 (tension + affect tests) | 4.1 (adversarial tests) | 4.1 appends new test classes. Must not modify 3.2's classes. |

## Decision Traceability

| Decision | Tasks |
|----------|-------|
| D-01: Create contradiction edges for all resolution types | 2.1, 2.2 |
| D-02: Active Tensions tier surfaces only unresolved contradictions | 3.2 |
| D-03: Thread narration contradiction edges are resolved:true, weight 0.3 | 2.2 |
| D-04: Thompson sampling prior Beta(1,3) | 4.1, 4.2 |
| D-05: Double-gated: Thompson + affect guard (frustration > 0.4) | 4.1, 1.2 |
| D-06: Build order: Contradiction Tensors -> Affect Signatures -> Adversarial Surfacing | All waves (wave structure follows this order) |
