# LLM Engineer Review: TAXONOMY-AND-DEFERRED-PATTERNS.md

## TL;DR

The schema proposal is architecturally coherent but contains two blockers that will cause silent data corruption in production: `linked_observation_ids[]` is physically impossible to populate at Stage 1 (where it's required), and the four-axis classification burden (kind + subject + work_event + knowledge_type) will produce high noise at consolidation time because the axes aren't independent enough for an LLM to resolve consistently. The activation formula in §9 invents a third approach that doesn't match Park 2023 or MemoryBank and has a miscalibrated half-life that will cause useful T2 memories to fully decay in 25h, not 7 days as documented.

---

## Findings

### F1: `linked_observation_ids[]` is structurally impossible at Stage 1  Severity: BLOCKER
Section: §3, §8 (item 9)

**Issue:** The schema lists `linked_observation_ids[]` as a top-level field on the unified JSON schema, and §8 item 9 says "Stage 2 prompt: ask LLM to identify which existing memories (from manifest)..." — implying this is Stage 2 only. But the schema in §3 is presented as the unified schema for both stages. Stage 1 (`OBSERVATION_EXTRACT_PROMPT`) runs during the 15-minute cron over a transcript slice. It has no memory manifest. It cannot emit valid UUIDs for memories it has never seen. If the field is in the Stage 1 output schema, one of three things happens: (a) LLM hallucinates UUIDs, (b) LLM emits `[]` always (field is vestigial), or (c) LLM emits `null` and the schema validator rejects it if the field is marked required.

**LLM-failure-mode example:**
```json
{
  "kind": "finding",
  "subject": "system",
  "linked_observation_ids": ["a3f2b1c4-..."]
}
```
That UUID doesn't exist. If validation is strict, you get a rejection loop. If it's lenient, you silently store a dangling graph edge that corrupts Zettelkasten traversal later.

**Recommendation:** Mark `linked_observation_ids[]` as Stage 2 only. Remove it from the Stage 1 output schema entirely. In Stage 2, provide the manifest as context and ask for link candidates by semantic similarity. Add explicit fallback: "if no close match, emit `[]`."

---

### F2: Four-axis simultaneous classification will collapse under LLM variance  Severity: BLOCKER
Section: §3

**Issue:** The proposal asks the LLM to simultaneously emit `kind` (6 values) + `subject` (6 values) + `work_event` (7 values + null) + `knowledge_type` (4 values) in a single generation pass. That's 6×6×8×4 = 1,152 valid combinations. The axes aren't independent. "How a system works" is `knowledge_type=conceptual`, `subject=system`, `kind=finding` — but it's also plausibly `knowledge_type=procedural` (if it describes a sequence), `subject=domain` (if it's general technical knowledge), `kind=constraint` (if it limits design choices). The LLM will resolve this ambiguity inconsistently across sessions.

**LLM-failure-mode example:** Take this real observation: "EventBus uses copy-on-write snapshot arrays on the publish path for thread safety."

- Axis 1 kind: `finding` (something learned) OR `constraint` (threading constraint going forward)
- Axis 2 subject: `system` (about the codebase) OR `domain` (technical pattern applicable elsewhere)
- Axis 3 work_event: `null` (no discrete action) OR `discovery` (it was discovered)
- Axis 4 knowledge_type: `conceptual` (mechanism) OR `procedural` (how to use it safely)

An LLM in Session A might emit `{finding, system, null, conceptual}`. In Session B: `{constraint, domain, discovery, procedural}`. Both are defensible. Your retrieval filters will miss half your corpus depending on the query.

**Recommendation:** Reduce to a **two-axis minimum for Stage 2**: `kind` (required, 6 values) + `knowledge_type` (required, 4 values). Make `subject` and `work_event` optional with explicit null defaults. Add a prompt-level tiebreaker rule per axis (see §Prompt Engineering Recommendations). Measure per-axis agreement rate across 20 observations before adding more axes.

---

### F3: Activation formula half-life is miscalibrated by ~7× for T2  Severity: MAJOR
Section: §9

**Issue:** The formula is `recency = exp(-age_hrs / decay_tau_hours)`. T2 `decay_tau_hours = 168`. At age = 168h (7 days), recency = `e^-1 = 0.368`. That's not half-life — that's the 1/e point. Half-life is `τ × ln(2) = 168 × 0.693 = 116h = 4.8 days`. The table says T2 half-life is "7d" but it's actually 4.8d. For T3 (48h τ), half-life is 33h, not 2d. For T4 (12h τ), half-life is 8.3h, not 12h.

More importantly: the pruning threshold `activation < 0.05` fires when `importance × recency × access_boost < 0.05`. A T2 memory with `importance=0.75` and `access_count=0` (access_boost=1) hits the threshold when `recency < 0.0667`, i.e., `age > -168 × ln(0.0667) = 168 × 2.71 = 455h ≈ 19 days`. That's broadly fine. But a T3 memory with `importance=0.5` and `access_count=0` prunes at `recency < 0.1`, i.e., `age > -48 × ln(0.1) = 48 × 2.3 = 110h ≈ 4.6 days`. That's aggressive — a 4-day-old useful finding gets auto-pruned. The doc says T3 half-life is "2d" which would make it worse.

**LLM-failure-mode example:** Not an LLM issue — this is a math error in the spec. But it produces system behavior the doc claims not to: "T3 (useful context)" memories disappear faster than stated, surprising users.

**Recommendation:** Fix the table to say τ not half-life, or use `τ = half_life / ln(2)` and recalculate: T2 τ = 240h (10d), T3 τ = 69h (2.9d), T4 τ = 17h. Or switch to explicit half-life formula: `recency = 0.5^(age_hrs / half_life_hrs)`. This is equivalent but makes the calibration transparent.

---

### F4: Park 2023 importance scoring is cited but not used  Severity: MAJOR
Section: §3, §9

**Issue:** The doc cites Park et al. 2023 (Generative Agents) to justify the retrieval formula, but Park's actual mechanism is fundamentally different from what's implemented here. In Park 2023, **importance is not assigned at extraction time** — it comes from a separate reflection pass where the LLM asks "On a scale of 1–10, how important is this memory?" after aggregating a stream of observations. The LLM's reflection importance is a derived signal, not an initial annotation. The extraction just creates raw observations. Importance emerges from reflection, which can re-weight memories as context accumulates.

What this doc proposes is extraction-time importance scoring (Stage 1 anchors, Stage 2 passthrough), which is closer to MemoryBank's model than Park's. That's not wrong, but it means:
1. Importance is frozen at the moment of extraction, not updated as the memory's relevance evolves
2. The citation is misleading — you're not implementing Park's model

**Recommendation:** Either (a) add a reflection pass that re-scores `importance` after N sessions using the Park mechanism — ask the LLM "given the last 10 sessions, how important is this memory now?" — or (b) remove the Park citation and cite MemoryBank instead for the importance anchors. If (a) is deferred, mark it explicitly as a known divergence from Park's actual mechanism, not just "future work."

---

### F5: Skip protocol `{"skip": true}` vs empty array `[]` — migration path missing  Severity: MAJOR
Section: §5 ("skip_summary as First-Class Signal")

**Issue:** The doc correctly identifies that `[]` is ambiguous (intentional skip vs. parse failure). The proposed fix is to switch Stage 1 to emit `{"skipped": true, "reason": "..."}` instead of `[]`. But `transcript_ingest.py` currently expects either a valid JSON array or a logged parse failure — it's not documented to handle a dict response. If you change the prompt before patching the ingest script, all "skipped" sessions will throw a JSON type error (list expected, dict received), silently killing observations.

**LLM-failure-mode example:** The LLM, during a boring session with no signal, now correctly emits `{"skipped": true, "reason": "no new observations"}`. The ingest script runs `observations = json.loads(response)` then iterates over it as a list, or calls `len(observations)`. Type error. Session silently dropped. You now have *more* silent drops than before, not fewer.

**Recommendation:** Patch `transcript_ingest.py` first to handle both formats before changing the prompt. Or: keep the `[]` behavior for production and implement skip signal only in the fail-fast parser path (§5 XML validator). The skip signal is only valuable when you have a dashboard that shows extraction gaps — otherwise `len([]) == 0` is sufficient.

---

### F6: Schema validation fail-fast rule in §5 not applied to new fields  Severity: MAJOR
Section: §5 vs §3

**Issue:** §5 correctly endorses the claude-mem fail-fast parser philosophy: "no coercion, no silent passthrough, no lenient mode." But §3's schema has `kind` as a required field with 6 enum values. What happens when the LLM returns `kind: "observation"` (hallucinated) or `kind: null` (declined to classify)?

The doc doesn't specify:
- Is `kind` required or optional at Stage 1?
- Is `knowledge_type` required or optional?
- What is the rejection/fallback behavior for invalid enum values?

The current system (`json.loads()` at ingest) silently accepts any string for `mode`. After the rename to `kind`, it will silently accept any string for `kind` too. The fail-fast philosophy is endorsed but not applied to the new schema.

**LLM-failure-mode example:**
```json
{"kind": "insight", "subject": "system", "knowledge_type": "factual", ...}
```
`"insight"` is not a valid `kind`. Current behavior: silently stored, breaks any filter that queries `kind IN (decision, finding, preference, constraint, correction, open_question)`. The memory is orphaned from all type-based retrieval.

**Recommendation:** Add a Pydantic (or dataclass + validator) schema layer at ingest boundary. Invalid enum values should either (a) fail-fast with a logged rejection and no DB write, or (b) be stored with `kind=null` and flagged for review. Do not silently coerce. Write the validator before writing the new prompts.

---

### F7: Bloom-Revised `knowledge_type` has semantic overlap that LLMs resolve inconsistently  Severity: MAJOR
Section: §3

**Issue:** "How a system works" — the doc's own framing — is ambiguous between `conceptual` and `procedural` depending on framing. The Bloom-Revised framework is validated for *human learning outcomes*, not for LLM classification of third-person observations. The four categories are not mutually exclusive for memory content:

- "EventBus uses copy-on-write" → conceptual (mechanism) or factual (discrete architectural fact)?
- "always call `_resolve_db_path` before `init_db`" → procedural (how-to sequence), but also metacognitive if the LLM frames it as "I tend to forget this"
- "Emma rejects CSS grid in favor of fixed-width panels" → factual (specific preference fact) or metacognitive (self-knowledge about aesthetic patterns)?

The ambiguity is highest at the factual/conceptual boundary and the procedural/metacognitive boundary — which are the most common observation types in a code session.

**LLM-failure-mode example:** Across 20 observations of architectural facts, an LLM will use `factual` vs `conceptual` roughly 60/40 or 40/60 depending on how the instruction is phrased. This means `knowledge_type` as a retrieval filter will only catch ~60% of target observations.

**Recommendation:** Add disambiguation examples to the prompt. For each pair that overlaps, provide one positive and one negative example. Specifically: "factual = discrete, specific, lookup-able fact; conceptual = generalized principle or mechanism that explains multiple facts. If in doubt, prefer factual." Also: add a `knowledge_type_confidence: low|high` field and only use `knowledge_type` as a filter for `high` confidence classifications. Deferred implementation cost: one extra enum field.

---

### F8: Token budget for new schema is significant and not analyzed  Severity: MINOR
Section: §3, §8

**Issue:** The unified schema adds approximately 12 new fields vs. the current 4-field Stage 1 output. Rough token count per observation (conservative estimate):
- Current: ~80 tokens (content, mode, importance, tags)
- Proposed: ~200–280 tokens (adding kind, subject, work_event, knowledge_type, linked_observation_ids[], facts[], cwd, title, subtitle, salience_tier, decay_tau_hours, etc.)

At Stage 1's current 0–3 observations per session slice, this is 0–840 tokens of output per slice vs. 0–240 tokens today. That's a 3.5× increase in output token cost, plus the larger input prompt needed to explain all the new fields and their rules. With a 15-minute cron and high-activity sessions generating many slices, the cost compounds.

The doc mentions token budget zero times. For a memory system that runs continuously, this matters.

**Recommendation:** Profile actual token costs against the 15-minute cron cadence. Prioritize fields: `kind`, `importance`, `facts[]`, `knowledge_type` are the high-value additions. `subtitle`, `title`, `work_event`, and `subject` can be Stage 2 only (cheaper, runs hourly). Don't add all fields to both stages simultaneously.

---

### F9: Mode system deferral is incorrect for current usage  Severity: MINOR
Section: §5 ("Mode System")

**Issue:** The deferral rationale is "memesis has one audience (software developers) and one session type (code sessions)." Based on the user profile in the CLAUDE.md context, the user uses memesis across writing/research sessions (not just code). The author's own memory file (`MEMORY.md`) references project context for a space game (Sector), design decisions, and architectural discussions — none of which are cleanly "code sessions."

The current single-mode taxonomy (`decision | finding | preference | constraint | correction | open_question`) is genre-neutral enough to handle non-code sessions, but `work_event` (bugfix/feature/refactor/etc.) is pure code vocabulary. Applying it to a writing or design session produces either `null` for every observation (field is vestigial) or hallucinated code-action labels.

**Recommendation:** Either (a) make `work_event` truly optional with null as the expected default for non-code observations — which the doc technically does say — or (b) promote mode system to near-term work given the actual usage pattern. The minimal viable mode system is just a `session_type: code | writing | research` field on the Memory, not a full `plugin/modes/*.json` implementation.

---

### F10: `self_reflection.py` surface area not updated  Severity: MINOR
Section: Not addressed

**Issue:** The doc rewrites Stage 1 and Stage 2 prompts but doesn't mention `core/self_reflection.py`. Self-reflection prompts presumably reference the existing observation schema (`mode`, `observation_type`, `concept_tags`). After the W5 migration, `concept_tags` is removed, `mode` becomes `kind`, and `observation_type` gains the legacy label. The self-reflection prompt will be querying fields that no longer exist or have changed semantics.

**Recommendation:** Add `self_reflection.py` prompt update to the W5 pull-list as item 11 (before the salience items). Mark it as a required change, not optional.

---

## Prompt Engineering Recommendations

**1. Kind disambiguation tiebreaker:**
Add to Stage 1 and Stage 2 prompts after listing kind values:
> "If the observation describes something that was learned, use `finding`. Reserve `decision` for observations where a choice was explicitly made between alternatives with rationale. Reserve `constraint` for observations that restrict future choices. When in doubt: `finding`."

This reduces the decision/finding/constraint ambiguity that's most likely to produce noise.

**2. Knowledge_type anchor examples (both stages):**
> "factual: 'memesis consolidator runs hourly via consolidate_cron.py' — a specific, lookup-able fact.
> conceptual: 'EventBus uses copy-on-write snapshots for thread safety' — a mechanism that explains behavior.
> procedural: 'always call _resolve_db_path before init_db' — a step-by-step rule to follow.
> metacognitive: 'I tend to over-engineer retrieval before measuring miss rate' — self-knowledge about patterns or tendencies.
> If the fact could be both factual AND conceptual, prefer factual."

**3. Subject null-default for code observations:**
> "For observations about code, infrastructure, or tool behavior, use `subject=system` as the default. Only use `user` for observations explicitly about the developer's preferences, personality, or work style. Only use `self` for observations about the AI's own behavior or errors."

This reduces the system/domain ambiguity for the majority of code-session observations.

**4. work_event null contract:**
> "Set `work_event` to `null` unless the observation traces directly to a discrete code action in this session (a bug was fixed, a feature was implemented, etc.). Most preference, constraint, and correction observations should have `work_event=null`."

This prevents spurious `discovery` labels on non-event observations.

**5. facts[] attribution enforcement (Stage 1):**
> "Each fact must begin with a named subject. Not: 'prefers explicit types.' Yes: 'Emma prefers explicit type annotations in all new C# code.' Not: 'stores auth tokens in Redis.' Yes: 'sector project stores auth tokens in Redis with 24h TTL.' No pronouns — each fact must be self-contained."

**6. High-density session cap strategy:**
At high session density (>30 tool uses per slice), the current 0–3 cap may produce systematic under-extraction with the expanded schema. Recommendation: add a `density_hint` injected by the ingest script:
> "This slice contains [N] tool uses. Prioritize observations with importance ≥ 0.7. Emit at most 3, fewest 0."

This prevents prompt confusion about whether the 0–3 cap is per-slice or per-session.

---

## Comparison to Production Systems

**vs. Letta (MemGPT lineage):** Letta uses a core memory / archival memory split with explicit in-context editing. The proposed system has no equivalent of core memory — all memories are archival. The `[PRIORITY]` prefix convention is a weak analog but not architecturally equivalent. Letta's strength is that the LLM *manages its own context window* — memesis's batch cron doesn't allow this. The live observer deferral means you're permanently in archival-only mode.

**vs. Mem0:** Mem0's extraction is single-axis (fact extraction with entity resolution), no multi-axis classification. It avoids the consistency problem entirely by making memories atomic facts rather than typed observations. The proposed system is closer to Mem0 than to Letta, but adds the classification overhead that Mem0 deliberately omits. Mem0 compensates with vector-only retrieval; the proposed system adds Zettelkasten traversal (A-MEM) which is legitimately better for long-horizon QA.

**vs. A-MEM (Xu 2024):** A-MEM's empirical advantage comes from the linking graph, not from the taxonomy. A-MEM doesn't use Bloom-Revised at all — it uses LLM-emergent keywords. The paper shows that emergent specificity beats closed-tag taxonomies. This contradicts the doc's rationale for `knowledge_type`. The Zettelkasten linking (`linked_observation_ids[]`) is the right A-MEM borrow; the Bloom-Revised taxonomy is not what A-MEM recommends.

**vs. MemoryBank (Zhong 2023):** The Ebbinghaus forgetting curve is cited in §9 but not actually implemented. The formula in §9 is exponential decay (Park model), not `R = e^(-t/S)` with access-boosted S (MemoryBank model). MemoryBank's key innovation is that **access strengthens S**, making the forgetting curve slower over time for frequently accessed memories. The proposed `access_boost = 1 + log(1 + access_count)` is an approximation of this but is applied multiplicatively to the final score rather than to the decay constant — a different behavioral shape.

**vs. headroom:** The proposal borrows headroom's atomic fact format and importance anchors — both good borrows. Headroom's mandatory WHO attribution in facts is stronger than the optional "try to name the subject" guidance the proposal implies. Headroom's entity/relationship extraction (§6) is correctly deferred given the effort required.

**Non-trivial divergences:**
- The three-axis classification (kind + subject + work_event) has no direct analog in any of these systems. It's the doc's most novel contribution. This novelty is also the source of the LLM consistency risk (F2).
- The activation formula blends Park recency + ACT-R access reinforcement + entroly tiers. This hybrid is undocumented in any production system reviewed. That's not inherently bad — but it means you have no empirical baseline for the calibration values.

---

## What I'd Actually Do Differently

**1. Reduce Stage 1 to 3 fields + system-injected fields.**
Stage 1 output: `kind` (required, 6 values), `importance` (required, 0.0–1.0), `facts[]` (required, 0–5 atomic facts). Everything else — subject, work_event, knowledge_type, linked_observation_ids, subtitle — is Stage 2 only. Stage 1 is the hot path (every 15 min). Keep it cheap and high-precision on the fields that matter most.

**2. Add a Stage 2 schema validator before touching the prompts.**
Write a Pydantic schema for the new unified Memory structure. Run it against the current Stage 2 output to measure the baseline rejection rate. Then change the prompts and compare rejection rates. If the new prompts produce higher rejection rates, you've regressed. Without the validator, you have no signal.

**3. Implement linked_observation_ids[] as a Stage 2 post-processing step, not a prompted field.**
Instead of asking the LLM to emit UUIDs, run a post-consolidation similarity search against the existing Memory table, take the top-3 cosine matches above threshold 0.85, and auto-populate `linked_observation_ids[]`. The LLM doesn't need to know about other memories to generate links — your retrieval system does. This is cheaper, more reliable, and sidesteps the Stage 1 impossibility entirely.

**4. Replace Bloom-Revised with a two-value split if you need knowledge typing.**
`knowledge_type: factual | non-factual` is 95% as useful as the four-way Bloom split and produces near-perfect LLM consistency. The non-factual category covers procedural, conceptual, and metacognitive — which are hard to distinguish in practice anyway. Add the four-way split only after you have agreement-rate data.

**5. Add a Park-style reflection pass before the activation model.**
Run a nightly reflection prompt: take the 20 most recent observations + the 10 oldest active memories + the 5 most frequently accessed memories. Ask: "Which of these are most important given recent session patterns? Re-score importance 0.0–1.0." This is 1 LLM call/day and gives you dynamic importance rather than the frozen extraction-time score. It also means your activation formula works with better inputs.

**6. Ship the skip signal and validator before shipping the new schema.**
The fail-fast philosophy in §5 is correct but applied in the wrong order. Make the parser robust first (validator + skip signal). Then expand the schema. Expanding the schema without a validator first means you'll ship new failure modes alongside new features.
