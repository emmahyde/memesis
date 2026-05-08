# Neuroscientist Review: TAXONOMY-AND-DEFERRED-PATTERNS.md

## TL;DR (3 sentences)

The document borrows academic credibility from Anderson/Krathwohl, ACT-R, and Ebbinghaus without accurately representing any of them: the activation formula is exponential when ACT-R requires power-law, the half-life table conflates time constants with half-lives, and Park et al.'s additive retrieval ranking is silently replaced with a multiplicative one. The decision to use Bloom-Revised (a pedagogical theory of *learner knowledge*) as a taxonomy of *machine memory observations* is a category error — Tulving's biologically-grounded tripartite model is actually more defensible for this use case and was dismissed without argument. The implementation is pragmatically workable despite these misrepresentations, but the academic framing is not earned and several formula errors will produce meaningfully different runtime behavior than the cited models predict.

---

## Fact-check of cited research

| Cited | Doc claim | Source actually says | Verdict |
|---|---|---|---|
| Anderson 1983 (ACT-R) | "base-level activation: `A = ln(Σ t_i^-d)` over access timestamps. Power-law decay." | Correct formula citation. ACT-R base-level learning: `B_i = ln(Σ_{j=1}^{n} t_j^{-d})` where `d ≈ 0.5` (power-law decay), empirically derived from memory experiments. | Correct citation in §9 prose — but then IGNORED in the activation formula, which is exponential. |
| Anderson 1983 (ACT-R activation formula) | Formula implemented: `importance × exp(-age_hrs / τ) × (1 + log(1 + access_count))` | ACT-R base-level activation does not use exponential decay. It uses `B_i = ln(Σ t_j^{-d})` — a sum over all past access times, each raised to the power `-d`. There is no exponential. | **WRONG.** The doc cites ACT-R then implements Ebbinghaus. These are different models. |
| Ebbinghaus 1885 | `R = e^{-t/S}` (via Zhong/MemoryBank, §9 academic basis) | Ebbinghaus 1885/1913 retention function: `R = e^{-t/S}` where S is "strength of memory." Correct. | Correct formula, correctly attributed to MemoryBank/Zhong. |
| Park et al. 2023 (*Generative Agents*, arXiv 2304.03442) | "retrieval ranks memories by `importance + recency + relevance`. Recency = exponential decay with factor 0.99/hour." | Park et al. §3.3: retrieval score = `α·recency_score + β·importance_score + γ·relevance_score` — ADDITIVE weighted sum, not multiplicative. Recency uses `0.99^hours_since_last_retrieval`. | **PARTIALLY WRONG.** Correct recency formula. But doc's activation function multiplies `importance × recency × access_boost`, while Park uses additive combination. Multiplicative means a zero-importance memory can never recover regardless of recency or access count. Additive means each component contributes independently. Different math, different behavior. |
| Anderson & Krathwohl 2001 (Bloom-Revised) | "~50k citations, 4 knowledge dimensions, validated for retention + cross-context transfer in 50+ years of education research" | Anderson & Krathwohl 2001 is a revision of Bloom's 1956 *Taxonomy of Educational Objectives*. The 4 knowledge dimensions (factual, conceptual, procedural, metacognitive) describe **types of knowledge that learners are expected to acquire or demonstrate** in educational settings. The taxonomy is a framework for writing learning objectives and designing assessments, not a framework for classifying stored propositions or memory observations. Krathwohl 2002 (review paper in *Theory Into Practice*) explicitly notes inter-rater reliability issues with factual/conceptual distinction even among trained educators. | **MISAPPLIED.** The taxonomy is valid and well-cited but is a learner-facing pedagogical tool. Using it to classify an AI agent's stored memory observations is a domain transfer that is not validated by the 50+ years of education research cited. That research validated it for *instructional design*, not *memory system classification*. |
| Zhong et al. 2023 (*MemoryBank*, arXiv 2305.10250) | "Ebbinghaus forgetting curve `R = e^{-t/S}` where access boosts strength `S`. Outperformed naive retention." | MemoryBank §3.2 implements Ebbinghaus-style memory updating: repeated retrieval strengthens S; memory strength is updated via `S_{n+1} = S_n × f(r)` after each retrieval. The paper shows improved performance on conversational benchmarks. | Correct, accurately cited. |
| Xu et al. 2024 (*A-MEM*, arXiv 2502.12110) | "A-MEM's linked-note graph beats fixed-schema baselines on long-horizon QA." | A-MEM implements Zettelkasten-style linking with LLM-generated contextual notes and interconnections. Evaluation on LoCoMo shows improvement over fixed-schema baselines on multi-hop queries. | Correct, accurately cited. |
| Collins & Loftus 1975 | Not cited — but "spreading activation" is invoked in §9 | Collins & Loftus 1975 (*Psychological Review*) defines spreading activation as propagation through **semantic networks**, where activation decays with distance through the graph, creating priming effects at related nodes. | Doc's use of "spreading activation" to mean "increment access count on accessed item" is a misnomer. The doc does mention optional propagation along `linked_observation_ids[]` — that's the actual spreading activation concept — but it's presented as optional rather than definitional. |

---

## Findings

### F1: ACT-R Formula Misrepresentation  Severity: MAJOR
Section: §9 "Activation formula (computed at retrieval)"

**Issue:** The doc cites ACT-R and then implements a formula that is not ACT-R. ACT-R base-level activation is:

```
B_i = ln( Σ_{j=1}^{n} t_j^{-d} )
```

where `t_j` is the time since the j-th access (in seconds), `d ≈ 0.5`, and the sum is over all access events. This is a **sum of power-law terms** — it requires storing a full access timestamp history. The doc's formula:

```python
recency = math.exp(-age_hrs / memory.decay_tau_hours)
access_boost = 1 + math.log(1 + memory.access_count)
return memory.importance * recency * access_boost
```

is not ACT-R. It is closer to Ebbinghaus (exponential decay) with a heuristic log-count boost. This matters because:

1. Power-law decay (ACT-R) falls steeply early and slowly later — matching human forgetting data well. Exponential decay (Ebbinghaus) falls at a constant rate — it systematically over-decays recent memories and under-decays old ones relative to the ACT-R model.
2. ACT-R's summation means every access contributes independently to activation — the effect compounds in a specific way. The doc's `log(1 + access_count)` approximation is not derived from ACT-R theory; it's invented.
3. The doc claims ACT-R support for the formula (`~10k citations`). That credibility is not deserved by the formula as implemented.

**Cognitive science basis:** Anderson 1983, *The Architecture of Cognition*; Anderson et al. 2004, *An Integrated Theory of the Mind*.

**Recommendation:** Either (A) implement actual ACT-R base-level activation (requires storing `access_timestamps[]` list), or (B) keep the exponential formula but call it "Ebbinghaus-style with log access boost" and cite Zhong et al. 2023 (MemoryBank) which actually uses this pattern. Drop the ACT-R citation from the formula description. Do not claim ACT-R when implementing Ebbinghaus.

---

### F2: Half-Life vs. Time Constant Confusion  Severity: MAJOR
Section: §9, tier table — "Half-life (τ)" column

**Issue:** The table header says "Half-life (τ)" and the `decay_tau_hours` field is described as encoding half-life. These are not the same thing.

For exponential decay `R = exp(-t/τ)`:
- At `t = τ`: `R = exp(-1) ≈ 0.368`, not 0.5
- τ is a **time constant** (time to decay to 1/e ≈ 37% of original value)
- **Half-life** = `τ × ln(2) ≈ 0.693τ`

So τ=720h is NOT a 30-day half-life. The actual half-life is `720 × ln(2) ≈ 499h ≈ 20.8 days`.

The doc's test comment compounds this: "activation(memory aged τ hours) ≈ importance × 0.368 (e^-1 = 0.368, **half-life sanity**)" — 0.368 is NOT the half-life check, it's the time-constant check. A half-life check would expect 0.5.

The tier table:
| Tier | τ claimed as "half-life" | Actual half-life | Actual time to 37% |
|---|---|---|---|
| T1 | 30d | 20.8d | 30d |
| T2 | 7d | 4.85d | 7d |
| T3 | 2d | 1.39d | 2d |
| T4 | 12h | 8.3h | 12h |

This affects operational semantics: memories decay faster than the documentation implies. A T1 "30-day half-life" memory is actually at 37% strength after 30 days, and at 50% strength after only 20.8 days.

**Cognitive science basis:** Standard exponential decay; Ebbinghaus 1885 formalized this for memory retention.

**Recommendation:** Pick one consistently: either name the parameter `decay_tau_hours` (time constant) and remove "half-life" from all documentation, OR compute half-life as `τ × ln(2)` and store that as `half_life_hours`. Fix the test comment: "activation at t=τ ≈ importance × 0.368 (time-constant check)" — not "half-life sanity."

---

### F3: Additive vs. Multiplicative Retrieval Scoring (Park et al. Mis-implementation)  Severity: MAJOR
Section: §9 activation formula

**Issue:** Park et al. 2023 uses:
```
score = α·recency + β·importance + γ·relevance
```
(additive, with tunable weights; Park uses α=β=γ=1 in their ablations)

The doc implements:
```python
return importance × recency × access_boost
```
(multiplicative, no tunable weights)

Multiplicative combination means:
- A memory with `importance=0.0` has **activation=0 regardless of recency or access count** — it is permanently dead even if accessed frequently. Additive scoring would give it a non-zero recency contribution.
- A memory with `recency≈0` (very old) similarly collapses the whole product. An additive model would preserve the importance and access-count contributions.
- There is no way to tune the relative weight of each factor post-deployment without changing the formula.

Neither formulation is strictly more correct — multiplicative has the property that all factors must be "good" for a memory to rank, which may be the desired semantics. But the doc presents this as implementing Park's model when it is a structural departure from it.

**Cognitive science basis:** Park et al. 2023 §3.3; Anderson 1983 ACT-R does not combine multiple scores multiplicatively.

**Recommendation:** Document this as a design choice, not a Park implementation. If the goal is: "all factors must be strong," multiplicative is defensible. If the goal is: "any strong factor can carry a memory," use additive. Either way, stop attributing the multiplicative formula to Park.

---

### F4: Bloom-Revised Taxonomy — Category Error in Domain Transfer  Severity: MAJOR
Section: §3, "knowledge_type" field and decision rationale

**Issue:** Anderson & Krathwohl 2001 classifies **types of knowledge that learners are expected to acquire or demonstrate** within an instructional context. The four dimensions (factual, conceptual, procedural, metacognitive) answer the question: "What is the student supposed to know/do?" The taxonomy was validated empirically in educational settings — lesson plan design, assessment writing, curriculum alignment.

Using it to classify **propositions extracted from an AI agent's session observations** is a domain transfer that is not supported by the cited research. The "50+ years of education research" the doc invokes validated Bloom-Revised for *instructional design*, not for *memory system classification*. The distinction matters:

1. **Learner context vs. observer context.** Bloom describes what a student *doesn't yet know* and needs to acquire. Memesis describes what an AI *already observed* and needs to store. The directionality is reversed — Bloom is a prospective framework, memesis is retrospective.
2. **Inter-rater reliability is low for factual/conceptual.** Krathwohl 2002 (*Theory Into Practice* 41:4) explicitly notes this is the hardest distinction in the taxonomy, even for trained educators reviewing structured lesson plans. The doc will ask an LLM to make this distinction from unstructured session observations. There is no evidence this is more reliable.
3. **The taxonomy is not about memory longevity or retrieval priority** — it's about cognitive complexity. Using it to drive memory management conflates epistemic category with retention strategy.

That said, the four types as *vocabulary* (factual/conceptual/procedural/metacognitive) are reasonably intuitive and the doc's one-line definitions at §3 are clear. The problem is the academic citation claiming external validation that doesn't apply here.

**Cognitive science basis:** Anderson & Krathwohl 2001; Krathwohl 2002 (*Theory Into Practice* 41(4):212-218); Forehand 2010 (review of empirical validity).

**Recommendation:** Keep the four-type vocabulary — it's genuinely useful as folk taxonomy for this domain. Drop the claim that 50 years of education research validates it for memory system classification. Reframe it as: "borrowed from Bloom-Revised as a convenient four-way partition; validation for this use case is empirical, not inherited." Alternatively, adopt Tulving's tripartite (see F5).

---

### F5: Tulving's Tripartite Memory Was Dismissed Without Argument  Severity: MINOR
Section: §2 reference table ("Axis" comparison), implicitly rejected in favor of Bloom

**Issue:** The document never explicitly considers Tulving — it simply doesn't use him. But Tulving's episodic/semantic/procedural distinction is biologically grounded in distinct memory systems:

- **Episodic memory** (hippocampus-dependent): personally experienced events, temporally tagged, "what happened when" — directly analogous to memesis's session observations. Hippocampal-dependent; susceptible to interference; benefits from consolidation (see F6 on consolidation).
- **Semantic memory** (neocortex, slower consolidation): context-free facts and concepts — analogous to memesis's `domain_knowledge`, `conceptual`, `factual`.
- **Procedural memory** (basal ganglia/cerebellum): skill-based, action sequences — analogous to `workflow_pattern`, `procedural`.

Three of Bloom's four types map almost perfectly to Tulving's three systems. Bloom's fourth type (metacognitive) has no Tulving equivalent, which is arguably a gap — but metacognitive observations in memesis (self-observations, correction of own errors) may deserve a separate treatment regardless of which framework is primary.

The biological grounding matters for memory system design: Tulving predicts different decay rates for different memory types (episodic is more volatile; semantic is more durable), different consolidation pathways, and different interference patterns. Bloom predicts nothing about any of these — it was never designed to.

See "Better metaphor: Tulving vs Bloom" section below for full argument.

---

### F6: "Consolidation" Terminology Misuse  Severity: MINOR
Section: §1 (Stage 2 — "Consolidation"), throughout

**Issue:** Biological memory consolidation (McGaugh 2000, *Science* 287:248-251) refers to the **synaptic and systems consolidation process** by which labile memories are stabilized: initially in the hippocampus (hours-days), then transferred to neocortex during sleep for long-term storage (weeks-months). Consolidation is a biological process that transforms memory traces — it is not editing, merging, pruning, or reclassifying.

The memesis "consolidator" does: KEEP/PRUNE/PROMOTE gating, deduplication, enrichment with `subject`/`work_event` fields, and linking via `reinforces`/`contradicts`. This is **memory editing**, not consolidation. The closest cognitive analog is **elaborative rehearsal** (Craik & Lockhart 1972, levels-of-processing framework) — the process of adding meaning and connections to a memory trace, which improves retention. Or it could simply be called **memory curation**.

This is a naming issue, not a functional one. The code works regardless of what it's called. But "consolidation" in a cognitive-science-citing document implies a specific process (sleep-dependent hippocampal transfer to neocortex) that is quite different from what the consolidator does.

**Cognitive science basis:** McGaugh 2000; Stickgold 2005 (*Nature Reviews Neuroscience* 6:219-228) on sleep-dependent consolidation.

**Recommendation:** Rename to `curator`, `refiner`, or `elaborator`. If "consolidation" is kept for engineering reasons (the module already exists), add a comment clarifying: "Despite the name, this is not biological consolidation — it's elaborative curation: gating, enrichment, and linking of extracted observations."

---

### F7: Salience Tiers Create Cliff Effects at Arbitrary Boundaries  Severity: MINOR
Section: §9, tier assignment table

**Issue:** Human memory strength is a continuous variable — there are no discrete tiers in neural systems. The tier boundaries (0.4, 0.7, 0.9) are operationally convenient but introduce cliff effects:

- A memory with `importance=0.399` gets `τ=12h`
- A memory with `importance=0.401` gets `τ=48h`
- The actual difference in importance is 0.002, but decay rate differs by 4×

The doc partially acknowledges this: "Tiers exist for UI bucketing + explainability... The actual scoring is continuous via the activation formula below. Tier just sets τ." But the activation formula uses `decay_tau_hours` set discretely by tier — so the continuity claim is undermined by the implementation. The continuous importance score is converted to a discrete τ value, then recombined multiplicatively in the activation formula. The cliff is real.

**Cognitive science basis:** Anderson 1983 ACT-R treats activation as a continuous real-valued quantity with no discrete thresholds. Ebbinghaus's forgetting curve is continuous. No neuroscientific model of memory uses discrete salience tiers.

**Recommendation:** Either (A) compute τ continuously from importance (e.g., `τ = τ_min × (importance)^{-k}` for some `k`, giving smooth variation), or (B) keep discrete tiers but document explicitly that they are an engineering approximation for explainability, not a model of memory. The "explainability" justification in the doc is valid; the cognitive-science framing is not.

---

### F8: Spreading Activation Mis-labeling  Severity: MINOR
Section: §9, "Access reinforcement"

**Issue:** The doc labels `on_access(memory)` as "Park's spreading activation" and describes incrementing a single memory's access count. This is not spreading activation.

Collins & Loftus 1975 (*Psychological Review* 82:407-428) defines spreading activation as: when a concept node is activated, activation **spreads along semantic links to neighboring nodes**, decaying with path length. The result is that accessing one memory primes related memories — measurable as faster retrieval of semantically related items.

The doc then mentions optionally propagating "a fractional access boost along `linked_observation_ids[]` edges (A-MEM-style)" — that is spreading activation. But it's presented as optional and secondary to the non-spreading `access_count++`.

Park et al. 2023 do not use the term "spreading activation" in the Generative Agents paper — they use "recency" as a score component updated at retrieval time. The attribution is doubly incorrect.

**Recommendation:** Rename `on_access()` to "recency reinforcement" or "access reinforcement." If `linked_observation_ids[]` propagation is implemented, call that "spreading activation" correctly. Consider making linked-node propagation non-optional — it is the feature that gives the Zettelkasten graph actual cognitive value, and its real analog (Collins & Loftus) suggests it should be primary, not optional.

---

## What human memory actually does that the doc doesn't capture

1. **Interference effects.** Human forgetting is not just decay — it's heavily driven by interference from similar memories (proactive and retroactive interference: McGeoch 1932, Underwood 1957). The doc treats all memories as independently decaying. In reality, a large volume of similar `domain_knowledge` memories will interfere with each other, degrading retrieval precision in ways the activation formula won't predict. The `linked_observation_ids[]` + `contradicts` field is a partial proxy for interference detection, but it's not modeled in the retrieval scoring.

2. **Context-dependent retrieval.** Tulving & Thomson 1973 (*Psychological Review*): retrieval is most effective when the encoding context matches the retrieval context. The `cwd` field captures spatial context — good. But temporal context (what was happening in the session, what project phase) and emotional/motivational state are not captured. This is a real limitation for retrieving memories across projects with different contexts.

3. **Schema-driven encoding (Bartlett 1932 / Rumelhart 1980).** The doc's `linked_observation_ids[]` implicitly builds memory schemata — interconnected networks of related observations that give meaning to new ones. Bartlett (*Remembering*, 1932) showed that memory is reconstructive, not reproductive — people remember the gist as filtered through existing schemata, not verbatim content. This matters for memesis: when the consolidation prompt "reinforces" or "contradicts" an existing memory, it is performing schema assimilation/accommodation in Piagetian terms, or Bartlett-style reconstruction. The doc would benefit from acknowledging this explicitly, because it implies the linked-graph traversal is not just a retrieval optimization — it is the mechanism that makes memories meaningful in context.

4. **Encoding specificity and the generation effect.** Slamecka & Graf 1978: memories that are actively generated (vs. passively received) are retained better. The Stage 2 consolidation prompt asks the LLM to actively generate `subtitle`, `subject`, `linked_observation_ids[]` — this is actually a good analog of the generation effect and increases the likelihood that the stored memory will be retrievable. This is an unacknowledged strength of the design.

5. **The fan effect (Anderson 1974, ACT-R).** More associations from a concept node → slower retrieval time for any individual association. The `linked_observation_ids[]` graph has no mechanism to counteract fan degradation — a heavily-linked observation will be retrieved more slowly (or with more noise in the vector space) as its neighbor count grows. ACT-R's spreading activation formula explicitly accounts for fan: `W_i / n_i` where `n_i` is the fan count. The doc's optional propagation step has no such normalization.

6. **Forgetting as adaptive (Anderson & Schooler 1991, *Psychological Science*).** This is a key finding the doc misses. Anderson & Schooler showed that human forgetting is not a failure — the forgetting curve tracks the environmental probability of needing a memory. You forget things at the rate they become unlikely to be needed again, given the pattern of prior accesses. The memesis pruning policy treats forgetting as purely cost-driven (remove low-activation memories to save space). The cognitive-science view suggests pruning policy should be calibrated to actual re-access probability, not just activation score — memories that haven't been accessed in 30 days but were accessed heavily 6 months ago (perhaps during a completed project) follow a different environmental probability curve than memories never accessed after creation.

---

## Better metaphor: Tulving vs Bloom (revisit)

**The case for switching to Tulving:**

Tulving's episodic/semantic/procedural distinction maps directly to the function of memories in a developer assistant context:

- *Episodic*: "In the session on 2026-04-15, Emma decided to use Peewee over SQLAlchemy because of deployment simplicity." This is a temporally tagged, contextually bound event memory — exactly what Stage 1 extracts. Episodic memories are volatile, context-sensitive, and benefit from consolidation into semantic form.
- *Semantic*: "Peewee is preferred over SQLAlchemy for single-user local deployments." This is the consolidated, context-free factual form of the episodic memory above. Semantic memories are more stable and more transferable.
- *Procedural*: "Always call `_resolve_db_path` before `init_db`." This is action-sequence knowledge that is accessed differently (procedural retrieval is often implicit and triggered by context).

The Stage 1 → Stage 2 consolidation pipeline actually maps beautifully onto **episodic-to-semantic consolidation**: Stage 1 captures raw episodic observations tied to a session; Stage 2 promotes them to context-free semantic form. This is biologically the most important memory transition. Naming it correctly would improve the architecture's self-documentation.

The procedural type from Bloom maps exactly to Tulving's procedural system, so nothing is lost.

The only Bloom type without a Tulving equivalent is **metacognitive** — self-knowledge about one's own memory, reasoning, and errors. Tulving's model doesn't have this because biological metacognition is handled by prefrontal systems rather than a separate memory system. For a machine memory system, metacognitive observations (corrections, self-observations) are arguably the most important category and deserve first-class treatment regardless of which framework is primary.

**The case for keeping Bloom:**

Bloom is more widely legible to software developers than Tulving. The four terms (factual/conceptual/procedural/metacognitive) are more self-explanatory than (episodic/semantic/procedural). The metacognitive category is a genuine strength of Bloom for this use case. The existing back-derivation map in §4 already maps Stage 2 types to Bloom knowledge types cleanly.

**Verdict:**

Neither framework was designed for machine memory classification. Tulving is more biologically grounded and the episodic→semantic pipeline maps cleanly onto the Stage 1→Stage 2 architecture, which is genuinely valuable. Bloom is more legible and the four-way partition is workable. A hybrid is defensible: use Tulving's terminology in architectural documentation (to accurately describe the consolidation pipeline as episodic-to-semantic promotion), while keeping Bloom's four types as the classification vocabulary in the `knowledge_type` field (for LLM and user legibility). Drop the claim that Bloom provides empirical validation for the classification task — it doesn't.

---

## Recommended terminology fixes

| Current term | Incorrect/imprecise because | Recommended replacement |
|---|---|---|
| "ACT-R-style activation formula" | The formula is exponential (Ebbinghaus), not ACT-R (power-law) | "Ebbinghaus-style activation with log-access reinforcement (cf. MemoryBank/Zhong 2023)" |
| `decay_tau_hours` called "half-life (τ)" | τ is a time constant (time to 1/e ≈ 37%), not half-life (time to 50%). Half-life = τ·ln(2). | Either rename to `decay_time_constant_hours` or explicitly compute `half_life_hours = tau * ln(2)` |
| "consolidation" (Stage 2 module name) | Biological consolidation is hippocampal→neocortical transfer during sleep; not editing/gating/linking | `curator`, `refiner`, or `elaborator`; or keep name with clarifying comment |
| "spreading activation" (on `on_access()`) | Spreading activation propagates through a graph to neighbors; incrementing one node's count is not spreading | "recency reinforcement" or "access reinforcement"; reserve "spreading activation" for the linked-node propagation |
| "50+ years of education research validates Bloom-Revised" | That research validated Bloom for instructional design, not memory system classification | "Bloom-Revised provides convenient vocabulary for knowledge type classification; not empirically validated for this use case" |
| `salience_tier` "Half-life (τ)" column header | τ is not half-life (see above) | "Decay time constant (τ)" |
| Test comment: "e^-1 = 0.368, half-life sanity" | 0.368 is the time-constant check, not the half-life check (half-life check expects 0.5) | "e^-1 = 0.368, time-constant sanity (decay to 37% at t=τ)" |
