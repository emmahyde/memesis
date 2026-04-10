# Context: Relationship Engine — Phases 2 & 3 (minus Temporal Echoes)

**Gathered:** 2026-03-29
**Status:** Ready for implementation
**Codebase map:** .context/codebase/ (mapped 2026-03-29)

<domain>
## Scope

Implement three of the four remaining relationship engine features from `docs/relationship-engine-spec.md`:

1. **Contradiction Tensors** (Phase 2) — Persist bidirectional `contradicts` edges during reconsolidation and thread narration. Surface unresolved tensions in retrieval as Tier 2.6.
2. **Affect Signatures** (Phase 3) — Attach session-level affect to edges and threads. Compute arc trajectories. Affect-aware thread ordering in retrieval.
3. **Adversarial Surfacing** (Phase 3) — Retrieval-time computation that surfaces a single counterpoint memory using contradiction edges and correction_chain thread members. Thompson-sampled, affect-gated.

**Excluded from scope:** Temporal Echoes (Phase 2). The `echo` edge type, `echo_count` column, `detect_echoes()`, Tier 2.7, and echo-based instinctive promotion are all out. The `echo_count` column and schema migration already exist from Phase 1 (added proactively) — leave them in place but unused.
</domain>

<decisions>
## Implementation Decisions

### Contradiction edge lifecycle

- **D-01:** Create contradiction edges for ALL resolution types (superseded, scoped, coexist). For superseded contradictions, immediately mark the edge as `resolved: true` with `resolution: "superseded"`. For scoped/coexist, leave `resolved: false` — these are active tensions.
- **D-02:** Active Tensions tier (2.6) surfaces ONLY unresolved contradictions (`metadata.resolved == false`). Resolved edges exist as historical record for graph traversal but don't consume injection budget.
- **D-03:** Contradiction edges from thread narration (correction_chain arc_type) are always `resolved: true` with weight 0.3 — these are historical, not active tensions.

### Adversarial surfacing personality

- **D-04:** Initial Thompson sampling prior is **Beta(1,3)** — cautious. ~25% initial sample rate. The system earns the right to challenge by proving counterpoints are engaged with.
- **D-05:** Combined with the affect guard (no challenges when frustration > 0.4), this means adversarial surfacing is double-gated: probabilistic restraint AND emotional awareness.

### Build order

- **D-06:** Contradiction Tensors first (foundation for adversarial candidates). Then Affect Signatures (needed for adversarial gating). Then Adversarial Surfacing (depends on both).

### Claude's Discretion

- Thread narration "early members" vs "late members" split point for correction_chain contradiction edges — Claude should determine the most reasonable heuristic during implementation.
- Exact affect trajectory detection thresholds in `_compute_arc_affect()` — the spec lists the trajectory types but not the exact ratio logic.
- How `inject_for_session()` loads session affect state — the spec sketches loading from `.affect-{session_id}.json` but the exact wiring is implementation detail.
</decisions>

<canonical_refs>

## Canonical References

**Downstream agents MUST read these before implementing.**

### Specification

- `docs/relationship-engine-spec.md` — Full technical specification for all five features. Phase 1 section documents implemented patterns to follow. Phase 2 Contradiction Tensors, Phase 3 Affect Signatures, and Phase 3 Adversarial Surfacing sections are the implementation targets.

### Prior context decisions

- `.context/CONTEXT-contradiction-resolution.md` — Existing contradiction resolution branches (superseded→archive, scoped→scope tag, coexist→update). New edge creation hooks into these paths.
- `.context/CONTEXT-thread-retrieval.md` — Thread retrieval wiring (batch query, 8K budget, membership-based injection). New Tier 2.6 follows the same injection pattern.

### Core implementation files

- `core/reconsolidation.py` — Phase 1 causal edge creation (`_create_causal_edges`, `_rank_by_similarity`). New contradiction edge creation follows the same structure.
- `core/graph.py` — `compute_edges()` preservation logic, `expand_neighbors()` priority tiers. New edge types slot into existing priority table.
- `core/retrieval.py` — `inject_for_session()`, `_get_thread_narratives()`. New tiers 2.6 and 2.8 added here.
- `core/affect.py` — `InteractionAnalyzer`, `AffectState`. Needs `current_state()` method for PreCompact bridge.
- `core/threads.py` — `ThreadDetector`, `ThreadNarrator`, `build_threads()`. Correction_chain detection piggybacks here.
- `core/flags.py` — Feature flag registry. Three new flags needed.
- `core/models.py` — `MemoryEdge`, `RECOMPUTABLE_TYPES`. `contradicts` is NOT recomputable.
- `core/relevance.py` — `integration_factor` logic. Contradiction edges count as integration.
- `hooks/pre_compact.py` — Pipeline orchestration. Session affect loading added here.

### Testing

- `tests/test_causal_edges.py` — 24 tests covering Phase 1. Pattern to follow for new edge types.
- `tests/test_retrieval.py` — Retrieval injection tests.
- `tests/test_consolidator.py` — `TestContradictionResolution` class. New edge creation tests extend this.

</canonical_refs>

<code_context>

## Codebase Insights

### Reusable Assets

- `reconsolidation._create_causal_edges()` — Pattern for edge creation with duplicate checking, similarity ranking, metadata attachment. Contradiction edge creation follows the same shape.
- `reconsolidation._rank_by_similarity()` — sqlite-vec cosine similarity ranking. Reusable for any target selection.
- `graph.expand_neighbors()` priority table — Already has slots for `contradicts` at priority 3. Just needs the edges to exist.
- `retrieval._get_thread_narratives()` — Tier 2.5 implementation. Pattern for new tier methods (budget, greedy selection, logging).
- `affect.InteractionAnalyzer` — Already persists state to `.affect-{session_id}.json`. Needs `current_state()` accessor but the persistence mechanism exists.
- `storage.get_threads_for_memories_batch()` — Batch query pattern from thread-retrieval work. Reusable for batch edge queries.

### Established Patterns

- **Flag gating:** Every feature check is `if flags.is_enabled("feature_name"):` at the call site, not at the feature implementation level.
- **Edge metadata as JSON TEXT:** All edge metadata is stored as JSON string in the `metadata` column. Vary structure by edge type per the spec's schema.
- **Non-fatal hook errors:** Each subsystem in `pre_compact.py` is wrapped in try/except so one failure doesn't block others.
- **Per-operation SQLite connections:** All store methods open fresh connections.
- **Schema migrations via try/except ALTER TABLE:** Idempotent column additions.

### Integration Points

- `pre_compact.py` step 2 (reconsolidation) — Add contradiction edge creation here, after the existing causal edge creation.
- `pre_compact.py` step 6 (build_threads) — Thread narrator already returns arc metadata. Wire contradiction edge creation for correction_chain threads.
- `pre_compact.py` top-level — Load session affect state from `.affect-{session_id}.json` and pass to reconsolidation for edge coloring.
- `retrieval.inject_for_session()` — Insert Tier 2.6 (Active Tensions) and Tier 2.8 (Counterpoint) methods after existing Tier 2.5.
- `core/flags.py` — Add `contradiction_tensors`, `affect_signatures`, `adversarial_surfacing` to the flag registry.
- `core/relevance.py` integration_factor — Add `_has_contradiction_edges()` check alongside existing `_has_causal_edges()`.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches following existing codebase patterns.
</specifics>

<deferred>
## Deferred Ideas

- **Temporal Echoes** — Explicitly excluded from this iteration. The schema columns exist but the feature (`detect_echoes()`, Tier 2.7, echo-based promotion) is deferred to a separate iteration.
- **Pairwise contradiction scan** — The spec's Source 3 (LLM-based pairwise comparison at consolidation time) is explicitly deferred in the spec itself.
- **Meta-observation: recurring emotional patterns** — Correlating affect data across sessions to detect topic-level frustration patterns. Explicitly deferred in the spec.
</deferred>

---

_Context for: Relationship Engine — Phases 2 & 3 (minus Temporal Echoes)_
_Gathered: 2026-03-29_
