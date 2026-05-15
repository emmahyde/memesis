# F4 — Soften PROMOTE Bias in CONSOLIDATION_PROMPT Audit

**Verdict: CONCERN**

The PROMOTE gate is correctly tightened in language, but empirical ConsolidationLog data shows PROMOTE is still firing at ~33% of decisions across recent runs, and the same two or three memory UUIDs are receiving repeated promote reinforcements across consecutive sessions. The "Most observations should KEEP" selectivity line does not dominate LLM behavior when the manifest is large enough to surface high-overlap memories. One structural ambiguity — the BEHAVIORAL GATE vs SELECTIVITY tension — also creates unpredictable routing at the KEEP/PRUNE boundary.

---

## 1. Gate Inventory and Consistency

Gates in `core/prompts.py:111-308`:

- **MANDATORY KEEP** (line 135): Unconditional for `[PRIORITY]`-tagged observations. Unambiguous.
- **KEEP gates** (lines 139-147): Four ordered criteria — corrections, preference signals, self-observations, workflow patterns. The priority order is declared but the LLM is not told how to handle an observation that qualifies under multiple criteria; the output is the same (`keep`) either way, so this is benign.
- **PROMOTE gates** (lines 149-163): "Only on exact restatement" — requires same fact, same subject, same scope. Sets `reinforces`. Explicitly forbids separate dupe KEEP.
- **SUPERSEDE gates** (lines 174-184): For contradiction with a replacement fact. Atomic (no ARCHIVE+KEEP pair).
- **ARCHIVE gates** (lines 186-192): For pure obsolescence with no successor.
- **PRUNE if** (lines 201-207): Five conditions — re-derivable, one-time mechanics, generic, preference-without-WHY, should-have-done-X-without-pattern.

**Overlap/conflict assessment:** SUPERSEDE and ARCHIVE are cleanly separated by the "is there a replacement fact?" criterion (line 186). No conflict. PRUNE and KEEP have a latent overlap: "Preferences without the WHY" (line 204) is a PRUNE condition, but KEEP criterion 2 (line 143) also carves out preferences ("ONLY keep if the preference is surprising"). Both paths apply to the same observation class; which the LLM selects depends on word-choice in the observation text. This is a soft conflict — the effective outcome may differ from the intended gate ordering.

---

## 2. BEHAVIORAL GATE vs SELECTIVITY Line

The BEHAVIORAL GATE (line 115): "Would I do something wrong without this? PRUNE IT."

The SELECTIVITY line (line 208): "Most observations should KEEP. PRUNE only what is trivially derivable."

These pull in opposite directions. The BEHAVIORAL GATE sets a high bar ("would I do something *wrong*") as a PRUNE trigger, which implies aggressive pruning of observations that merely save time. The SELECTIVITY line then counters with "most should KEEP," which lowers that bar substantially. The LLM sees both in the same prompt and must reconcile them without a declared tie-breaker.

In practice, the BEHAVIORAL GATE appears earlier in the prompt (line 115) while SELECTIVITY appears after the gate definitions (line 208), placing it closer to the output format section. Positional recency effects in LLMs favor later instructions, suggesting SELECTIVITY may dominate — consistent with the 62% KEEP rate observed (39 keeps / 62 total decisions across last 15 runs). However, when both are active in the same decision context, the LLM's behavior is unpredictable rather than deterministic.

**Verdict on this tension:** the BEHAVIORAL GATE should either be removed or reframed as a *quality modifier* ("before deciding KEEP, confirm it passes this test") rather than a standalone PRUNE trigger, to prevent conflict with SELECTIVITY.

---

## 3. PROMOTE Softening and "Exact" Interpretation

The prompt now requires "EXACTLY DUPLICATES an existing memory's core claim (same fact, same subject, same scope)" (line 151). This is a meaningful tightening from the pre-F4 wording that routed refine/extend to PROMOTE.

**How LLMs interpret "exact":** The LLM's token-level sense of "exact" is semantic, not lexical. A paraphrase of the same fact will be treated as exact if the propositional content is identical. The examples at lines 165-172 help calibrate this, but they only cover three cases. Borderline cases — where a new observation adds a condition clause or refines scope — are underspecified.

**Evidence from ConsolidationLog:** Rationale strings across logs 8-15 all read "Exact restatement of existing memory [uuid]" (verbatim), suggesting the LLM is producing the phrase as a compliance signal rather than as a reasoned judgment. Memory `5f50325f` was promoted against in logs 8, 9, 10, 11, 12, 13, and 15 — the same memory receiving seven promote reinforcements across consecutive sessions. This pattern strongly suggests the LLM is treating "exact restatement" loosely (paraphrases qualify), not strictly.

**Dupe KEEP risk:** If the LLM is over-promoting on paraphrases, then observations that differ meaningfully (adding a condition, refining scope) may still be PROMOTE'd. But if the `reinforces` UUID is stale or hallucinated (the LLM recycles known UUIDs from prior context rather than consulting the live manifest), those PROMOTE decisions are silently no-ops. The more dangerous case is the reverse: near-paraphrases that the LLM routes to KEEP (missing the PROMOTE gate) then get processed by `auto_promote_if_dupe` at cosine >= 0.85 (linking.py:40). The embedding-based threshold and the LLM's semantic "exact" threshold are not calibrated against each other — there is no documented test establishing what cosine range corresponds to the LLM's promote gate.

---

## 4. Manifest Exposure and Size

`_build_manifest_summary` (consolidator.py:510-529) iterates `Memory.by_stage()` for `consolidated`, `crystallized`, and `instinctive` stages with no size cap. Each entry emits `[uuid] title: summary`. At the time of audit: 12 consolidated + 1 crystallized + 8 instinctive = 21 non-ephemeral memories (confirmed via query). At this scale the manifest is compact (~40-60 lines), so PROMOTE is *feasible* — the LLM can compare against all entries.

However, there is no cap. As the memory store grows, the manifest will eventually crowd out ephemeral content in the prompt context window (max_tokens=2048 at line 548 of consolidator.py). At ~200 memories the manifest alone could reach 6-8k tokens, degrading extraction quality and forcing the LLM to truncate or hallucinate. No guard is present; this is a latent risk that will manifest at scale.

---

## 5. ConsolidationLog Evidence

Action distribution, last 15 runs (15 log entries, 62 total decisions):

| Action   | Count | %    |
|----------|-------|------|
| keep     | 39    | 63%  |
| promote  | 19    | 31%  |
| prune    | 4     | 6%   |
| archive  | 0     | 0%   |
| supersede| 0     | 0%   |

The 31% PROMOTE rate is high for a gate meant to fire "sparingly — only on exact restatement." The `"Most observations should KEEP"` guidance does increase the KEEP rate relative to what a pure BEHAVIORAL GATE would produce, but PROMOTE is not rare. Logs 8-10 show three PROMOTE decisions per run against identical UUIDs (`5f50325f`, `d16cbce4`, `976f55d8`) in consecutive runs — same observations being re-promoted across sessions, indicating the habituation filter is not suppressing already-processed observations before they reach the LLM, or the observations are genuinely recurring.

---

## 6. Risk: Over-Correction to Dupe KEEPs

F4 over-correction is low risk at the current memory store size (21 memories) because the manifest is fully visible and PROMOTE is still firing above its intended rate. The actual risk is opposite: the current behavior may be correct (many observations legitimately are exact restatements of installed memories from the same ongoing work), but the prompt cannot distinguish between "repeat observation from same dev loop" (should PROMOTE) and "near-paraphrase of a related but distinct fact" (should KEEP).

Detection signal for over-promotion: compare `reinforcement_count` growth rate on a memory vs rate of new observations entering the buffer. If a memory's `reinforcement_count` grows more than twice per week, it is likely receiving over-broad PROMOTE decisions. For dupe KEEP detection: monitor `auto_promote_if_dupe` subsumption rate (linking.py:200) — if it is non-zero and rising, the LLM is missing PROMOTE decisions that the embedding layer is catching.

---

## 7. Recommended Prompt-Evaluation Tests

**Test 1 — Paraphrase discrimination:** Feed two observations to the LLM: one that is a lexically different but propositionally identical restatement of a manifest entry, and one that adds a single conditional clause ("only when X is true"). Assert that the first routes to PROMOTE and the second routes to KEEP. If both route to PROMOTE, the "exact" gate is under-specified. Implement as a pytest fixture in `tests/test_prompts.py` with a mocked manifest and ephemeral buffer.

**Test 2 — BEHAVIORAL GATE vs SELECTIVITY conflict:** Construct an observation that passes KEEP criterion 3 (self-observation, specific and actionable) but also passes the BEHAVIORAL GATE ("code review would surface this"). Verify the LLM routes to KEEP, not PRUNE. If it routes to PRUNE, the BEHAVIORAL GATE is dominating the SELECTIVITY line, which is contrary to stated intent.

**Test 3 — UUID hallucination check:** Provide a manifest with five known UUIDs, then inject an observation that closely resembles a sixth memory *not* in the manifest. Assert that the LLM does NOT emit a PROMOTE with a hallucinated UUID. Currently no validation gate catches this before `_execute_promote` runs; a test documenting the failure mode would also surface the need for a pre-execute UUID existence check against the live DB.
