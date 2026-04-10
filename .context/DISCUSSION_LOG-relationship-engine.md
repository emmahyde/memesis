# Discussion Log: Relationship Engine — Phases 2 & 3 (minus Temporal Echoes)

> **Audit trail only.** Do not use as input to implementation agents.
> Decisions are captured in CONTEXT-relationship-engine.md — this log preserves the alternatives considered.

**Date:** 2026-03-29
**Work:** Implement Contradiction Tensors, Affect Signatures, and Adversarial Surfacing from the relationship engine spec
**Areas discussed:** Scope, Edge lifecycle, Adversarial personality

---

## Implementation Scope

| Option | Description | Selected |
| --- | --- | --- |
| Phase 2 only | Contradiction Tensors + Temporal Echoes | |
| Phase 2 + Phase 3 (all four) | All four remaining features | |
| Custom selection | User-specified subset | ✓ |

**User's choice:** All features EXCEPT Temporal Echoes — Contradiction Tensors (Phase 2) + Affect Signatures (Phase 3) + Adversarial Surfacing (Phase 3).
**Notes:** User explicitly said "I don't want Temporal Echoes, everything else tho." No rationale given for excluding echoes.

---

## Contradiction Edge Lifecycle

| Option | Description | Selected |
| --- | --- | --- |
| Always create, leave unresolved | Create edge first, then resolve. Edge as historical record. | |
| Skip for superseded | Only create edges for scoped/coexist. Superseded archived anyway. | |
| Create but auto-resolve | Create for all types, immediately mark superseded as resolved:true. | ✓ |

**User's choice:** Create but auto-resolve (Claude's recommendation).
**Notes:** User asked "What do you recommend?" — Claude recommended create-but-auto-resolve for full historical graph without Active Tensions noise.

---

## Adversarial Thompson Prior

| Option | Description | Selected |
| --- | --- | --- |
| Cautious — Beta(1,3) | ~25% initial sample rate, learns upward | ✓ |
| Neutral — Beta(1,1) | ~50% initial sample rate, no bias | |
| Eager — Beta(2,1) | ~67% initial rate, assumes useful | |

**User's choice:** Cautious — Beta(1,3) (Claude's recommendation).
**Notes:** Claude recommended cautious start: "the system earns the right to challenge by proving counterpoints are engaged with."

---

## Claude's Discretion

- Thread narration early/late member split for correction_chain edges
- Affect trajectory detection exact thresholds
- Session affect loading wiring in inject_for_session

## Deferred Ideas

- Temporal Echoes — entire feature deferred by user choice
- Pairwise contradiction scan — deferred per spec
- Recurring emotional pattern meta-observations — deferred per spec
