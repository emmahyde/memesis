# Panel Review Synthesis — TAXONOMY-AND-DEFERRED-PATTERNS.md

Three independent reviewers (data scientist / LLM engineer / cognitive neuroscientist) audited the W5 plan. This document consolidates findings, ranks by consensus severity, and proposes a revised W5 scope.

Source reviews:
- `REVIEW-DATA-SCIENTIST.md` — 11 findings (2 BLOCKER, 7 MAJOR, 2 MINOR)
- `REVIEW-LLM-ENGINEER.md` — 10 findings (2 BLOCKER, 5 MAJOR, 3 MINOR)
- `REVIEW-NEUROSCIENTIST.md` — 8 findings (4 MAJOR, 4 MINOR)

---

## Convergent findings (raised by ≥2 reviewers — high confidence)

### C1: Activation formula does not match cited papers   Severity: BLOCKER (consensus)
Raised by: DS-F2, LLM-eng-F4, NS-F1, NS-F3

The proposed `importance × exp(-Δt/τ) × (1 + log(1 + accesses))` is attributed to ACT-R + Park 2023 + MemoryBank, but matches none of them:

- **ACT-R** uses power-law decay over a sum of access timestamps: `B = ln(Σ t_j^-d)` with `d ≈ 0.5`. Not exponential.
- **Park 2023** uses **additive** combination: `α·recency + β·importance + γ·relevance`. Not multiplicative. Multiplicative means importance=0 kills activation regardless of recency/access.
- **MemoryBank** boosts decay-strength `S` after access (modifies decay rate), doesn't multiply final score by an access term.

Plus Park's importance comes from a **reflection pass**, not extract-time annotation — extract-time importance is MemoryBank's model, not Park's.

**Fix:** Pick one model and implement it faithfully. Recommendation: implement the simpler Park formula (additive, with explicit weights) for ranking + MemoryBank-style decay-rate access boost. Drop the ACT-R citation entirely or implement actual ACT-R (requires storing access_timestamps[] list).

### C2: Bloom-Revised over-claim   Severity: MAJOR (consensus)
Raised by: DS-F5, LLM-eng-F7, NS-F4

Bloom-Revised was validated for **instructional design and learner assessment**, not for classifying machine memory observations. Krathwohl 2002 explicitly notes inter-rater reliability issues with the factual/conceptual distinction even among trained educators. LLM consistency on this distinction will be ~60/40, not deterministic.

**Fix:** Keep the 4-type vocabulary (factual/conceptual/procedural/metacognitive) as folk taxonomy. Drop the claim that 50+ years of education research validates it for memory classification. Add a `knowledge_type_confidence: low|high` field and only use as a hard filter for `high` confidence. Run inter-annotator agreement test (fleiss-kappa across 3 prompt runs on 50 observations) before treating as reliable retrieval feature.

### C3: `linked_observation_ids[]` LLM-emitted is unreliable   Severity: BLOCKER (LLM-eng) / MAJOR (DS)
Raised by: DS-F7, LLM-eng-F1

LLM cannot emit valid UUIDs for memories it hasn't seen. Stage 1 has no manifest — emitting links there is structurally impossible. Stage 2 has the manifest but link quality requires validation.

**Fix:** (a) Mark `linked_observation_ids[]` as Stage 2 only, never Stage 1. (b) Generate links via post-consolidation cosine-similarity search (top-3 above 0.85 threshold) — not LLM-prompted. The LLM doesn't need to know about other memories; the retrieval system does. This sidesteps both impossibility and hallucination.

### C4: No baseline measurement / no eval plan   Severity: BLOCKER (DS) / MAJOR (LLM-eng)
Raised by: DS-F1, LLM-eng "What I'd do differently #2"

The W5 plan optimizes a retrieval system whose current performance (precision@k, acceptance rate) was never measured. Every "improvement" claim is unanchored.

**Fix:** Ship measurement infrastructure first. Log queries + retrieved memories + acceptance signal for 2 weeks before any schema changes. Without this, W5 is blind optimization. DS provides full 5-phase eval plan in §"Concrete eval plan".

### C5: Multi-axis cardinality + LLM consistency risk   Severity: BLOCKER (LLM-eng) / MAJOR (DS)
Raised by: DS-F6, LLM-eng-F2

The 4-axis schema (`kind × subject × work_event × knowledge_type`) creates 6×6×8×4 = 1,152 combinations. LLMs will resolve axis ambiguity inconsistently — same observation gets different multi-axis labels in Session A vs Session B.

**Fix:** Reduce to 2-axis minimum for Stage 2: `kind` (required, 6 values) + `knowledge_type` (required, 4 values, with confidence flag). Make `subject` and `work_event` Stage 2 optional. Defer multi-axis filtering until inter-annotator agreement is measured. Start with `kind`-only filtering as the first retrieval feature.

### C6: Half-life math error   Severity: MAJOR (LLM-eng) / MAJOR (NS)
Raised by: LLM-eng-F3, NS-F2

The doc labels `decay_tau_hours` as "half-life" but the formula `exp(-Δt/τ)` gives 1/e ≈ 0.368 at t=τ, not 0.5. Actual half-life is `τ·ln(2)`. T2 stated as "7d half-life" is actually a 7d time constant = 4.85d half-life. Operational behavior diverges from documented behavior.

**Fix:** Either rename column to "Time constant (τ)" and remove "half-life" everywhere, OR switch formula to `recency = 0.5^(age_hrs / half_life_hrs)` and store `half_life_hours` directly. Recommendation: explicit half-life form — more legible.

### C7: Importance is set once, never updated   Severity: MAJOR (DS) / MAJOR (LLM-eng) / MINOR (NS)
Raised by: DS-F4, LLM-eng-F4

LLM-assigned extract-time importance is the single point of failure for the entire decay/tier/pruning pipeline. LLM bias toward 0.7-0.9 will systematically collapse tier distribution. Park's actual mechanism uses reflection-based importance that updates with context.

**Fix:** Stage 2 should re-score importance independently (it has more context). Add nightly reflection pass that re-scores top-N memories. Defer activation-based pruning until importance calibration audit shows ρ ≥ 0.6 vs human-rescored sample.

---

## Reviewer-unique findings (single-reviewer, also valid)

### DS-only findings worth acting on

- **DS-F3:** Pruning threshold 0.05 is uncalibrated. T3 importance=0.5 prunes at 4.6 days — too aggressive for slow-domain knowledge. **Fix:** simulate on existing corpus first; defer destructive pruning to W6 with shadow-prune validation.
- **DS-F8:** Tier boundaries (0.4/0.7/0.9) create cliff effects — LLM importance noise ±0.1 vs boundary precision 0.001. **Fix:** continuous τ function `τ(i) = 12 × exp(i × ln(60))` smooths to [12h, 720h], or hysteresis ±0.05.
- **DS-F9:** `open_question` has no lifecycle — silent drop or indefinite accumulation. **Fix:** first-class type, pinned (no decay), surfaced in session injection, resolvable by subsequent `correction` or `finding` on same topic.
- **DS-F10:** `facts[]` attribution contract (no pronouns, named subject) enforced by prompt only — no parse-time validator. **Fix:** 5-line check at consolidator boundary that rejects facts starting with `he/she/it/they/we/i/this/the`.

### LLM-eng-only findings worth acting on

- **LLM-eng-F5:** Skip protocol `{"skipped": true}` migration path missing. Changing prompt before patching `transcript_ingest.py` will break list-iteration on dict response. **Fix:** patch ingest first; or keep `[]` and only add skip-signal at the fail-fast parser layer.
- **LLM-eng-F6:** Schema validation fail-fast philosophy from §5 not applied to new schema. Invalid `kind` values silently stored. **Fix:** Pydantic validator at ingest boundary before any prompt changes ship.
- **LLM-eng-F8:** Token budget per observation: 80 tokens current → 200-280 tokens proposed (3.5× output cost). Not analyzed. **Fix:** put high-cardinality fields (subtitle, work_event, subject) in Stage 2 only — runs hourly, not 15-min.
- **LLM-eng-F9:** Mode system deferral incorrect — user uses memesis on writing/research not just code. `work_event` is pure code vocabulary. **Fix:** Either truly-optional `work_event` with null default, or add minimal `session_type: code | writing | research` field now.
- **LLM-eng-F10:** `core/self_reflection.py` not updated for new schema. **Fix:** add to W5 pull-list as required.

### NS-only findings worth acting on

- **NS-F5/Tulving:** Tulving's biologically-grounded episodic/semantic/procedural maps to Stage 1 → Stage 2 pipeline as **episodic-to-semantic consolidation** — biologically the most important memory transition. **Fix:** Use Tulving terminology in architectural docs to describe the pipeline; keep Bloom 4-type vocabulary at the field level. Hybrid framing is more accurate.
- **NS-F6:** "Consolidation" misnomer — biological consolidation is hippocampal→neocortical sleep transfer; memesis "consolidator" is elaborative curation. **Fix:** Document the divergence with one comment, OR rename module to `curator`. Deferred — naming change is larger surface area.
- **NS-F7:** Tier cliff effects acknowledged in doc but undermined by implementation (continuous importance → discrete τ). **Fix:** Same as DS-F8 (continuous τ).
- **NS-F8:** "Spreading activation" mis-labeling — `access_count++` is not spreading activation; propagation through `linked_observation_ids[]` IS. **Fix:** Rename `on_access()` to `recency_reinforcement()`. Make graph propagation primary not optional.

### NS missing-from-doc cognitive science the team should know

- **Interference effects** (McGeoch 1932) — high-similarity memory volume degrades retrieval. Currently unmodeled. Worth tracking via `linked_observation_ids[]` density.
- **Adaptive forgetting** (Anderson & Schooler 1991) — human forgetting tracks environmental probability of needing the memory, not just cost. Pruning policy should be calibrated to actual re-access probability.
- **Fan effect** (Anderson 1974) — heavily-linked node retrieves slower; ACT-R normalizes by `1/n_links`. Doc has no equivalent.
- **Schema theory** (Bartlett 1932 / Rumelhart 1980) — `linked_observation_ids[]` is implicit schema construction; Bartlett predicts this enables meaning-making, not just retrieval.
- **Generation effect** (Slamecka & Graf 1978) — LLM generating subtitles/links during consolidation is actually a strength of the design (active generation > passive storage). Worth noting.

---

## Revised W5 scope — based on panel consensus

### What ships in W5 (revised, sequenced)

**Phase 5.0 — Measurement (week 1, no schema changes):**
1. Instrument retrieval path: log query, retrieved memory IDs, acceptance signal (used in session vs ignored)
2. Instrument consolidator: log Stage 1 type, importance, KEEP/PRUNE/PROMOTE outcome, before/after counts
3. Run for 2 weeks. Compute baseline precision@5 and acceptance rate.

**Phase 5.1 — Schema validator (week 2, before any prompt changes):**
4. Pydantic schema for current Memory + planned new fields
5. Apply at ingest boundary; log rejection rate on current corpus
6. Patch `transcript_ingest.py` to handle both `[]` and `{"skipped": true}` formats

**Phase 5.2 — Calibration audits (week 3, no schema changes):**
7. Inter-annotator agreement on `kind`: 50 observations × 3 runs at temp=0.3 → fleiss-kappa
8. Inter-annotator agreement on `knowledge_type` same protocol
9. Importance calibration: 100 memories, human (or 2nd LLM) re-score, compute Spearman ρ
10. **Decision gate:** if any kappa < 0.6 or ρ < 0.6, revise prompt anchors before proceeding

**Phase 5.3 — Schema fields (week 4):**
11. Add fields to `Memory` model: `kind`, `knowledge_type`, `knowledge_type_confidence`, `facts[]`, `cwd`, `subtitle`, `linked_observation_ids[]`
12. Migration: back-derive `kind` from existing `mode`; back-derive `knowledge_type` from concept_tags collapse map; flag ambiguous rows
13. Drop `concept_tags[]` field + `CONCEPT_TAGS` dict (revert W2 partial)

**Phase 5.4 — Stage-1 prompt rewrite (week 5):**
14. Drop "0-3" cap, replace with quality gate language
15. Add verb-anchor list, type↔knowledge_type orthogonality note, anchor examples for each axis
16. Stage 1 emits: `kind` (required), `knowledge_type` (required), `knowledge_type_confidence` (required), `importance` (required), `facts[]` (0-5), `cwd` (required)
17. **NOT in Stage 1:** subject, work_event, linked_observation_ids, subtitle (all Stage 2 only)
18. Update `core/self_reflection.py` for new schema

**Phase 5.5 — Stage-2 prompt rewrite (week 5):**
19. Stage 2 receives Stage 1 fields + manifest, emits all + `subject`, `work_event` (nullable), `subtitle`
20. Stage 2 re-scores `importance` independently; preserve Stage 1 score as `raw_importance` for audit
21. Track Stage 2 vs Stage 1 importance distribution; alert if median drift > 0.15

**Phase 5.6 — Linked-graph (post-W5, depends on validation):**
22. Generate `linked_observation_ids[]` via cosine-similarity post-processing — top-3 above 0.85, validate UUIDs against manifest
23. Spot-audit 100 consolidations: relevance precision of generated links

### What moves to W6 (defer until W5 measurement provides evidence)

- **Salience tiers + decay + pruning** — entire §9 of original doc. Defer until:
  - Importance calibration ρ ≥ 0.6
  - Baseline retrieval precision known
  - Pruning simulated on full corpus with false-prune rate < 5%
- **Activation formula in retrieval ranking** — defer until baseline measured
- **Park-style reflection pass** for importance re-scoring — W6 with proper Park citation

### What changes in academic framing

- Drop ACT-R citation from formula (formula is Ebbinghaus/MemoryBank, cite accordingly)
- Reframe Bloom-Revised: "borrowed as convenient 4-way vocabulary; validation for memory-system use case is empirical TBD, not inherited from education research"
- Add Tulving (episodic→semantic) to architectural prose describing Stage 1→Stage 2 pipeline
- Fix half-life vs time constant terminology throughout
- Rename `on_access()` → `recency_reinforcement()`; reserve "spreading activation" for graph propagation
- Add note: "consolidation" is engineering-historical name; functionally this is elaborative curation (Craik & Lockhart 1972 framing)

### Open questions for user

1. **Tulving terminology in architecture docs?** Pro: more accurate, episodic→semantic Stage 1→2 mapping is genuinely useful. Con: less legible to non-cog-sci audience.
2. **Mode system fast-tracked?** LLM-eng-F9 says single-mode is wrong given user's writing/research usage. Add minimal `session_type` field now, or keep deferred?
3. **Pydantic vs lightweight dataclass validation?** Pydantic adds dependency. Dataclass + `__post_init__` validator is dependency-free but more code.
4. **Cosine threshold for `linked_observation_ids[]`?** Doc proposes 0.85. Could be 0.80 (more recall, more noise) or 0.90 (less recall, higher precision). No data yet to choose.
