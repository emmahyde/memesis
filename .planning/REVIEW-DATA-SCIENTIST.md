# Data Scientist Review: TAXONOMY-AND-DEFERRED-PATTERNS.md

## TL;DR (3 sentences)

The schema proposal is architecturally coherent but the retrieval-improvement case is asserted, not demonstrated — there is no eval plan, no baseline measurement, and no power analysis anywhere in the document. The activation formula (`importance × recency × access_boost`) is a plausible heuristic but its three components are not independently motivated, the threshold `0.05` for pruning is pulled from thin air, and the entire decay model is applied to a system whose retrieval quality has never been measured. Bloom-Revised `knowledge_type` is the strongest decision in the doc — but it requires inter-annotator agreement testing before any claim about classification reliability can be made.

---

## Findings

### F1: No retrieval quality baseline exists  Severity: BLOCKER
Section: §8, §9
Issue: The entire W5 plan improves a retrieval system whose current performance is nowhere measured. FTS5 + vector similarity is the baseline. What is its precision@k? Recall@k? MRR? NDCG? Without this, there is no way to know whether the activation formula, the taxonomy changes, or the Zettelkasten edges move the needle at all.
Evidence: The doc cites Park 2023, A-MEM, and MemoryBank for empirical support, but these are measured against *their own* baselines on their own datasets (Generative Agents park simulation, long-horizon QA benchmarks). None of those benchmarks apply to a software-developer session-memory retrieval task. The citations lend credibility-by-association, not empirical support for this specific system.
Recommendation: Before shipping W5, instrument the current retrieval path. Log query + retrieved memories + whether the returned memory was actually used (click-through signal or explicit acceptance). Even 50 queries gives you a weak baseline. Without this, W5 is a blind optimization.

---

### F2: Activation formula — three unmotivated knobs multiplied together  Severity: MAJOR
Section: §9 ("Activation formula")
Issue: `activation = importance × exp(-Δt/τ) × (1 + log(1 + accesses))` is presented as if it follows from ACT-R and Park 2023. It does not. ACT-R base-level activation is `A = ln(Σ t_i^-d)` — a log of a sum of power-law terms over every access timestamp. The formula here replaces that with a single-timestamp exponential and then bolts on a log-count term. Park 2023's recency score is simply `exp(-0.99^Δt)` — no importance or access multiplier. MemoryBank uses Ebbinghaus `R = e^(-t/S)` and boosts *S* (stability), not *R* directly. None of these papers multiply all three components together. This formula is a new construction that cites the papers without actually using their models.
Evidence: ACT-R: Anderson 1983 ch. 4; Park 2023 arXiv 2304.03442 §3.2; MemoryBank arXiv 2305.10250 §2.2. In all three, recency and access history are combined additively or by modifying a stability parameter — not multiplicatively in the retrieval score.
Recommendation: Either (a) use one of the cited formulas directly with the actual parameter derivation, or (b) explicitly call this a heuristic combination and treat it as a hyperparameter to tune against the retrieval baseline from F1. If multiplicative, show the monotonicity + dynamic range properties analytically — the current form can produce `importance=0.9, age=0, accesses=100` → `activation ≈ 5.5`, which exceeds 1.0 and would need renormalization before being combined with cosine similarity scores in [0,1].
Side note: `access_boost = 1 + log(1 + accesses)` prevents the "runaway" problem noted in the doc, but log-1-plus is only sub-linear for small counts. At 1000 accesses it's ~6.9× — this can completely dominate `importance` for frequently-accessed low-importance memories (e.g., a boilerplate config fact that's injected repeatedly). This is the Matthew Effect problem: rich get richer. Is that actually the desired behavior?

---

### F3: Pruning threshold 0.05 is uncalibrated  Severity: MAJOR
Section: §9 ("Pruning policy")
Issue: The doc prunes memories where `activation < 0.05`. This threshold is not derived from anything. There is no false-prune rate estimate. No sensitivity analysis. No consideration of what fraction of the existing corpus would be pruned on day one if this were deployed.
Evidence: Given `activation = importance × recency × boost`, a T3 memory (τ=48h) with `importance=0.4`, not accessed since creation, reaches `activation = 0.4 × e^(-t/48)`. It crosses 0.05 at `t = 48 × ln(8) ≈ 99.6 hours` (~4.1 days). A T3 memory with `importance=0.5` crosses 0.05 at `t ≈ 48 × ln(10) = 110h` (~4.6 days). This is extremely aggressive — a useful "domain" memory about a project architecture detail gets auto-pruned in under a week if the user doesn't happen to retrieve it. The doc acknowledges this is a problem for "completed projects" but doesn't analyze the false-prune rate.
Recommendation: Compute the empirical distribution of `importance` scores and `access_count` on the existing corpus. Simulate decay for 30/60/90 days and plot what fraction survives. Then set the threshold based on what survival rate is acceptable. A/B test with threshold variants {0.01, 0.05, 0.10} once the eval baseline from F1 exists. Also reconsider: pruning at 4 days is calibrated to entroly's "ticks" model (which has very different semantics from wall-clock hours in a developer memory tool).

---

### F4: Importance is set once by LLM at extract time — this is a circularity  Severity: MAJOR
Section: §3 ("importance"), §9 ("Tier assignment")
Issue: `importance` is estimated by the LLM at Stage 1 extraction with anchors at 0.3/0.5/0.7/0.9. This same `importance` value is then used to assign `salience_tier`, which determines `decay_tau_hours`, which governs how quickly the memory disappears. The LLM's initial importance estimate is therefore the single point of failure for the entire lifecycle. If the LLM over-assigns importance (which is a known bias — LLMs tend toward 0.7–0.9 when in doubt), T4 memories (ephemeral, prune fast) will be systematically under-represented. If it under-assigns for slow-domain facts, they get pruned in 4 days regardless of actual utility.
Evidence: The doc notes at §9 final "What this does NOT do": "Importance re-scoring — currently importance is set once at extract; could be re-scored on consolidate or via reflection." This is acknowledged as a gap, but its severity is not called out: the entire decay model is built on a static score whose calibration is unvalidated.
Recommendation: At minimum, run an importance calibration audit: sample 100 existing memories, have a human (or second LLM call) re-score importance, compute Spearman correlation with original scores. If correlation is below 0.6, the entire decay model is built on noise. Consider: Stage 2 should re-score importance during consolidation (it sees more context); this is one line in the Stage 2 output schema and directly addresses the circularity.

---

### F5: Bloom-Revised `knowledge_type` — inter-annotator agreement never validated  Severity: MAJOR
Section: §3 ("Decision 2026-04-27"), §8 item 8
Issue: The doc claims Bloom-Revised provides "academically-backed" classification with better reliability than the existing folk taxonomy. This is only true if the LLM achieves consistent classification. The education research behind Bloom 2001 is about human cognitive outcomes, not about whether an LLM will reliably distinguish `conceptual` from `procedural` for a given software observation. These are genuinely hard to distinguish: "always call `_resolve_db_path` before `init_db`" could be `procedural` (a sequence) or `conceptual` (a dependency constraint). "EventBus uses copy-on-write snapshot" could be `conceptual` (mechanism) or `factual` (a specific implementation fact).
Evidence: Anderson & Krathwohl 2001 validate Bloom-Revised for *instructional design* and *assessment* in education contexts — the ~50k citations are for that purpose. The doc uses this citation to claim the taxonomy is "validated for retention + cross-context transfer" in a memory retrieval context, which is a category error. There is no study of LLM inter-annotator agreement on Bloom-Revised classification of software observations.
Recommendation: Before deploying this as a retrieval feature: (a) generate 50 diverse software observations from the existing corpus, (b) run the classification prompt three times with slight temperature variation, (c) compute fleiss-kappa across runs. If kappa < 0.6, `knowledge_type` is unreliable as a retrieval filter and should be treated as soft metadata, not a hard retrieval axis. The test suite requirement (§8) only checks schema validation, not classification consistency — add a kappa test.

---

### F6: Multi-axis cardinality — no demonstration that it improves retrieval  Severity: MAJOR
Section: §3 ("Axis 1/2/3"), §8
Issue: The 4-axis schema (`kind × subject × work_event × knowledge_type`) creates 6×6×7×4 = 1008 possible label combinations. The doc does not show that any downstream retrieval query actually uses more than one axis at a time, nor that multi-axis filtering outperforms single-axis or pure vector recall. Retrieval systems with too many dimensions can hurt performance through filter fragmentation: a query filtered to `kind=finding, subject=domain, knowledge_type=conceptual` may return 0 results where vector similarity would return the right answer.
Evidence: The A-MEM citation (Xu 2024, arXiv 2502.12110) supports *graph traversal* outperforming vector-only recall on long-horizon QA — this is about the `linked_observation_ids` edges, not the taxonomic labels. The cardinality explosion buying retrieval gains is an unexamined claim.
Recommendation: Start with `kind` only as an active retrieval filter (smallest cardinality, clearest semantics). Log which filters are actually used in practice. Add `subject` only if retrieval audits show it's a useful discriminator. `work_event` is useful for attribution (which code action caused this), not for general retrieval — keep it as metadata, not a filter axis.

---

### F7: `linked_observation_ids[]` populated by LLM — this is unreliable at scale  Severity: MAJOR
Section: §8 item 9, §9 ("Access reinforcement")
Issue: Stage 2 is asked to "identify which existing memories the new observation extends, contradicts, or builds on — populate `linked_observation_ids[]`." This requires the LLM to reason about the full memory corpus during every consolidation call. The doc doesn't address: (a) how many memories are visible to Stage 2 at this point (manifest size), (b) whether the manifest is large enough to make this useful but small enough that the LLM doesn't hallucinate connections, or (c) what the false-positive rate is for spurious links.
Evidence: A-MEM (Xu 2024) uses LLM-generated links as its core contribution and reports improvements on long-horizon QA — but the A-MEM evaluation corpus is controlled and the link quality is validated against ground truth. There is no equivalent validation here.
Recommendation: Rate-limit link generation: Stage 2 should only attempt linking if the consolidation manifest contains ≥ 5 candidate memories with high semantic similarity (cosine > 0.75) to the new observation. Require that each proposed `linked_observation_id` is present in the manifest (validate before insert — hallucinated UUIDs otherwise quietly become dangling references). Log link generation and do a spot audit after 100 consolidations.

---

### F8: Tier thresholds (0.4/0.7/0.9) — arbitrary, boundary behavior unspecified  Severity: MINOR
Section: §9 ("Tier assignment table")
Issue: The tier thresholds are round numbers with no derivation. The boundary behavior is: a memory at `importance=0.699` gets τ=48h (T3); one at `importance=0.700` gets τ=168h (T2) — a 3.5× difference in half-life from a 0.001 difference in importance score. This is a discrete discontinuity in a system whose input (LLM importance scoring) is noisy to at least ±0.1.
Evidence: The doc's own anchors (§3/§6) place "important" at 0.7–0.8 and "useful" at 0.5–0.6 — these bands are soft, not crisp. An LLM asked to assign 0.7 may reliably produce 0.65–0.75. This means T2/T3 boundary membership is noise-driven.
Recommendation: Use soft tier assignment (weighted average of τ values based on importance) rather than hard thresholds, or at minimum add ±0.05 hysteresis at each boundary. Alternatively, make τ a continuous function of importance: `τ(i) = 12 × exp(i × ln(60))` maps [0,1] → [12h, 720h] smoothly without discontinuities.

---

### F9: `open_question` type — no lifecycle model  Severity: MINOR
Section: §3 (Stage 1 types), §4 (back-derivation), §8 item 6
Issue: `open_question` observations have no Stage 2 counterpart and no defined lifecycle. The doc says "add it or explicitly PRUNE." Neither choice is analyzed. Open questions are high-value: they represent unresolved issues the system should surface. Pruning them silently is a data-quality loss. Promoting them as `kind=open_question` without a resolution mechanism means they accumulate indefinitely.
Evidence: None of the reference systems (claude-mem, headroom, entroly) have an "open question" type. The doc introduces it from Stage 1 without connecting it to any resolution pattern.
Recommendation: Treat `open_question` as a first-class type with a separate lifecycle: they should not decay (they're pinned until resolved), they should be surfaced in session context injection with a distinct signal, and they should be resolvable by Stage 1 detecting a `correction` or `finding` that addresses the same topic. This is the "question answering" loop that makes the memory system useful across sessions — worth designing properly rather than punting.

---

### F10: `facts[]` attribution contract — no validation mechanism  Severity: MINOR
Section: §3 ("facts[]"), §6 ("Atomic Fact Format"), §8 item 7
Issue: The WHO/WHAT/WHEN/WHERE attribution contract is enforced by prompt instruction only. The doc and test suite have no check that a returned fact actually starts with a named subject (not a pronoun). LLMs reliably violate this constraint under prompt compression, especially for short observations.
Evidence: The test suite in §8 checks schema validation (field presence, type correctness) but has no test that verifies: `all(fact.split()[0].istitle() for fact in memory.facts)` or equivalent. The headroom implementation at `extraction.py:260–268` has the same prompt-only enforcement — no hard validation at parse time.
Recommendation: Add a parse-time validator in `core/consolidator.py`: reject any fact that begins with a pronoun (`he`, `she`, `it`, `they`, `we`, `i`, `this`, `the`). Soft rejection: log and flag, not hard fail. This is 5 lines and catches the most common violation pattern.

---

### F11: Stage 2 importance re-emission — not just schema, needs re-calibration guidance  Severity: MINOR
Section: §8 item 2, §6 ("Importance Scoring")
Issue: Adding `importance` to Stage 2 output schema (currently absent) is correct. But the doc doesn't specify whether Stage 2 should preserve Stage 1's `importance` value, override it, or average the two. Stage 2 sees more context (full consolidation buffer, existing memories, KEEP/PRUNE/PROMOTE reasoning) — it is better positioned to assess importance than Stage 1. But if it routinely overrides Stage 1's estimate upward, all memories cluster at high importance and the tier model breaks.
Recommendation: Stage 2 should re-score independently and the Stage 2 score should win, but the Stage 1 score should be preserved as `raw_importance` for audit. Monitor the distribution of Stage 2 vs. Stage 1 importance scores over 200 consolidations. If median Stage 2 importance > 0.65, the prompt anchors need tightening.

---

## Missing analysis

What the doc should add but doesn't:

- **Retrieval baseline measurement.** No precision@k, recall@k, MRR, or user-acceptance rate for current FTS5 + vector retrieval. Every improvement claim is unanchored.
- **LLM classification agreement study.** Bloom-Revised `knowledge_type` and `kind` both need fleiss-kappa measurement before being treated as reliable retrieval features.
- **Corpus distribution analysis.** What does the existing importance score distribution look like? What fraction of memories would be T4 (< 0.4)? What is the current corpus size and growth rate? The pruning model is designed for an unknown target.
- **False-prune rate simulation.** Simulate the pruning policy on the existing corpus at 30/60/90 days and report what fraction survives. Identify which `kind` and `subject` values are most vulnerable to early pruning.
- **Activation dynamic range and normalization.** The activation formula is not bounded to [0,1]. How does it combine with cosine similarity scores in retrieval ranking? Is it a reranker (activation re-orders vector-retrieved results) or a pre-filter (activation < θ → excluded)? The doc conflates both uses.
- **Cost analysis for linked-graph traversal.** Following `linked_observation_ids` edges at retrieval time means additional DB queries per hop. For a graph with high average degree (many linked observations), this can explode. What is the expected out-degree? What is the hop depth cap?
- **Statistical power for any A/B eval.** If you ran a 4-week A/B test of W5 vs. baseline, how many queries would you need to detect a 10% improvement in retrieval quality with 80% power? This is a solvable calculation and should bound the eval timeline.

---

## What I'd actually do differently

If I were authoring this from scratch:

1. **Measure before designing.** Ship a minimal instrumentation layer (log queries + accepted memories) and run it for 2 weeks before any schema changes. Without this, you're optimizing a black box.

2. **Start with `kind` only.** The six `kind` values (decision/finding/preference/constraint/correction/open_question) are the most unambiguous, least correlated axis. Deploy this single field, measure whether filtering by `kind` improves retrieval quality in practice, then add `subject` only if the data supports it.

3. **Use Park 2023's formula faithfully.** Their recency score is `exp(-0.99^Δt_hours)` — it's simple, motivated, and tested on an actual retrieval task. Don't combine it multiplicatively with importance and access count without evidence that the combination outperforms the simpler version.

4. **Treat `linked_observation_ids` as a Phase 2 feature.** The Zettelkasten edges are architecturally attractive and A-MEM's results are real — but link quality is the critical variable and it requires a validation study before production use. Ship the schema field in W5, but don't wire Stage 2 to populate it until you have a link-quality eval.

5. **Calibrate `importance` with a second-pass audit.** Sample 100 memories, re-score by hand (or second LLM call), compute correlation. If Spearman ρ < 0.6, the entire decay model needs a different foundation.

6. **Treat pruning as a separate milestone.** The decay/pruning machinery (§9) is complex, has multiple uncalibrated thresholds, and has no false-prune safety net. Shipping it in the same milestone as the taxonomy changes creates two large unknowns at once. Ship the schema fields, measure for 4 weeks, then enable pruning.

---

## Concrete eval plan (the doc lacks one)

### Goal
Determine whether W5 (taxonomy changes + activation-based retrieval) improves retrieval quality over the current FTS5 + vector baseline.

### Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| Precision@5 | Fraction of top-5 retrieved memories judged relevant by user | Baseline + 10% |
| Acceptance rate | Fraction of injected memories that the session used (clicked/referenced) | Baseline + 10% |
| False-prune rate | Fraction of pruned memories re-created within 30 days (indicates they were needed) | < 5% |
| Classification consistency (κ) | Fleiss-kappa for `kind` and `knowledge_type` across 3 prompt runs on 50 observations | ≥ 0.7 |
| Importance calibration (ρ) | Spearman correlation between LLM-assigned and human-assigned importance scores | ≥ 0.6 |

### Setup

**Phase 0 — Instrument baseline (2 weeks, no schema changes):**
- Log every retrieval query: timestamp, query text, memories returned, which were accepted/used
- Log every consolidation: observation `kind` (Stage 1 `mode`), importance score, whether KEPT/PRUNED/PROMOTED
- Compute current precision@5 and acceptance rate from logs

**Phase 1 — Classification consistency test (1 day):**
- Sample 50 diverse existing memories
- Run Stage 1 classification prompt 3× with temperature 0.3
- Compute fleiss-kappa for `kind` and `knowledge_type`
- If kappa < 0.6 for either: revise prompt anchors before shipping

**Phase 2 — Importance calibration (2 days):**
- Sample 100 existing memories with their current importance scores
- Have a human (or GPT-4 with detailed rubric) re-score independently
- Compute Spearman ρ
- If ρ < 0.6: revise anchor definitions, re-sample 50 more

**Phase 3 — Deploy W5 taxonomy fields (no activation scoring yet):**
- Ship items 1–10 from §8 pull-list
- Run for 4 weeks alongside baseline logging
- Compare `kind`-filtered retrieval acceptance rate vs. unfiltered

**Phase 4 — Activation scoring A/B (4 weeks):**
- Enable activation-based retrieval reranking (§9) for 50% of queries
- Compare precision@5 and acceptance rate between activation-reranked and baseline
- Required n: at power=0.80, α=0.05, δ=0.10, σ≈0.25 (binary relevance) → n ≈ 200 queries per arm. 4 weeks at ~10 queries/day is borderline — consider 8 weeks or a more sensitive metric.

**Phase 5 — Pruning safety validation (separate from W5):**
- Before enabling auto-pruning: simulate on full corpus, report survival rates by tier and kind
- Set false-prune target at < 5%, adjust threshold accordingly
- Shadow-prune (log only) for 2 weeks before enabling destructive pruning
