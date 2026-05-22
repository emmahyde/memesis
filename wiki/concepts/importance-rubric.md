---
title: Importance Rubric
type: concept
tags: [importance, scoring, lifecycle, crystallization, instinctive]
---

# Importance Rubric

The consequence-anchored five-band rubric used by the Stage 2 consolidation LLM to score `importance` on every kept memory. Defined in `core/prompts.py:252` (IMPORTANCE RE-SCORING block, panel C7).

The importance score is not an abstract quality rating — it directly gates the memory lifecycle. Score against consequences, not against subjective quality.

## Bands

| Band | Score | Name | Lifecycle Effect |
|---|---|---|---|
| 1 | 0.00–0.30 | ROUTINE | Memory expires. Re-derivable from code, git log, types, or CI. Default unless a higher band's test clearly passes. |
| 2 | 0.30–0.55 | CONTEXT | Saves recall time; getting it wrong costs a minor detour, never rework. Stays as short-lived consolidated memory. |
| 3 | 0.55–0.74 | SIGNIFICANT | Material finding, but scoped or contingent. True for one project, tool version, or session. Stays consolidated; never becomes permanent. |
| 4 | 0.75–0.84 | LOAD-BEARING | Getting this wrong causes real rework or repeats a mistake, AND it applies beyond the session it came from. **Crystallizes** via the high-importance fast path. |
| 5 | 0.85–1.00 | INVARIANT / CORRECTIVE | Explicit user correction, hard constraint, or behavioral rule that should fire every session. **Instinctive-eligible** (also requires 10+ session usage). |

## Lifecycle Thresholds

- `importance >= 0.75` → triggers `_can_promote_to_crystallized` high-importance fast path (`core/lifecycle.py:352`)
- `importance > 0.85` → satisfies the instinctive gate's importance check (`core/lifecycle.py:414`)

## Kind-Based Default Bands

Soft defaults — override with a stated reason in `rationale`.

| Kind | Default Band |
|---|---|
| correction, constraint | 0.85–1.00 |
| decision, preference | 0.75–0.84 |
| open_question (with action item) | 0.75–0.84 |
| open_question (no action item) | 0.30–0.55 |
| finding | No shift — score purely on band tests |

## Calibration Rules

Spread scores. A cluster at 0.5–0.6 carries no ranking signal. Commit to a judgment.

Push toward a **higher** band for:
- Numeric evidence backing the claim
- Alignment with established engineering practice
- Explicit unresolved action item

Push toward a **lower** band for:
- Subject matter owned by a third-party package not maintained here
- Anything already enforced mechanically by a test, hook, type, or CI check

## Stage 1 vs Stage 2 Scoring

Stage 1 (transcript extraction): window-local salience only. Anchors: 0.2 passing mention, 0.5 concrete outcome in window, 0.8 central finding, 0.95 explicit correction or hard constraint. Do not inflate to compensate for Stage 2.

Stage 2 (consolidation): re-scores independently using full buffer and manifest. Must diverge from Stage 1 when context justifies it. This is the score that governs the lifecycle. (`core/prompts.py:252`)

## Related

- [[promotion-gates]] — how importance thresholds gate stage transitions
- [[memory-lifecycle]] — the stage model importance feeds into
- [[memory-kind-taxonomy]] — kind values that set default bands
