# Panel Consensus Ranking — Memesis W5

Three reviewers: **DS** (senior data scientist, retrieval/IR), **LLME** (senior LLM engineer, production agentic-memory), **NS** (cognitive neuroscientist, memory systems).

Source: 28 items across convergent findings C1–C7, DS-unique F3/F8/F9/F10, LLME-unique F5/F6/F8/F9/F10, NS-unique F5/F6/F7/F8, NS missing-cog-sci MM1–MM5, open decisions OD-A through OD-D.

---

## Debate (28 items)

---

### Convergent Findings

---

### C6: Half-life math error — τ vs ln(2)·τ

**DS:** This is a pure math fix. The table says "half-life (τ)" but exp(-t/τ) hits 1/e at t=τ, not 0.5. Actual half-life is τ·ln(2). The test comment calling 0.368 a "half-life sanity check" is also wrong — that's the time-constant check. Two-line fix in the tier table and one-line fix in the test. Zero design ambiguity. Do it immediately.

**LLME:** The cascading operational impact is bigger than it looks: T2 "7-day half-life" is actually 4.85-day half-life, and the pruning threshold fires ~30% earlier than documented. Ship this mislabeled and users will observe memories disappearing faster than spec says. Same priority as any spec-vs-behavior divergence: fix before it ships. Effort: XS, just the table, the formula comment, and the one test comment.

**NS:** This one is clear-cut. τ is a time constant, not a half-life — standard physics, standard exponential decay, no ambiguity in any memory model. The test comment "e^-1 = 0.368, half-life sanity" teaches the wrong thing. Fix it. If you want explicit half-life semantics, switch to `recency = 0.5^(age/half_life_hrs)` and be done with it. That formula is more legible anyway.

**Verdict:** Unanimous fix. Rename table column to "Time constant (τ)" and fix test comment, or switch to explicit half-life formula. No design debate.
**Impact:** HIGH (spec-vs-behavior divergence, operational semantics wrong)
**Effort:** XS (< 1 hr)
**Ratio:** 10

---

### LLME-F6: Schema validator fail-fast not applied to new schema

**LLME:** This is the single most leveraged change in the entire plan. Every subsequent schema field added (kind, knowledge_type, linked_observation_ids, facts[]) can silently corrupt the DB if invalid enum values are stored without validation. The §5 philosophy says "no coercion, no silent passthrough" — but none of the new fields enforce this. A Pydantic model (or even a dataclass with __post_init__ checks) at the ingest boundary takes 2-3 hours to write and blocks an entire class of future bugs. Write the validator before writing any new prompts.

**DS:** Agreed. The measurement infrastructure I want (baseline retrieval logging) should be layered on top of a validated schema, not alongside an unvalidated one. If I'm logging `kind` values to analyze filter effectiveness and half the entries have `kind="insight"` from LLM hallucinations, my baseline data is garbage. Validator first, logging second, schema changes third.

**NS:** From my perspective, schema integrity is a precondition for the empirical work we want to do. You can't measure knowledge_type classification consistency if invalid values are silently accepted. No position on Pydantic vs dataclass — that's OD-C. But the validator needs to exist before the new fields ship.

**Verdict:** Write the validator before any prompt changes. Blocks all downstream schema work if skipped.
**Impact:** HIGH
**Effort:** S (1-4 hr)
**Ratio:** 9

---

### LLME-F5: Skip protocol migration path missing

**LLME:** If someone changes the Stage 1 prompt to emit `{"skipped": true}` before patching `transcript_ingest.py`, every boring session becomes a silent drop. The fix is sequencing: patch the ingest to handle both formats before the prompt changes. Or keep `[]` and only add the skip signal at the parser layer. Either way, the ordering constraint is the blocker. This is a 30-minute code change to `transcript_ingest.py`.

**DS:** I'd actually prefer keeping `[]` for now — the skip signal is only valuable once there's a dashboard showing extraction gaps, which we don't have. The `len([]) == 0` heuristic is fine until then. But if we're adding it, fix the ingest first — I agree on ordering.

**LLME:** Fair. My recommendation: patch ingest to handle both formats (15 minutes), then the prompt change is unblocked and safe. Even if we keep `[]` for now, the ingest is now forward-compatible.

**NS:** No strong view on this one. The biological framing doesn't apply. The sequencing argument is correct.

**Verdict:** Patch transcript_ingest.py to handle both `[]` and `{"skipped": true}` before any prompt changes touch skip behavior. 15-minute change, prevents a silent-drop regression.
**Impact:** HIGH (prevents regression)
**Effort:** XS (< 1 hr)
**Ratio:** 10

---

### C1: Activation formula misrepresentation

**DS:** The formula `importance × exp(-Δt/τ) × (1 + log(1 + access_count))` cites ACT-R, Park 2023, and MemoryBank, but matches none of them. ACT-R is power-law, Park is additive, MemoryBank modifies decay rate not final score. My concern isn't primarily accuracy theater — it's that the formula is an invented hybrid with no calibration anchor. If the formula is wrong empirically (which we can't know without a baseline), we have no cited model to fall back to for recalibration. Fix: either pick Park's additive formula (simplest, tested on a retrieval task) or call this what it is — a heuristic — and list the tuning ranges. The ACT-R citation should be dropped unless you implement actual ACT-R (which requires storing full access_timestamps[]).

**LLME:** The practical failure mode here is the multiplicative combination. With additive scoring (Park), a very old memory still contributes its importance weight to retrieval. With multiplicative, an old memory with near-zero recency gets activation ≈ 0 regardless of importance or access count. Whether that's the desired behavior is a product decision, but it should be a deliberate one, not an accidental consequence of misattributing Park. Fix the attribution; make the multiplicative-vs-additive choice explicitly.

**NS:** The ACT-R misrepresentation is the most technically egregious item in the doc. You cite the formula (`B = ln(Σ t_j^-d)`) correctly in prose, then immediately implement something entirely different. Power-law decay (ACT-R) and exponential decay (Ebbinghaus) have different forgetting-curve shapes — power-law falls fast early and slowly later, exponential falls at constant rate. If you actually care which one models developer memory better, you'd need empirical data. Until you have that, pick Ebbinghaus/MemoryBank (which is what you're implementing) and cite it correctly.

**Verdict:** Fix attribution to "Ebbinghaus-style exponential with log-access reinforcement (cf. MemoryBank/Zhong 2023)." Remove ACT-R citation from the formula. Document the multiplicative-vs-additive choice as a deliberate design decision with stated rationale, not an implicit Park implementation.
**Impact:** HIGH (wrong formula cited, wrong operational behavior expected)
**Effort:** S (fix prose + formula comments + optionally switch to Park's additive; rewrite ~20 lines of doc + 5 lines of code if formula changes)
**Ratio:** 9

---

### C3: linked_observation_ids[] LLM-emitted is unreliable

**LLME:** This is a structural impossibility at Stage 1 (no manifest) and a hallucination risk at Stage 2. The right solution is to not prompt the LLM for UUIDs at all — run a post-consolidation cosine similarity search, take the top-3 above threshold, validate the UUIDs are real, and auto-populate. This is cheaper, more reliable, and gives you a tunable threshold (OD-D). The LLM generates content; the retrieval system generates links.

**DS:** Agreed on the implementation path. My additional concern: even with cosine-based linking, we need to validate link quality empirically before wiring it into retrieval scoring. The A-MEM results are compelling but were measured on a controlled benchmark. Implement the cosine linking (it's straightforward), but treat the linked-graph traversal as a Phase 2 retrieval feature — ship the schema field in W5, populate links via cosine, but don't weight them in retrieval ranking until you have spot-audit data on link quality.

**NS:** From the cognitive science angle: spreading activation through a linked graph (Collins & Loftus 1975) requires that the links are semantically valid — activation propagates through meaningful associations, not spurious cosine neighbors. If cosine similarity links two memories that happen to share vocabulary but aren't semantically related (a common IR failure mode), graph traversal will spread activation in the wrong direction and degrade retrieval. Threshold selection (OD-D) matters a lot. I'd recommend 0.88–0.90 to start — high precision, accept lower recall — and audit link validity before lowering.

**Verdict:** Generate links via cosine similarity post-processing (top-3 above 0.88), validate UUIDs exist, do not ask LLM to emit UUIDs. Schema field ships in W5; graph traversal in retrieval deferred to W6 pending spot-audit.
**Impact:** HIGH (prevents dangling references, enables future graph traversal)
**Effort:** S (1-4 hr — cosine search post-processing step in consolidator)
**Ratio:** 9

---

### C4: No baseline measurement / no eval plan

**DS:** This is the one I feel most strongly about. The entire document optimizes a system that has never been measured. I want 2 weeks of instrumentation before any schema changes: log query text, returned memory IDs, and an acceptance signal (was the injected memory referenced in the next session?). That's 50–100 lines in the retrieval path and the CLAUDE.md hook. Without it, we're tuning knobs on a black box. Every "improvement" claim — taxonomy changes, activation-based retrieval, Zettelkasten linking — is untestable without a baseline.

**LLME:** I'd rather ship the validator (F6) before the measurement infra, since garbage-in-garbage-out applies to the baseline data too. But conceptually I agree: you can't A/B the new schema without knowing what A (current) looks like. The measurement overhead is low — a logging decorator on the retrieval function and a structured log line per consolidation. One sprint task, not a milestone.

**NS:** The DS's 5-phase eval plan is thorough but the measurement prerequisites are the right priority order: validator first, then measurement, then calibration, then schema changes. The fleiss-kappa test for knowledge_type consistency is also part of this — you need to run that before deciding whether Bloom-Revised is reliable enough to use as a retrieval filter.

**Verdict:** Instrument retrieval path and consolidator for baseline logging before schema changes. Fleiss-kappa for kind + knowledge_type classification consistency is a required gate before those fields are used as retrieval filters.
**Impact:** HIGH (enables all downstream eval)
**Effort:** S (1-4 hr — logging decorators + kappa test script)
**Ratio:** 9

---

### DS-F10: facts[] attribution not validator-enforced

**DS:** This is 5 lines. The WHO/WHAT/WHEN/WHERE contract is enforced only by prompt instruction, which LLMs violate under compression. A parse-time check that rejects facts starting with pronouns (`he/she/it/they/we/i/this/the`) catches the most common violation pattern. Soft rejection (log and flag, not hard fail) is appropriate — don't drop the whole observation because one fact has a pronoun, but flag it for quality monitoring.

**LLME:** Agreed — this belongs inside the validator we're building anyway (F6). Once you have the Pydantic schema, add a `@validator` on `facts` that checks `[0].split()[0].lower() not in PRONOUN_SET`. Net code delta: 3 lines inside the validator class. No separate effort.

**NS:** Headroom's implementation (mandatory WHO attribution) is stronger than "try to name the subject." The gap is real — pronoun-starting facts lose attribution and become useless after context changes. Five lines, do it.

**Verdict:** Bundle into the F6 validator. Pronoun-check on facts[0] as a soft validation flag, not hard reject.
**Impact:** MEDIUM (data quality, affects long-term recall fidelity)
**Effort:** XS (bundled into validator)
**Ratio:** 9

---

### C2: Bloom-Revised over-claim

**DS:** The over-claim is a problem for two reasons. First, LLM inter-annotator agreement on factual vs. conceptual is ~60/40, not deterministic — Krathwohl 2002 says trained educators struggle with this distinction for lesson plans; an LLM classifying unstructured session observations will be no better. Second, using an unreliable field as a hard retrieval filter will cause systematic miss rates. Fix: keep the vocabulary (it's genuinely useful), drop the claim, add `knowledge_type_confidence: low|high` and only use as a filter for `high` confidence. Measure fleiss-kappa before treating it as reliable.

**LLME:** The over-claim in the doc is also misleading to contributors: "50k citations validate this for memory classification" is simply false. But the practical fix is the same — add a confidence field, run the kappa test, don't use as a hard filter until kappa ≥ 0.6. The vocabulary is fine. LLME's alternative suggestion (factual|non-factual as a 2-way split with near-perfect consistency) is worth considering as a fallback if kappa is low.

**NS:** The Bloom-Revised taxonomy was designed for instructional design, not machine memory classification. Using it to classify third-person observations about a codebase is a domain transfer that the citation doesn't support. That said, the four-type vocabulary is intuitive and the one-line definitions in the doc are clear. The fix is honest framing: "borrowed from Bloom-Revised as a convenient partition; not empirically validated for this use case." Tulving's three-way split (episodic/semantic/procedural) is more grounded but less legible — the Bloom vocabulary is pragmatically fine if the academic overclaim is removed.

**Verdict:** Drop the "50 years validates this" language. Add `knowledge_type_confidence`. Run fleiss-kappa gate before using as a retrieval filter. Keep the four-type vocabulary.
**Impact:** MEDIUM (prevents over-reliance on unreliable filter; honesty in academic framing)
**Effort:** XS (drop 2 sentences from doc, add one field to schema)
**Ratio:** 9

---

### LLME-F10: self_reflection.py not updated

**LLME:** This is a straightforward but high-blast-radius omission. `self_reflection.py` references `mode`, `observation_type`, and `concept_tags` in its prompts. After the W5 schema changes, those fields either don't exist or have changed semantics. The reflection pass will query the wrong fields and silently produce degraded or wrong output. Add it to the W5 pull-list as a required item.

**DS:** Agreed. If self_reflection.py is surfacing memory quality signals or re-scoring importance, it needs to understand the new schema. Running a reflection pass against a renamed-but-not-updated schema is worse than not running it — it produces confident-sounding garbage. Required, not optional.

**NS:** No neuroscience angle. Just: update all code that touches the schema when the schema changes. This is maintenance discipline.

**Verdict:** Add self_reflection.py prompt update to W5 pull-list. Must happen before W5 ships.
**Impact:** HIGH (prevents silent regression in reflection path)
**Effort:** S (1-4 hr — prompt update + schema reference fixes)
**Ratio:** 9

---

### C5: Multi-axis cardinality + LLM consistency risk

**DS:** The 6×6×8×4 = 1,152 combination space is not inherently the problem — the problem is that the axes aren't independent, so LLMs will produce correlated noise across them. If `kind=finding` always co-occurs with `subject=domain` in practice, you've added cardinality without adding discriminating power. The fix I'd push for: ship `kind` as the only required classification axis in Stage 2. Measure how often `kind`-based filtering outperforms no-filter. Then add `knowledge_type` if the data supports it.

**LLME:** My failure-mode evidence is concrete: the EventBus example (finding/constraint × system/domain × null/discovery × conceptual/procedural) shows the axes interact in ambiguous ways. Requiring all four simultaneously will produce inconsistent multi-axis labels. Minimum viable: `kind` (required) + `knowledge_type` (required) + the others as nullable optional. Stage 1 should only emit `kind` + `knowledge_type` (plus system-injected fields). Subject and work_event are Stage 2 optional.

**NS:** From a memory science perspective, orthogonality is the key property. If two axes are correlated in practice, they're not genuinely orthogonal and one of them is redundant. The doc claims the axes are orthogonal conceptually — they probably are conceptually — but orthogonality in LLM output is an empirical question, not a logical one. Measure the cross-axis correlation on 50 observations before committing to all four axes.

**Verdict:** Stage 1: `kind` + `knowledge_type` only (required). Stage 2: adds `subject` and `work_event` as optional nullable. No hard multi-axis filtering until cross-axis agreement is measured.
**Impact:** HIGH (prevents retrieval fragmentation from inconsistent multi-axis labels)
**Effort:** S (prompt simplification + schema optionality changes)
**Ratio:** 9

---

### C7: Importance set once, never updated

**DS:** This is the single point of failure for the entire decay/tier/pruning pipeline. LLM over-assignment bias toward 0.7–0.9 will collapse the tier distribution — almost everything becomes T2, and the tier system loses discriminating power. Fix: Stage 2 re-scores importance independently (it has more context — full consolidation buffer, existing memories, KEEP/PRUNE/PROMOTE reasoning). Preserve Stage 1 score as `raw_importance` for audit. Monitor the Stage 2 vs. Stage 1 distribution. If median Stage 2 importance > 0.65, tighten prompt anchors.

**LLME:** I'd add: defer the activation-based pruning entirely until the importance calibration audit (Spearman ρ ≥ 0.6 vs. human re-score) passes. If importance is noise, the decay model is noise. Stage 2 re-scoring is a one-line prompt change and a column rename — low effort, high leverage.

**NS:** Park's reflection pass is the right longer-term solution: periodically ask "given recent sessions, how important is this memory now?" That's dynamic importance, not frozen extraction-time importance. Stage 2 re-scoring is a reasonable W5 step toward that. The importance calibration audit the DS recommends is exactly what would validate whether Stage 2 does better than Stage 1.

**Verdict:** Stage 2 re-scores importance independently. Stage 1 score preserved as `raw_importance`. Defer activation-based pruning until importance calibration ρ ≥ 0.6.
**Impact:** HIGH (prevents collapse of tier distribution)
**Effort:** S (one-line prompt change + column rename + audit script)
**Ratio:** 9

---

### LLME-F8: Token budget 3.5× increase not analyzed

**LLME:** 80 tokens per observation → 200–280 tokens after all the new fields. At 3 observations per slice, 15-minute cron, the output cost compounds. The fix is architectural: keep Stage 1 lean (kind, knowledge_type, importance, facts[], cwd — system injected). Move subtitle, work_event, subject to Stage 2 only (hourly, not 15-minute). This also reduces Stage 1 classification burden (C5). Two wins for the same sequencing decision.

**DS:** The cost analysis matters less to me than the classification quality argument, but LLME is right that the same fix (Stage 1 lean, Stage 2 enriches) addresses both issues. From a measurement perspective, cheaper Stage 1 calls also mean less cost per observation in the baseline logging period.

**NS:** No direct cog-sci angle, but the Stage 1 / Stage 2 split maps to something real: Stage 1 is episodic capture (raw, fast, minimal classification overhead) and Stage 2 is semantic elaboration (richer, slower, more context). Keeping Stage 1 lean is architecturally aligned with how biological episodic encoding works — you encode fast during an event; you consolidate and enrich later.

**Verdict:** Stage 1 emits: kind, knowledge_type, knowledge_type_confidence, importance, facts[], cwd (system-injected). Everything else is Stage 2 only.
**Impact:** MEDIUM (cost control + classification quality)
**Effort:** XS (sequencing decision, already implied by C5 fix)
**Ratio:** 9

---

### DS-F3: Pruning threshold 0.05 uncalibrated — T3 prunes in 4.6 days

**DS:** A T3 memory (τ=48h, importance=0.5) hits activation < 0.05 at ~110 hours = 4.6 days. That's too aggressive for any "useful context" memory. The threshold is not derived from anything. Fix: simulate the pruning policy on the existing corpus before enabling it. Shadow-prune (log what would be pruned, don't delete) for 2 weeks. Set false-prune target at < 5% (re-creation within 30 days). Only then enable destructive pruning.

**LLME:** The threshold issue is compounded by the half-life math error (C6) — the actual prune timing is faster than even the uncalibrated threshold implies. Fix the math first, then simulate, then set the threshold. Defer destructive pruning to W6. This is not a W5 item.

**NS:** Adaptive forgetting (Anderson & Schooler 1991) argues pruning policy should be calibrated to the environmental re-access probability, not just an activation cutoff. A project-specific domain memory that hasn't been accessed in 2 weeks might be perfectly appropriate to keep if the project is in a "dormant" phase. The 4.6-day prune window is especially dangerous for slow-domain knowledge about completed subprojects. Shadow-prune plus re-creation analysis is the right empirical approach.

**Verdict:** Do not enable destructive pruning in W5. Shadow-prune (log only) for 2 weeks. Set threshold after analyzing false-prune rate on existing corpus. Defer to W6.
**Impact:** HIGH (prevents irreversible data loss)
**Effort:** S (shadow-prune logging, no destructive changes — actual fix is deferral)
**Ratio:** 9

---

### LLME-F9: Mode system deferral wrong — user does writing/research too

**LLME:** The deferral rationale is "one audience, one session type (code)." This is factually wrong given the user profile — the CLAUDE.md and MEMORY.md show design discussions, architectural decisions for a game, documentation work. None of those are cleanly "code sessions" by the work_event vocabulary (bugfix/feature/refactor). Result: `work_event` will be `null` for most non-code observations, or the LLM will hallucinate code-action labels. The minimal fix: make `work_event` explicitly and truly optional with `null` as the expected default for most observations, and update the deferral rationale.

**DS:** The bigger concern from my angle: if `work_event` is vestigial for 60% of observations, it's not worth the LLM classification overhead. LLME's minimal-mode-system suggestion (`session_type: code | writing | research` field) is better — it's one field, not a full mode system, and it gives retrieval a useful filter without requiring an overhaul.

**NS:** The mode system deferral in §5 describes it as "one session type" — but memory research (Tulving's encoding-specificity principle) suggests that retrieval is context-sensitive. A memory about game design is harder to retrieve in a code-context session, and vice versa. A `session_type` field would encode the retrieval context at extraction time, which actually improves cross-context recall. So this is both a practical fix and a cognitively grounded one.

**Verdict:** Add `session_type: code | writing | research | null` as a nullable system-injected or Stage 1 field. Mark `work_event` as explicitly null-default for non-code sessions with clear prompt guidance. Update deferral rationale to reflect actual usage.
**Impact:** MEDIUM (prevents vestigial/hallucinated work_event labels; adds useful retrieval context)
**Effort:** S (one field + prompt update)
**Ratio:** 8

---

### DS-F8: Tier boundaries 0.4/0.7/0.9 cliff effects vs LLM noise ±0.1

**DS:** A memory at importance=0.699 gets τ=48h; one at 0.700 gets τ=168h — a 3.5× difference from a 0.001 delta. LLM importance scoring is noisy to ±0.1. This means T2/T3 tier membership is effectively random near the boundary. The fix is a continuous τ function: `τ(i) = τ_min × (τ_max/τ_min)^i` maps [0,1] → [12h, 720h] smoothly. Or add ±0.05 hysteresis at each boundary. Either way, 10 lines of code.

**LLME:** Continuous τ is cleaner but slightly harder to explain to users ("why is this memory's half-life 86h instead of 48h?"). The hysteresis approach (boundary at 0.70 applies only if previous tier was T2 and new importance ≥ 0.65, else stays T3) is more legible but more code. I'd take the continuous function — simpler implementation, better mathematical properties.

**NS:** ACT-R treats activation as a continuous real-valued quantity with no discrete thresholds. Ebbinghaus's forgetting curve is continuous. The discrete tiers are an engineering approximation for UI explainability — the doc acknowledges this. The continuous τ approach is more aligned with how memory strength actually works. However, this is only important if the decay model ships (which I'd argue it shouldn't until W6). If pruning is deferred to W6 (per DS-F3 recommendation), this cliff-effect fix can wait for W6 as well.

**DS:** Fair — if the decay machinery is deferred to W6, this fix belongs there too. Bundle it.

**Verdict:** Defer to W6 alongside the pruning machinery. Document the continuous τ function in the W6 spec so it's not forgotten.
**Impact:** MEDIUM (prevents cliff-effect tier assignment)
**Effort:** S (10 lines when implemented)
**Ratio:** 7 (deferred, so effective W5 effort = 0)

---

### DS-F9: open_question has no lifecycle

**DS:** `open_question` observations are introduced in Stage 1 but have no Stage 2 counterpart — they either get silently dropped or accumulate indefinitely with no resolution mechanism. Open questions are high-value: they represent unresolved issues that should be surfaced at the start of subsequent sessions. Fix: first-class type, pinned (no decay), surfaced in session context injection, resolvable by Stage 1 detecting a `correction` or `finding` on the same topic.

**LLME:** The silent drop is the worst outcome. At minimum, add `open_question` to the Stage 2 enum and set the consolidation rule: KEEP open_questions with no decay, with a maximum of N active open_questions per project (else oldest gets promoted to `finding` or archived). The resolution trigger (new finding that addresses the same topic) is the harder part — it requires the Stage 2 consolidation pass to compare against existing open_questions, which adds prompt complexity.

**NS:** Unresolved questions are epistemically important — they represent knowledge gaps that affect subsequent reasoning. In memory research, the generation effect predicts that questions that get answered are retained better than facts presented without context. Building a first-class question lifecycle (ask → persist → answer) would leverage this. But the minimal fix (no-decay, surface in injection) is meaningful even without the resolution trigger.

**LLME:** On resolution: stage 2 already looks at the manifest and does reinforces/contradicts reasoning. Add a "resolves_question_id" field as an optional output — Stage 2 can check against the manifest's open_questions and emit a resolution link. That's one optional field, not a whole new subsystem.

**Verdict:** Add `open_question` to Stage 2 enum. Pinned (no decay). Surface in session injection. Add optional `resolves_question_id` field to Stage 2 output for resolution tracking. No full lifecycle needed in W5.
**Impact:** MEDIUM (prevents silent data loss; enables cross-session question tracking)
**Effort:** S (Stage 2 enum addition + one optional field + retrieval surfacing logic)
**Ratio:** 7

---

### OD-B: Fast-track minimal mode system (session_type field)

**LLME:** This is a direct consequence of LLME-F9. The minimal mode system is not a full `plugin/modes/*.json` implementation — it's one nullable enum field (`session_type: code | writing | research`) that gates whether `work_event` is expected to be non-null. Implementation: 30 minutes to add the field, 1 hour to update Stage 1 guidance to inject or ask for `session_type`. Worth doing now given actual usage.

**DS:** Agreed on the minimal version. This also gives us a useful retrieval filter from day one — cross-mode retrieval contamination (code memories surfacing in writing sessions) is a real quality problem once the store has multiple session types.

**NS:** The encoding specificity principle (Tulving & Thomson 1973) supports this directly: retrieval is most effective when encoding context matches retrieval context. `session_type` is a rough proxy for retrieval context. Even a 3-value enum provides meaningful context matching.

**Verdict:** Add `session_type: code | writing | research | null` as a system-injected or Stage 1-emitted field. Minimal implementation, addresses actual usage pattern.
**Impact:** MEDIUM
**Effort:** S (1-4 hr)
**Ratio:** 7

---

### OD-D: Cosine threshold for linked_observation_ids[] (0.80 / 0.85 / 0.90)

**DS:** No data to choose yet. Start at 0.88–0.90 (high precision, accept lower recall) and audit the first 100 link-generation runs. Lower the threshold only if the audit shows good precision at that range and you want more recall. Never set it below 0.80 without evidence — at 0.80 you'll start linking memories that share vocabulary but not meaning.

**LLME:** Start at 0.90 as the implementation default, parameterize it so it's easy to change. The cosine threshold is the main tuning knob for link quality — make it a config value, not a hard-coded float.

**NS:** Semantic distance in embedding space is noisy. Two memories that are genuinely related may have cosine similarity 0.75 (if one uses technical vocabulary and one uses natural language). Two memories that share a project name but are unrelated may have cosine similarity 0.88. The threshold choice is inherently empirical. Start high (0.90), measure precision on the first batch of links, then tune.

**Verdict:** Default to 0.90, parameterized. Audit first 100 link-generation runs before tuning. Do not implement before the validator and schema fields are in place.
**Impact:** MEDIUM (link quality determines graph traversal value)
**Effort:** XS (config value choice, deferred to post-W5)
**Ratio:** 7

---

### OD-C: Pydantic vs lightweight dataclass validator

**LLME:** Pydantic is the right choice if you're already using it or adjacent packages. The enum validation, field presence checks, and custom validators are concise and testable. If Pydantic is not already a dependency, a dataclass + `__post_init__` validator avoids adding a dependency for a ~100-line module. Either works. Decision: check `requirements.txt` — if Pydantic is already there, use it; otherwise, dataclass is fine.

**DS:** From an evaluation standpoint, I don't care which validator runs as long as it: (a) rejects invalid enum values, (b) flags pronoun-starting facts, (c) returns a structured result I can log. Both Pydantic and dataclass can do this. Ship whichever is already in the dep tree.

**NS:** No strong view. The validator exists for data quality — the implementation choice is engineering.

**Verdict:** Check existing dependencies. If Pydantic present, use it. If not, dataclass + __post_init__. Decision takes 2 minutes to make; don't block validator work on this open question.
**Impact:** LOW (implementation detail of a HIGH-impact item)
**Effort:** XS (decision, not implementation effort)
**Ratio:** 6

---

### OD-A: Tulving terminology in architecture docs

**NS:** The case for Tulving in architectural documentation is real: the Stage 1 → Stage 2 pipeline is episodic-to-semantic consolidation, which is biologically the most important memory transition. Naming it correctly in the docs helps contributors understand why the pipeline is designed as two stages with different output types. Tulving's three-way system also predicts different decay rates for episodic vs. semantic memories — which is actually relevant to the tier design. However, Bloom's four-type vocabulary is more legible for the `knowledge_type` field and should stay at the field level.

**DS:** I'm neutral on terminology. If it makes the docs more accurate and contributors understand the design intent better, sure. But this is documentation work with zero impact on retrieval quality. It belongs in the "do it when you're writing docs anyway" category, not in the sprint plan.

**LLME:** Same as DS. Zero production impact. If someone is writing the architectural doc anyway, add the Tulving framing. If not, it's fine to defer indefinitely. I'd rather the reviewer's time go toward the validator and measurement infrastructure.

**Verdict:** Use Tulving terminology in architectural documentation when rewriting (episodic→semantic framing for Stage 1→2 pipeline). Keep Bloom's four-type vocabulary at the field level. Do not block any implementation work on this.
**Impact:** LOW (documentation accuracy; no retrieval impact)
**Effort:** XS (one paragraph in architectural docs)
**Ratio:** 5

---

### NS-F5: Tulving (episodic→semantic) as Stage1→Stage2 metaphor

**NS:** This is the same as OD-A but as a finding rather than a decision. The episodic→semantic consolidation framing is genuinely accurate and worth adding to architectural docs. Stage 1 captures raw temporally-tagged events (episodic); Stage 2 promotes to context-free generalizations (semantic). This is the most important memory transition in human cognition. The current "consolidator" name implies biological consolidation (sleep-dependent hippocampal transfer), which is wrong. "Elaborator" or "semantic promoter" would be more accurate. But this is a naming issue with no functional impact.

**DS:** Documentation. Low priority. Bundle with OD-A when docs are being written.

**LLME:** Same. No production impact.

**Verdict:** Bundle with OD-A. Add to architectural docs when written; do not prioritize separately.
**Impact:** LOW
**Effort:** XS
**Ratio:** 5

---

### NS-F6: "Consolidation" terminology misnomer

**NS:** Biological consolidation is hippocampal→neocortical transfer during sleep (McGaugh 2000). The memesis "consolidator" does KEEP/PRUNE/PROMOTE gating, deduplication, and field enrichment — this is elaborative curation (Craik & Lockhart 1972), not consolidation. The term is misleading in a document that claims cognitive science grounding. Rename to `curator` or add a clarifying comment.

**DS:** Renaming a module is a non-trivial surface area — it touches every import, the CLI command if there is one, documentation. Unless the rename comes with other refactoring, the comment approach (add one clarifying line to the module docstring) is lower risk.

**LLME:** The module name `consolidator` is already used in `core/consolidator.py`, `hooks/pre_compact.py`, `hooks/consolidate_cron.py`, and `CONSOLIDATION_PROMPT`. Renaming all of those is medium effort. Add the clarifying comment for W5; schedule rename for a refactoring sprint if desired.

**Verdict:** Add one comment to `core/consolidator.py` docstring clarifying the divergence from biological consolidation. Rename is deferred as optional cleanup. No functional impact.
**Impact:** LOW
**Effort:** XS (one comment line)
**Ratio:** 4

---

### NS-F7: Salience tier cliff effects (concurs with DS-F8)

**NS:** Same root issue as DS-F8. Continuous τ is more aligned with how memory strength works in any model (ACT-R, Ebbinghaus, human). The discrete tiers are an engineering approximation. Already covered under DS-F8.

**DS:** Fully converges with DS-F8. Defer to W6 with the pruning machinery.

**LLME:** Same.

**Verdict:** Duplicate of DS-F8. Defer to W6. See DS-F8 verdict.
**Impact:** MEDIUM (same as DS-F8)
**Effort:** S (same as DS-F8, deferred to W6)
**Ratio:** 4 (deferred)

---

### NS-F8: "Spreading activation" mis-labeling

**NS:** `on_access(memory)` increments a single node's count. Collins & Loftus (1975) defines spreading activation as propagation through a graph to neighboring nodes. These are different things. Rename `on_access()` to `recency_reinforcement()`. If linked-node propagation is implemented (recommended, it's the actual spreading activation), call that "spreading activation." Currently it's listed as optional — it should be primary, not optional.

**DS:** Low functional impact — it's a method name. High documentation accuracy impact. The rename is trivial; whether to make linked-node propagation primary vs. optional is a product decision. I'd keep it optional for W5 (graph isn't validated yet) and document the naming correctly.

**LLME:** Rename is 2 minutes. Do it. The linked-node propagation should stay optional until the graph links are validated (per C3 verdict — cosine-based linking in W5, traversal in W6).

**Verdict:** Rename `on_access()` to `recency_reinforcement()`. Reserve "spreading activation" for linked-node propagation. Make linked-node propagation the intended primary path in comments, but keep it conditional on validated links.
**Impact:** LOW (naming accuracy)
**Effort:** XS (rename)
**Ratio:** 4

---

### NS-MM1: Interference effects (McGeoch 1932)

**NS:** High-volume similarity-clustered memories will interfere with each other, degrading retrieval precision in ways the activation formula won't predict. Proactive interference (old memories block recall of new ones) and retroactive interference (new memories block recall of old ones) are well-documented. The `linked_observation_ids[]` + `contradicts` field is a partial proxy for detecting contradictory memories, but interference from similar (not contradictory) memories is unmodeled. This is a real limitation when the memory store grows large.

**DS:** Interesting theoretical concern. Measurable eventually via precision@k degradation as corpus size grows. Not actionable in W5 without a baseline (which we don't have yet). Track it as a future investigation item.

**LLME:** No near-term production fix available that doesn't require significant redesign. Watch for it in retrieval quality metrics as the store grows. Not a W5 item.

**Verdict:** Document as a known limitation in the architecture docs. No W5 action. Track via retrieval quality metrics as corpus grows.
**Impact:** LOW (W5-actionable impact: 0)
**Effort:** XS (documentation note)
**Ratio:** 3

---

### NS-MM2: Adaptive forgetting (Anderson & Schooler 1991)

**NS:** Human forgetting tracks environmental probability of needing a memory — you forget things at the rate they become unlikely to be needed. A project memory that was heavily accessed 6 months ago on a completed project has a different re-access probability curve than a memory never accessed after creation. The activation-threshold pruning policy treats all memories symmetrically. Adaptive pruning would account for historical access patterns.

**DS:** This is the right model for W6+ pruning. Once we have baseline access logs (C4), we can analyze re-access probability by memory type, project, and age. The Anderson & Schooler model could replace the current threshold-based approach. Not actionable in W5.

**LLME:** Agreed. Deferred. But document it as the target model for W6 pruning so the W5 schema captures the data needed to implement it (access timestamps, last_accessed_at — already in the spec).

**Verdict:** Document as target model for W6 pruning policy. Ensure W5 schema captures access timestamps needed to implement it. No W5 code change.
**Impact:** LOW (W5-actionable: 0; W6 framing value: MEDIUM)
**Effort:** XS (documentation note)
**Ratio:** 3

---

### NS-MM3: Fan effect (Anderson 1974)

**NS:** Heavily-linked observation nodes will retrieve slower as their neighbor count grows. ACT-R explicitly normalizes by `1/n_links` (fan count) to counteract this. The `linked_observation_ids[]` graph has no such normalization. If a central architectural decision memory gets linked to 30 other memories, its effective activation in spreading-activation traversal will be diluted.

**DS:** Not actionable until we have the graph in production and can measure it. Track as a future optimization. The fan effect matters when average out-degree exceeds ~5–10 connections; at W5 scale (cosine threshold 0.90), the average out-degree will likely be 0–3.

**LLME:** Agree — not a W5 concern. At 0.90 cosine threshold, most memories will have 0–2 links. Fan effect becomes relevant at scale, not at launch.

**Verdict:** Note in architecture docs as a known limitation. No W5 action.
**Impact:** LOW
**Effort:** XS (documentation note)
**Ratio:** 2

---

### NS-MM4: Schema theory (Bartlett 1932)

**NS:** The `linked_observation_ids[]` graph implicitly builds memory schemata — interconnected networks that give meaning to new memories. Bartlett predicted that memory is reconstructive, not reproductive; people remember the gist filtered through existing schema. For memesis, this is an argument that the linked graph is not just a retrieval optimization — it's the mechanism that makes memories meaningful. Acknowledging this in the docs would improve architectural self-documentation.

**DS:** Interesting theoretical framing. No actionable code change. Documentation note at most.

**LLME:** Same. No production impact.

**Verdict:** Add a note in the linked_observation_ids[] section: the graph enables schema-based meaning-making, not just retrieval optimization (Bartlett 1932). Documentation only.
**Impact:** LOW
**Effort:** XS (one sentence)
**Ratio:** 2

---

### NS-MM5: Generation effect strength (Slamecka & Graf 1978)

**NS:** Memories actively generated are retained better than passively received ones. Stage 2 asking the LLM to generate `subtitle`, `subject`, `linked_observation_ids[]` is actually a strength of the design — it improves the likelihood that the stored memory will be retrievable. This is an unacknowledged strength worth calling out.

**DS:** Nice. Document it as a design rationale. No code change needed.

**LLME:** Same. No production impact. Worth noting in the design doc as a justification for having Stage 2 generate rather than just copy Stage 1 fields.

**Verdict:** Add a note citing Slamecka & Graf 1978 as supporting rationale for the Stage 2 generative enrichment approach. Documentation only.
**Impact:** LOW
**Effort:** XS (one sentence)
**Ratio:** 2

---

## Consensus Ranking (impact/effort, highest first)

Tie-breaking rule: when ratio is equal, rank the item with the smaller blast radius (fewer files changed, lower risk of regression) first.

| Rank | Item | Impact | Effort | Ratio | Verdict |
|------|------|--------|--------|-------|---------|
| 1 | C6: Half-life math error | HIGH | XS | 10 | Fix τ vs. ln(2)·τ confusion in table, formula comment, and test comment before anything ships |
| 2 | LLME-F5: Skip protocol migration path | HIGH | XS | 10 | Patch transcript_ingest.py to handle both [] and {"skipped": true} before prompt changes |
| 3 | LLME-F6: Schema validator fail-fast | HIGH | S | 9 | Write Pydantic/dataclass validator at ingest boundary before any new schema fields ship |
| 4 | DS-F10: facts[] attribution validator | HIGH | XS | 9 | Bundle pronoun-check into validator; 3 lines inside the validator being built for F6 |
| 5 | C2: Bloom-Revised over-claim | MEDIUM | XS | 9 | Drop "50 years validates this" language; add knowledge_type_confidence field |
| 6 | C1: Activation formula misrepresentation | HIGH | S | 9 | Recite as Ebbinghaus/MemoryBank; document multiplicative vs. additive as explicit design choice |
| 7 | C3: linked_observation_ids[] LLM-emitted unreliable | HIGH | S | 9 | Generate links via cosine post-processing (top-3 ≥ 0.90), validate UUIDs; no LLM UUID prompting |
| 8 | C4: No baseline / no eval plan | HIGH | S | 9 | Instrument retrieval + consolidator logging; run fleiss-kappa gate before deploying classification filters |
| 9 | LLME-F10: self_reflection.py not updated | HIGH | S | 9 | Add to W5 pull-list; prompt update for renamed/changed schema fields |
| 10 | C5: Multi-axis cardinality risk | HIGH | S | 9 | Stage 1: kind+knowledge_type only; subject+work_event Stage 2 optional |
| 11 | C7: Importance set once, never updated | HIGH | S | 9 | Stage 2 re-scores independently; preserve raw_importance; defer pruning until ρ ≥ 0.6 |
| 12 | LLME-F8: Token budget 3.5× increase | MEDIUM | XS | 9 | Stage 1 lean (kind, knowledge_type, importance, facts[], cwd); all else Stage 2 only |
| 13 | DS-F3: Pruning threshold 0.05 uncalibrated | HIGH | S | 9 | Shadow-prune only in W5; defer destructive pruning to W6 after corpus simulation |
| 14 | LLME-F9: Mode system deferral incorrect | MEDIUM | S | 8 | Add session_type: code\|writing\|research\|null; work_event explicitly null-default for non-code |
| 15 | DS-F8: Tier boundary cliff effects | MEDIUM | S | 7 | Defer to W6 with pruning machinery; document continuous τ function for W6 spec |
| 16 | DS-F9: open_question has no lifecycle | MEDIUM | S | 7 | Add to Stage 2 enum; pinned (no decay); surface in injection; optional resolves_question_id field |
| 17 | OD-B: Fast-track minimal mode system | MEDIUM | S | 7 | Same resolution as LLME-F9; add session_type field |
| 18 | OD-D: Cosine threshold for linked links | MEDIUM | XS | 7 | Default 0.90, parameterize; audit first 100 link runs before tuning |
| 19 | OD-C: Pydantic vs dataclass validator | LOW | XS | 6 | Check requirements.txt; pick whichever is already present; 2-minute decision |
| 20 | OD-A: Tulving terminology in architecture docs | LOW | XS | 5 | Add episodic→semantic framing to architectural prose; bundle with NS-F5 |
| 21 | NS-F5: Tulving as Stage1→Stage2 metaphor | LOW | XS | 5 | Bundle with OD-A; one paragraph in architectural docs |
| 22 | NS-F6: "Consolidation" terminology misnomer | LOW | XS | 4 | Add one clarifying comment to consolidator.py docstring; rename deferred |
| 23 | NS-F7: Tier cliff effects (concurs DS-F8) | MEDIUM | S | 4 | Duplicate of DS-F8; defer to W6 |
| 24 | NS-F8: "Spreading activation" mis-labeling | LOW | XS | 4 | Rename on_access() → recency_reinforcement(); 2-minute change |
| 25 | NS-MM1: Interference effects | LOW | XS | 3 | Document as known limitation; track via retrieval quality metrics |
| 26 | NS-MM2: Adaptive forgetting | LOW | XS | 3 | Document as target model for W6 pruning; ensure W5 schema captures access timestamps |
| 27 | NS-MM3: Fan effect | LOW | XS | 2 | Note in architecture docs; not actionable at W5 scale |
| 28 | NS-MM4: Schema theory (Bartlett) | LOW | XS | 2 | One sentence in linked_observation_ids[] documentation |
| — | NS-MM5: Generation effect (Slamecka & Graf) | LOW | XS | 2 | One sentence noting Stage 2 generative enrichment as design strength |

*Note: NS-MM5 is listed separately above but belongs at rank 29 (or merged with 28) — total 28 items plus this supporting note.*

---

## Recommended Sprint Cuts

### Sprint A — Do this week (ratio ≥ 9, plus ratio 10 items)

All 13 items at ratio 9–10. Combined effort: approximately 1.5–2 days of focused work.

1. **C6 (half-life math):** Fix tier table column header, formula comment, test comment. 30 minutes. (ratio 10)
2. **LLME-F5 (skip protocol migration):** Patch transcript_ingest.py to handle both `[]` and `{"skipped": true}`. 30 minutes. (ratio 10)
3. **LLME-F6 (schema validator):** Write Pydantic/dataclass validator at ingest boundary with enum validation for kind, knowledge_type, subject, work_event. This is the foundation all other schema work builds on. (ratio 9)
4. **DS-F10 (facts[] attribution):** Bundle pronoun-check into the F6 validator. 3 lines. (ratio 9)
5. **C2 (Bloom over-claim):** Remove the "50 years validates" language; add `knowledge_type_confidence: low | high` to schema. One sentence deleted, one field added. (ratio 9)
6. **C1 (activation formula attribution):** Fix prose attribution to "Ebbinghaus-style / MemoryBank"; document multiplicative vs. additive as a deliberate design choice, not a Park implementation. (ratio 9)
7. **C3 (linked_observation_ids cosine linking):** Implement post-consolidation cosine similarity search (top-3 ≥ 0.90, UUID validation). Schema field in Memory model, no LLM UUID prompting. (ratio 9)
8. **C4 (baseline measurement):** Add logging decorators to retrieval path and consolidator. Log query, returned IDs, acceptance signal. Write fleiss-kappa script for kind + knowledge_type. (ratio 9)
9. **LLME-F10 (self_reflection.py):** Update self_reflection.py prompt for renamed/changed fields (mode → kind, concept_tags removed). (ratio 9)
10. **C5 (multi-axis reduction):** Update Stage 1 output schema to kind + knowledge_type + knowledge_type_confidence only. Mark subject + work_event as Stage 2 optional. (ratio 9)
11. **C7 (importance re-scoring):** Add `raw_importance` field to Memory. Stage 2 re-scores importance independently. Defer pruning activation until ρ ≥ 0.6. (ratio 9)
12. **LLME-F8 (token budget):** Confirm field assignment: kind, knowledge_type, knowledge_type_confidence, importance, facts[], cwd to Stage 1; all else Stage 2 only. Costs 0 extra effort — it's the same decision as C5. (ratio 9)
13. **DS-F3 (pruning deferral):** Do not enable destructive pruning in W5. Add shadow-prune logging (compute activation, log what would be pruned, no delete). (ratio 9)

### Sprint B — Do next, ~2 weeks (ratio 7–8)

4 items. Roughly 1.5 days of work.

14. **LLME-F9 / OD-B (session_type field):** Add `session_type: code | writing | research | null` as a system-injected or Stage 1 field. Update work_event to be explicitly null-default for non-code sessions. Update deferral rationale in §5. (ratio 8)
15. **DS-F9 (open_question lifecycle):** Add `open_question` to Stage 2 enum. Set as pinned (no decay). Add surfacing in session context injection. Add optional `resolves_question_id` field to Stage 2 output. (ratio 7)
16. **OD-D (cosine threshold):** Set default `LINK_COSINE_THRESHOLD = 0.90` as a named constant. Parameterize. Document the first-100-consolidations audit plan. (ratio 7)
17. **OD-C (validator implementation choice):** Check requirements.txt; pick Pydantic or dataclass. 2-minute decision. (ratio 6)

### Sprint C — Later, when data justifies (ratio 4–5)

4 items. Total effort: ~2 hours when done.

18. **OD-A / NS-F5 (Tulving terminology):** Add episodic→semantic framing to architectural prose. Bundle with any documentation writing sprint. (ratio 5)
19. **NS-F6 (consolidation terminology):** Add one comment to consolidator.py docstring. (ratio 4)
20. **NS-F8 (spreading activation rename):** Rename `on_access()` → `recency_reinforcement()`. 2 minutes. (ratio 4)
21. **DS-F8 / NS-F7 (tier cliff effects):** Implement continuous τ function. Deferred to W6 with pruning machinery. (ratio 4)

### Skip / Watch (ratio ≤ 3)

7 items. No code action in W5 or W6. Document as known limitations or future investigation items.

22. **NS-MM1 (interference effects):** Document as known limitation; track via retrieval quality degradation curve as corpus grows.
23. **NS-MM2 (adaptive forgetting):** Document as W6+ pruning target model. Ensure W5 schema captures access timestamps.
24. **NS-MM3 (fan effect):** Note in linked_observation_ids[] documentation. Not actionable at W5 scale.
25. **NS-MM4 (schema theory / Bartlett):** One sentence in linked graph documentation.
26. **NS-MM5 (generation effect):** One sentence in Stage 2 design rationale.
27. **NS-F7 (cliff effects duplicate):** Fully covered by DS-F8 / Sprint C item 21.
28. **OD-A duplicate framing:** Fully covered by Sprint C item 18.

---

## Hard-Disagreement Items (panel split, no consensus reached)

### Multiplicative vs. Additive Activation Formula

This was debated in C1 and did not fully resolve.

**DS position:** If the formula is changed to be correct empirically, use Park's additive formula. The multiplicative form is unanchored — nothing predicts it will behave better for developer session memory than the simpler additive form.

**LLME position:** Multiplicative has the property that *all factors must be strong* for a memory to rank. That may actually be the right semantics for a memory injection system: you don't want to inject a very old memory (near-zero recency) even if it was once important and frequently accessed. Additive lets old memories re-enter the candidate pool on the strength of their importance alone. For injection purposes, multiplicative is defensible.

**NS position:** Neither formulation matches any cited paper. The choice should be a deliberate product decision with a stated rationale, not an inherited one. Additive is more theoretically grounded (Park, ACT-R components) but both are pragmatic approximations.

**Why no consensus:** DS wants empirical evidence before committing to either; LLME finds multiplicative semantically appropriate for the injection use case; NS refuses to endorse either without a clear statement that the choice is pragmatic rather than theoretically derived.

**Resolution path:** Make the choice explicit in the code comment ("multiplicative: requires all three factors strong for injection; additive: any strong factor can carry a memory — choose based on desired injection behavior"). Run a 4-week A/B test once the baseline is established (C4). The debate is not blocking Sprint A.

---

### Importance calibration gate for pruning

**DS position:** Defer all destructive pruning to W6 unconditionally until Spearman ρ ≥ 0.6 on importance calibration audit.

**LLME position:** Shadow-prune in W5, enable destructive pruning in W6 if the ρ gate passes. Agrees with the gate but wants shadow-prune in W5 to begin collecting false-prune rate data.

**NS position:** Agrees with deferral on principled grounds (importance is frozen extract-time; decay model built on noise). No objection to shadow-pruning if it doesn't risk data.

**Resolution:** Shadow-prune (log only, no delete) ships in W5. Destructive pruning ships in W6 if and only if ρ ≥ 0.6 AND false-prune rate simulation < 5%. This satisfied DS's "no destructive prune without validation" requirement and LLME's "collect data now" requirement. NS has no objection.

*This item reached resolution after second-pass debate; listed here for transparency since it required a third round.*
