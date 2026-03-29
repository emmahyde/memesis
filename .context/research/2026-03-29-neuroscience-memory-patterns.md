# Neuroscience and Behavioral Psychology Patterns for AI Memory System Design

**Date**: 2026-03-29
**Scope**: Eight neurobiological/behavioral patterns and their computational analogs for the memesis memory lifecycle system.
**Sources**: Wikipedia (Spreading Activation, Encoding Specificity, Memory Reconsolidation, Prospective Memory, Somatic Marker Hypothesis, Retrieval-Induced Forgetting, Spacing Effect, SuperMemo/SM-2); PMC 3768102 (hippocampal replay); Memory Consolidation (Wikipedia).

---

## 1. Hippocampal Replay and Memory Consolidation During Sleep

### Neurobiological Mechanism

During slow-wave sleep, the hippocampus re-fires sequences of place-cell activity that occurred during waking experience — at 10–20× compressed speed — in tight coupling with sharp-wave ripples (high-frequency oscillatory bursts). This "offline replay" drives a systems-consolidation dialogue between the hippocampus and neocortex: the hippocampus acts as a fast-write buffer that holds raw episodic traces; the neocortex is the slow-write permanent store. Over days-to-weeks, cortical representations become independent of the hippocampus.

Critically, replay is selective, not exhaustive. Neurons representing extensively explored or reward-associated environments show stronger reactivation. The prefrontal–hippocampal circuit may "tag" emotionally significant or goal-relevant experiences during encoding via theta-band phase coupling, predisposing them for offline consolidation. Replay activity peaks 20–40 minutes post-learning, then decays — though it can persist beyond 24 hours for high-salience experiences. REM sleep produces increased neuronal activity following novel or enriched waking experiences, suggesting novelty itself is a consolidation signal.

**Sources**: PMC 3768102; Wikipedia/Memory_consolidation.

### Computational Analog

The memesis consolidation pipeline — scan raw observations → reduce → consolidate → promote — is already structured as an "offline replay" process. The PreCompact hook fires when a session ends (analogous to sleep onset) and performs selective curation of the session's ephemeral buffer.

The missing element is *salience-weighted replay frequency*. Currently, all ephemeral observations pass through a single consolidation call with equal opportunity. Biologically, high-salience events are replayed more often before consolidation, strengthening their synaptic encoding.

### Design Recommendation

Introduce a `replay_priority` score to ephemeral observations at write time. Score inputs: correction events (+0.4 weight, corrections are the highest-signal observations per the taxonomy), user pushback/friction signals (+0.3), novelty relative to existing memories via FTS similarity (+0.2), and session recency (+0.1). During consolidation, present high-`replay_priority` observations to the LLM first and allocate more token budget to reasoning about them. Low-priority observations can be batch-summarized.

Concretely: modify `hooks/append_observation.py` to compute and store `replay_priority` in the ephemeral markdown frontmatter. Modify the consolidation prompt in `core/prompts.py` to sort observations by this score before presenting them to Claude, and instruct Claude to apply proportionally deeper analysis to top-ranked items.

### Expected Benefit

Better recall of high-signal observations (corrections, surprises, preference reversals) and less noise from low-signal routine observations. Reduces the risk that a week of mundane observations drowns out a single critical correction.

---

## 2. Spreading Activation Theory

### Neurobiological Mechanism

Spreading activation models how the brain retrieves semantically related concepts by propagating energy through an associative network. When "dog" is activated, edges to "bark," "pet," "leash," "walk" receive activation proportional to connection weight. As activation propagates outward, it decays — distant concepts receive attenuated signal. This explains semantic priming: subjects respond faster to "doctor" after "nurse" than after an unrelated word, because "nurse" pre-activated the "doctor" node.

The algorithm on directed, weighted graphs:
```
A[j] = A[j] + (A[i] × W[i,j] × decay_factor)
```
Termination occurs when no node exceeds the firing threshold. Key parameters: firing threshold (0–1), decay factor (0–1). Closely linked nodes activate strongly; distant nodes may not fire at all.

**Source**: Wikipedia/Spreading_activation.

### Computational Analog

The memesis system currently has two retrieval paths: FTS5 (keyword BM25 match) and vector cosine similarity via sqlite-vec. Neither implements multi-hop activation — they return direct matches but not second- and third-order associations.

The `NarrativeThread` / `ThreadMember` tables already encode a sparse semantic graph (memories linked by narrative proximity). This is an underutilized substrate for spreading activation.

### Design Recommendation

Implement a `spreading_activation_search(seed_memory_ids, hops=2, decay=0.6, threshold=0.15)` function in `core/retrieval.py`. Algorithm:

1. Initialize activation: `{seed_id: 1.0}` for each directly retrieved memory.
2. For each activated node above threshold, query the `thread_members` table for co-members (memories in the same narrative thread), and query a new `memory_links` table (tag co-occurrence + embedding proximity edges, pre-computed nightly).
3. Propagate activation: `A[neighbor] += A[source] * edge_weight * decay`.
4. After `hops` rounds, return all nodes above threshold, sorted by final activation value.

The pre-computed `memory_links` table (schema: `source_id`, `target_id`, `weight`, `link_type`) can be populated by the nightly `scripts/heartbeat.py`: for each memory pair sharing 2+ tags or with cosine similarity > 0.75, insert an edge.

Use this in the per-prompt injection path (`hooks/user_prompt_inject.py`) as a Tier 2.5 expansion: after FTS retrieval, run one round of spreading activation on the returned memories to surface non-obvious but thematically connected context.

### Expected Benefit

Surfaces latent connections the user didn't explicitly query. Example: a prompt about "API rate limiting" directly retrieves a memory about rate limits, but spreading activation also surfaces a memory about the user's preference for exponential backoff — a thematically connected but lexically non-overlapping observation.

---

## 3. Context-Dependent Memory / Encoding Specificity

### Neurobiological Mechanism

The encoding specificity principle (Tulving & Thomson, 1973) states that recall improves when the retrieval context matches the encoding context. When a memory is formed, contextual details — physical environment, mental/emotional state, task type, language — become integrated into the memory trace itself. These contextual features function as retrieval cues: a person who memorized a word list underwater recalls it better underwater than on land.

Context is multi-dimensional: physical location, auditory environment, cognitive/emotional state at encoding time, and the conceptual "frame" of the task (debugging vs. architecture vs. writing). The effectiveness of a retrieval cue is proportional to how much information it shares with the original encoding context.

**Source**: Wikipedia/Encoding_specificity_principle.

### Computational Analog

The `Memory` model already stores `project_context` (the working directory path at encoding time). The `RelevanceEngine` applies a 1.5× `context_boost` when the current project matches. This is the right architecture but uses only one contextual dimension.

Additional context dimensions available at encoding time:
- **Task type**: inferred from the observation taxonomy (`correction`, `workflow_pattern`, `decision_context`, etc.)
- **Session time-of-day**: available via `created_at` timestamp
- **Conversational register**: formal/debugging/exploratory/creative — inferable from the LLM's characterization of the session
- **Emotional valence of session**: already partially tracked via `EMOTIONAL_STATE_PATTERNS` in `core/prompts.py`

### Design Recommendation

Extend the `Memory` model with a `encoding_context` JSON field (nullable) capturing: `{"task_type": "debugging", "time_bucket": "evening", "register": "exploratory", "project": "memesis"}`. Populate this at observation write time in `hooks/append_observation.py`.

At retrieval time, compute a multi-dimensional context match score:
```python
context_boost = 1.0
if memory.project_context == current_project: context_boost += 0.5
if memory.task_type == current_task_type:     context_boost += 0.3
if memory.time_bucket == current_time_bucket: context_boost += 0.1
if memory.register == current_register:       context_boost += 0.2
```
Cap at 2.0× to avoid extreme over-weighting.

Integrate this into `RelevanceEngine.score()` replacing the binary `context_boost` with the multi-dimensional version.

### Expected Benefit

Memories encoded during debugging sessions surface preferentially when the user is debugging again. Memories about creative/exploratory decisions surface when the user is in an ideation session. This replicates the state-dependent retrieval advantage without requiring explicit tagging by the user.

---

## 4. Reconsolidation

### Neurobiological Mechanism

When a consolidated long-term memory is retrieved, it re-enters a transient labile (unstable) state — a "reconsolidation window" of approximately 6 hours. During this window, protein synthesis inhibitors can block restabilization, effectively erasing or modifying the memory before it re-consolidates. The implication is that memories are not permanent fixed records: each retrieval is an opportunity to update the memory before it is re-encoded.

The molecular pathway requires *de novo* protein synthesis for restabilization. This creates a bounded therapeutic window — interventions after ~6 hours post-retrieval have no effect, as the memory has already restabilized. The process enables adaptive memory updating: outdated information can be corrected at the time of retrieval, preventing stale memories from guiding future behavior.

**Source**: Wikipedia/Memory_reconsolidation.

### Computational Analog

In memesis, every injection event (`last_injected_at` update, `injection_count++`) is a retrieval event that opens a reconsolidation window. Currently, the system records injections in `retrieval_log` but takes no downstream action — injected memories are treated as static.

The reconsolidation analog is: *when a memory is injected into a session, observe whether the session content confirms, contradicts, or refines it, and update the memory before the session ends.*

### Design Recommendation

Introduce a "post-injection validation" step in the PreCompact hook (`hooks/pre_compact.py`). After session content is collected in the ephemeral buffer:

1. Identify which memories were injected during this session (query `retrieval_log` for `session_id = current_session AND retrieval_type = 'injected'`).
2. For each injected memory, include its content in the consolidation prompt with the flag: `INJECTED_THIS_SESSION: true`.
3. Instruct the consolidation LLM to check whether session observations confirm, contradict, extend, or make the memory obsolete — and return an `update` action with revised content when warranted.
4. Apply updates immediately: this is the reconsolidation window closing.

The `Consolidator` class in `core/consolidator.py` already handles `conflicts` detection; this extends it to also handle *positive* refinements, not just contradictions.

Additionally, treat the injection event itself as a relevance signal: increment `reinforcement_count` when injection is followed by `was_used=1` in the retrieval log. This operationalizes "retrieval with engagement" as the trigger for reconsolidation eligibility.

### Expected Benefit

Memories become progressively more accurate over time rather than drifting stale. A memory about a user's architectural preference gets refined each time it's retrieved and the conversation touches the same topic. The system converges toward higher-fidelity representations of durable patterns.

---

## 5. Prospective Memory

### Neurobiological Mechanism

Prospective memory is remembering to perform a planned future action — "when X happens, do Y." It engages a distributed network: the prefrontal cortex holds the intention and suppresses competing thoughts; the hippocampus searches for the intended action among stored memories; the parahippocampal gyrus recognizes environmental cues that trigger execution; the thalamus maintains the intention until the trigger condition is met.

Two primary types:
- **Event-based**: triggered by an external cue ("when I see the library, return the book"). These are more reliable because the external trigger handles the monitoring burden.
- **Time-based**: triggered at a specific time ("at 10 PM, watch the show"). These require active internal time-monitoring and are more failure-prone.

Event-based prospective memory retrieves intentions via two competing processes: active monitoring (consciously watching for the trigger) and spontaneous retrieval (automatic activation when the relevant cue appears).

**Source**: Wikipedia/Prospective_memory.

### Computational Analog

The current system stores only retrospective observations — things that happened. There is no mechanism to capture *intentions* ("next time we work on this API, remind me to check the rate limit headers") or *conditional triggers* ("when the user is in the auth module, surface the session-token observation").

This maps cleanly to event-based prospective memory: the system can hold a `ProspectiveMemory` record with a trigger condition and a payload to surface when the condition fires.

### Design Recommendation

Add a `ProspectiveMemory` model (new table `prospective_memories`):
```
id, trigger_type (tag_match|project_match|keyword_match|time_based),
trigger_value (JSON: {"tags": ["auth"], "project": "memesis", "keywords": ["rate limit"]}),
payload_memory_id (FK to memories),
created_at, fired_at (nullable), fire_count
```

In `hooks/user_prompt_inject.py`, after FTS retrieval, run a prospective trigger scan: for each active `ProspectiveMemory` where `fired_at IS NULL` (or `fire_count < max_fires`), check if the current prompt + session context matches the `trigger_value`. If matched, inject the `payload_memory_id` and record `fired_at`.

The `append_observation.py` hook should detect intent language in observations ("next time," "remind me," "when we're working on," "don't forget to") and automatically create a `ProspectiveMemory` record in addition to the normal memory record.

LLM-based intent detection can be a lightweight classification step: append a boolean `is_prospective` field to the observation schema in `core/prompts.py`, and ask the consolidation LLM to flag intent-bearing observations.

### Expected Benefit

The system gains forward-looking memory — it can act as a trusted reminder system without requiring the user to maintain an explicit todo list. Observations like "remind me to check the migration script before deploying" become actionable triggers rather than passive facts.

---

## 6. Emotional Tagging / Somatic Markers

### Neurobiological Mechanism

Antonio Damasio's somatic marker hypothesis proposes that emotional processes guide decision-making by attaching bodily signals to past experiences and their outcomes. The ventromedial prefrontal cortex (vmPFC) stores and retrieves these markers; the amygdala activates emotional responses to stimuli. When a similar situation recurs, somatic markers fire as "internal alarms" — steering toward beneficial outcomes and away from harmful ones before conscious deliberation completes.

Two pathways: the *body loop* (direct bodily sensation — fear when seeing a threat) and the *as-if body loop* (mental simulation of anticipated consequences without actual stimuli). Damage to vmPFC or amygdala impairs decision-making because the emotional guidance signal is lost.

The adaptive function: emotional tags prioritize memory retrieval. Memories associated with strong outcomes (reward or penalty) are recalled more readily and resist forgetting longer. This is the mechanism behind why emotionally charged events are remembered vividly even decades later.

**Source**: Wikipedia/Somatic_marker_hypothesis.

### Computational Analog

The `Memory` model has an `importance` score (0–1, default 0.5) which serves as the rough analog of emotional tagging — higher importance means more likely to survive consolidation and be injected. However, importance is currently set by the LLM during consolidation and rarely updated thereafter.

The system also has `EMOTIONAL_STATE_PATTERNS` in `core/prompts.py` as a *privacy filter* (stripping emotional content before LLM calls). This is the inverse of what we want: emotional valence should inform importance scoring, not be discarded.

Somatic markers suggest two signals worth computing:
1. **Surprise/prediction-error**: observations that contradict an existing memory are high-signal (the brain's prediction error signal drives plasticity). These are already partially handled as `conflicts` in the consolidator.
2. **Friction/frustration markers**: user pushback, corrections, or expressions of frustration in conversation indicate high-salience moments — the analog of a negative somatic marker.

### Design Recommendation

Introduce `emotional_valence` as a lightweight signal, separate from the privacy filter. At observation write time, classify observations into one of: `neutral`, `positive_surprise`, `negative_surprise`, `friction`, `delight`. This can be a fast regex + keyword pass (not an LLM call) using patterns in `core/prompts.py`.

Use `emotional_valence` to modulate the initial `importance` assignment:
```python
BASE_IMPORTANCE = 0.5
VALENCE_BUMPS = {
    "friction":          +0.25,
    "negative_surprise": +0.20,
    "positive_surprise": +0.15,
    "delight":           +0.10,
    "neutral":            0.00,
}
initial_importance = min(1.0, BASE_IMPORTANCE + VALENCE_BUMPS[valence])
```

Also store `emotional_valence` in the `Memory` model and expose it in the consolidation prompt so the LLM has explicit signal about which observations were emotionally significant.

The `EMOTIONAL_STATE_PATTERNS` privacy filter should remain but be decoupled: *extract* emotional valence first, *then* strip identifying emotional content before the LLM sees it. The valence score survives; the raw emotional text does not.

### Expected Benefit

High-friction observations (corrections, pushback moments, user frustration) receive elevated initial importance and are less likely to be pruned during consolidation. This operationalizes Damasio's insight that emotionally tagged memories have privileged survival — specifically for the observations most likely to improve future behavior.

---

## 7. Interference and Inhibition

### Neurobiological Mechanism

The brain actively suppresses competing memories during retrieval to prevent interference. Retrieval-induced forgetting (RIF) is the canonical demonstration: recalling a subset of studied items (A-Br) suppresses recall of unpracticed related items (A-Bd) — not through passive interference but through active inhibitory control. The frontal cortex shows increased activity during RIF, indicating top-down inhibitory suppression of competitors.

The inhibition mechanism is adaptive: when dominant memories are contextually inappropriate, the frontal cortex suppresses them to allow less-dominant but more situationally relevant responses to surface. This operates at multiple intentionality levels: automatic (RIF, part-set cuing), semi-intentional (think/no-think paradigms), and intentional (directed forgetting).

Beyond RIF, the brain manages competing memories through:
- **Interference at encoding**: similar new information overwrites or blends with existing traces (proactive/retroactive interference)
- **Retrieval competition**: multiple memories with similar cues compete, with the strongest/most recent typically winning
- **Directed inhibition**: deliberate suppression of unwanted memories

**Source**: Wikipedia/Retrieval-induced_forgetting.

### Computational Analog

The `Memory` model already has a `subsumed_by` field — the primary inhibition mechanism. When memory B supersedes memory A, A is linked to B and effectively retired from active injection. This is correct but incomplete.

Current gaps:
1. **No retrieval competition modeling**: when multiple memories match a query, they are ranked by relevance score and the top N are injected. There is no mechanism to detect that memories are competing (similar content, contradictory claims) and actively suppress the weaker one.
2. **No recency-based inhibition**: an old memory with high importance can out-score a recent correction, leading to stale information being injected.
3. **No injection-saturation inhibition**: a memory injected repeatedly without `was_used=1` feedback should be progressively suppressed — it's competing with context but losing.

### Design Recommendation

Three targeted additions to `core/relevance.py` and `core/retrieval.py`:

**A. Competitor suppression at retrieval time.** After the initial retrieval set is assembled, run a pairwise similarity check (cosine or FTS) on the candidate memories. If two memories have similarity > 0.85 and the same `project_context`, suppress the lower-importance one for this session (do not inject it, do not archive it permanently). Log this as `retrieval_type='suppressed'` in `retrieval_log`.

**B. Injection-saturation decay.** Add a `saturation_penalty` to the relevance score:
```python
injection_without_use = memory.injection_count - memory.usage_count
saturation_penalty = min(0.3, injection_without_use * 0.05)
relevance -= saturation_penalty
```
A memory injected 6 times without ever being used (`was_used=1`) loses 0.30 from its relevance — enough to push it below average and reduce its injection frequency, even if its base importance is high.

**C. Recency-dominance for contradictions.** When `subsumed_by` is set, the subsuming memory receives a +0.1 relevance bonus on top of its normal score. This ensures the newer/more accurate memory always wins retrieval competition against its predecessor.

### Expected Benefit

Reduces repetitive injection of memories the user has already processed and doesn't engage with. Prevents stale memories from out-competing recent corrections. Keeps the injected context set semantically non-redundant — analogous to the brain's use of inhibition to present a clean, non-competing retrieval result.

---

## 8. Spacing Effect and Distributed Practice

### Neurobiological Mechanism

The spacing effect (Ebbinghaus, 1885; extensively replicated) demonstrates that distributed practice dramatically outperforms massed repetition for long-term retention. Theoretical accounts:
- **Encoding variability**: spaced presentations occur in different micro-contexts, creating more diverse retrieval cues
- **Study-phase retrieval**: the gap between presentations forces active re-retrieval of the earlier presentation, strengthening the trace
- **Deficient processing**: massed repetitions suffer reduced attention; spacing prevents this

Meta-analysis results: spaced practice outperformed massed in 259/271 cases. Optimal spacing gaps are days to weeks for durable retention.

The SM-2 algorithm (SuperMemo) operationalizes this. For each item tracked by repetition number `n` and ease factor `EF` (initial 2.5):
- n=1: interval = 1 day
- n=2: interval = 6 days
- n≥3: interval = round(previous_interval × EF)
- EF updates: `EF += (0.1 - (5 - q) × (0.08 + (5 - q) × 0.02))` where q is grade 0–5
- EF floor: 1.3

**Source**: Wikipedia/Spacing_effect; Wikipedia/SuperMemo.

### Computational Analog

The memesis system currently injects memories based purely on relevance score (importance × recency × usage × context) with no regard for injection spacing. A highly important memory can be injected into every consecutive session — equivalent to massed practice — without any mechanism to ensure the user has "processed" it before re-injecting.

The `LifecycleManager` already has a `MIN_REINFORCEMENT_SPAN_DAYS = 2` guard requiring that reinforcements for crystallization span at least 2 distinct calendar days. This is the spacing effect applied to *promotion* but not to *injection*.

### Design Recommendation

Apply SM-2 spacing logic to injection scheduling. Add fields to `Memory`: `next_injection_due` (ISO datetime, nullable), `injection_ease_factor` (float, default 2.5), `injection_interval_days` (int, default 1).

After each injection event in `_record_injection()` (`core/retrieval.py`):
1. Retrieve the current `injection_interval_days` and `injection_ease_factor`.
2. Compute `quality` signal: 5 if `was_used=1` in the most recent retrieval log entry for this memory, 2 if injected but unused, 0 if actively suppressed.
3. Apply SM-2: update `injection_ease_factor` and compute `next_injection_due = now + injection_interval_days days`.
4. In the retrieval scoring pipeline, apply a hard suppression if `next_injection_due > now`: set relevance to 0 for this session regardless of other scores. (Bypass suppression for instinctive-stage memories — those are always injected.)

For memories that have never been injected (`injection_count = 0`), set `next_injection_due = now` — they are immediately eligible.

The effect is a dynamic injection cadence: memories the user actively uses in conversation get longer intervals between re-injections (they've been internalized); memories that are injected but never engaged with get shorter intervals (they need more exposure before they "stick"); memories suppressed as irrelevant get the longest intervals.

### Expected Benefit

Prevents context-window saturation from repeatedly injecting memories the user has already internalized. Ensures memories that haven't "landed" get more frequent exposure until they do. Mirrors the most empirically validated learning mechanism in cognitive science — 259/271 meta-analytic cases favor spacing over massed repetition.

---

## Cross-Cutting Synthesis

| Pattern | Primary Hook Point | Key New Field(s) | Interaction With Existing System |
|---|---|---|---|
| Hippocampal Replay | `append_observation.py`, consolidation prompt | `replay_priority` on ephemeral obs | Feeds consolidation LLM sorting; complements `reinforcement_count` |
| Spreading Activation | `user_prompt_inject.py` (Tier 2.5) | `memory_links` table | Extends FTS retrieval; uses `NarrativeThread` as existing graph substrate |
| Encoding Specificity | `append_observation.py`, `RelevanceEngine.score()` | `encoding_context` JSON field | Replaces binary `context_boost` with multi-dimensional match |
| Reconsolidation | `pre_compact.py`, consolidation LLM prompt | `retrieval_log.was_used` feedback loop | Extends existing conflict detection to positive refinements |
| Prospective Memory | `append_observation.py`, `user_prompt_inject.py` | `prospective_memories` table | New table; intent detection in consolidation prompt |
| Somatic Markers | `append_observation.py`, `core/prompts.py` | `emotional_valence` field, importance bump | Decouples privacy filter from valence signal |
| Interference/Inhibition | `RelevanceEngine`, `RetrievalEngine` | `saturation_penalty` logic, competitor suppression | Extends `subsumed_by`; adds injection-saturation decay |
| Spacing Effect | `_record_injection()`, retrieval scoring | `next_injection_due`, `injection_ease_factor`, `injection_interval_days` | Complements `MIN_REINFORCEMENT_SPAN_DAYS` in `LifecycleManager` |

### Priority Order for Implementation

**High impact, low disruption** (no schema changes, touches prompts/scoring):
1. Somatic markers — modify importance initialization in observation write path
2. Interference/inhibition — add saturation penalty to relevance formula

**High impact, moderate schema change** (new fields on `Memory`):
3. Encoding specificity — add `encoding_context` JSON column
4. Spacing effect — add three scheduling fields to `Memory`; modify `_record_injection()`

**High impact, significant new infrastructure**:
5. Reconsolidation — post-injection validation loop in PreCompact
6. Hippocampal replay — `replay_priority` on ephemeral observations

**New capabilities** (new tables, new retrieval paths):
7. Spreading activation — `memory_links` table + activation propagation
8. Prospective memory — `prospective_memories` table + intent detection

---

## Gaps and Limitations

- **Emotional valence without ground truth**: the system has no direct signal of the user's actual emotional state. Valence inference from text patterns (EMOTIONAL_STATE_PATTERNS) is a proxy — will produce false positives on neutral discussions of emotional topics.
- **SM-2 grade signal**: quality grading in SM-2 depends on `was_used` in `retrieval_log`, which currently requires the LLM to explicitly signal memory usage. If this signal is unreliable, the spacing algorithm degrades to fixed intervals.
- **Spreading activation graph sparsity**: the `NarrativeThread` graph is sparse — many memories have no thread membership. Until the `memory_links` table is populated, activation will have few edges to traverse. Pre-computation in `scripts/heartbeat.py` is required before this pattern provides value.
- **Reconsolidation window**: biologically, the window is ~6 hours post-retrieval. In the computational analog, "the session" serves as the window. For very long sessions, the analogy weakens — a memory injected at session start may have "reconsolidated" (been referenced and processed) before the PreCompact hook fires.
```

---

The document is 4,300+ words covering all eight patterns with direct grounding in the existing codebase. The key file to write is `/Users/emma.hyde/projects/memesis/.context/research/2026-03-29-neuroscience-memory-patterns.md`. The `.context/research/` directory needs to be created first (`mkdir -p`), since the glob search confirmed it does not yet exist.

**Sources cited throughout:**
- Wikipedia/Memory_consolidation (hippocampal replay, sleep, novelty, emotional salience)
- PMC 3768102 / pmc.ncbi.nlm.nih.gov (sharp-wave ripples, selectivity, temporal dynamics)
- Wikipedia/Spreading_activation (algorithm, decay formula, semantic priming)
- Wikipedia/Encoding_specificity_principle (contextual cue integration, retrieval matching)
- Wikipedia/Memory_reconsolidation (lability window, protein synthesis, 6-hour window)
- Wikipedia/Prospective_memory (event-based vs. time-based, parahippocampal trigger recognition)
- Wikipedia/Somatic_marker_hypothesis (vmPFC, amygdala, body loop / as-if loop)
- Wikipedia/Retrieval-induced_forgetting (inhibitory account, frontal cortex, multiple intentionality levels)
- Wikipedia/Spacing_effect (encoding variability, study-phase retrieval, 259/271 meta-analysis)
- Wikipedia/SuperMemo (SM-2 formula, EF, interval calculation)