# memesis — Design Ethos

*The north star for decisions. Each pillar should discriminate real choices — not describe generic SWE hygiene.*

---

## 1. Signal Over Storage

**Claim:** The goal is not to remember everything. It is to remember what matters.

**Decides:** Orphaning is a quality gate; emitting zero cards is correct when clusters don't cohere. Prune aggressively at consolidation. Jaccard dedup + content-hash dedup coexist because both matter at different similarity thresholds.

**Rejects:** Recall completeness as a metric. Raw observation count as a success indicator. Defaulting to `keep` when uncertain.

---

## 2. Cognitive Realism

**Claim:** Memory is not flat. Affect, recency, friction, and repetition all modify salience — and the system must model that explicitly.

**Decides:** Somatic signals feed importance before synthesis (`apply_affect_prior`). Kensinger bump is applied at the write site, not the inference site (D1=C). Mixed-valence memories carry the flag. Affect has a lifecycle path.

**Rejects:** All observations treated as equal weight. Affect as cosmetic metadata. Simplifying the cognitive model to reduce complexity.

---

## 3. Write-Site Discipline

**Claim:** Data integrity is established at creation. Downstream recovery is expensive and partial.

**Decides:** #32 (importance throw-away) is Wave A, not deferred. Schema columns for new fields must exist before `Memory.create()` passes them. Every new field returned by `extract_card_memory_fields()` triggers a write-site audit before the PR closes.

**Rejects:** "We'll fix reads later." Passing fields through without confirming column existence. Silent field drops (the `criterion_weights`/`rejected_options` class of bug).

---

## 4. Progressive Durability

**Claim:** Trust is earned, not assigned. Observations are uncertain; crystallized memories are authoritative.

**Decides:** The `ephemeral → consolidated → crystallized → instinctive` lifecycle is the load-bearing premise. Stage advancement gates exist. Stats fields track how memories move (or stagnate). Reconsolidation is a real operation, not an edge case.

**Rejects:** Assigning high importance to new observations by default. Treating all stages as equivalent for retrieval. Skipping lifecycle advancement checks to simplify code.

---

## 5. Non-Intrusive Injection

**Claim:** Memory should feel like intuition, not interruption. If retrieval slows or noises the session, the system has failed its purpose.

**Decides:** Hook timeouts are hard constraints (SessionStart 5s, PreCompact 30s, UserPromptSubmit 3s). Retrieval has a cost ceiling. Three-tier retrieval (instinctive → crystallized → FTS) prioritizes fast/high-confidence paths first.

**Rejects:** Injecting everything relevant. Retrieval paths without latency budgets. Trading session-start speed for recall completeness.

---

## 6. Auditable, Not Magical

**Claim:** The system should be inspectable. Every decision should be traceable to a signal, rule, or threshold.

**Decides:** Stats fields are first-class (`extraction_stats`, `cards_unused_high_importance`, `cross_window_dedup_hits`). The rule registry is the source of truth for thresholds; hardcoded values are a code smell. Lifecycle audit scripts are required, not optional. `RULE_OVERRIDES` flows through the registry.

**Rejects:** LLM outputs accepted without validation. Thresholds scattered across source files. Opacity in why a memory was kept or pruned.

---

## 7. Single Responsibility at Every Boundary

**Claim:** Each system boundary (extraction, consolidation, retrieval) owns one transformation. Shared state across boundaries creates silent bugs.

**Decides:** Kensinger is applied once, at the consolidator (not in the synthesis prompt and again at write-time). Validator detects; synthesizer acts. Prompt rules don't duplicate consolidator logic.

**Rejects:** Prompt rules that perform the same adjustment as code. Two demotion paths that can desync. Retrieval logic that also writes. Logic scattered across boundaries on the assumption both paths will comply.

---

## Using This Document

When a design disagreement arises, test each option against these pillars. If an option violates a pillar, name which one and explain why the violation is or isn't acceptable. If a new pillar is needed to decide a case, propose it here first rather than making the decision ad-hoc.

This document describes what is already true in the architecture and the decisions that have been made. It is not aspirational. If a pillar is not yet fully implemented, that is technical debt — label it, don't promote it to ethos.
