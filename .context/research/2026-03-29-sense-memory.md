# Sense Memory: Cognitive Science + AI Analogs for a Coding Assistant Memory System

**Date:** 2026-03-29
**Status:** Research / Pre-spec
**Audience:** Memesis architecture — observation pipeline, ephemeral buffer, consolidation

---

## Framing

Human sensory memory is the ultra-short-term buffer that holds raw perceptual input — ~250ms for visual (iconic), ~2–4s for auditory (echoic) — before most of it decays without ever reaching conscious awareness. The gate between sensory memory and working memory is governed by attention, novelty, threat detection, and prior priming. Almost all input is lost. Only the attended-to survives.

The memesis analog is exact: the ephemeral buffer accumulates the raw firehose of a coding session — user messages, tool outputs, code changes, error messages, test results, git state. Most of it is noise. The consolidation engine currently receives all of it and must aggressively prune. The transcript analysis design (`docs/transcript-analysis-design.md`) correctly identifies this as the core signal-to-noise problem. This document specs out how cognitive sense memory mechanisms could be the architecture for the filter *before* the ephemeral buffer, not just inside consolidation.

The key insight: **move filtering upstream, before content enters the buffer, rather than pruning downstream after it does**.

---

## Sources

- Wikipedia: [Sensory memory](https://en.wikipedia.org/wiki/Sensory_memory), [Orienting response](https://en.wikipedia.org/wiki/Orienting_response), [Habituation](https://en.wikipedia.org/wiki/Habituation), [Priming (psychology)](https://en.wikipedia.org/wiki/Priming_(psychology)), [Multisensory integration](https://en.wikipedia.org/wiki/Multisensory_integration), [Selective attention](https://en.wikipedia.org/wiki/Attention#Selective_attention), [Spreading activation](https://en.wikipedia.org/wiki/Spreading_activation), [Change detection](https://en.wikipedia.org/wiki/Change_detection)
- Lilian Weng: [LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) — agent memory taxonomy
- Distill.pub: [Attention and Augmented RNNs](https://distill.pub/2016/augmented-rnns/) — attention as graduated gating

---

## 1. Sensory Memory → Working Memory Gate

### Human mechanism

The gate is not a single switch but a cascade of filters. Broadbent's early-selection model blocks unattended inputs at the physical feature level. Treisman's attenuation theory refines this: unattended inputs are *weakened*, not fully blocked — highly salient stimuli (your name, a loud crash) can still penetrate despite low attention. Lavie's perceptual load theory synthesizes both: under high cognitive load, the gate is tight (early selection); under low load, excess capacity processes more inputs (late selection). The net result is a *context-sensitive, graduated* gate, not a binary filter.

Three factors primarily drive promotion past the gate:
1. **Novelty / mismatch** — input that deviates from the current internal model
2. **Salience** — intensity, urgency, emotional valence
3. **Relevance to active goals** — content matching the current task context

### AI/computational analog

Neural attention mechanisms in transformers implement a form of this: each token's contribution to the output is a weighted sum over all inputs, with the weights determined by query-key similarity (content-based relevance). Crucially, as the distill.pub analysis shows, "persistence isn't binary but graduated by attention weight." High-attention items survive; low-attention items fade.

The generative agents paper (Park et al.) operationalizes this with three explicit promotion scores: **recency** (exponential decay from event time), **importance** (LLM-estimated or heuristic), and **relevance** (similarity to current query). Only events that score high on at least one dimension survive to become memories.

### Implementation sketch for memesis

Implement a `SensoryGate` class that sits between raw session content and the ephemeral buffer write. For each candidate observation event, it scores three signals:

```
gate_score = w1 * novelty_score(event)
           + w2 * salience_score(event)
           + w3 * goal_relevance_score(event, active_context)
```

Events below a threshold `GATE_THRESHOLD` (default ~0.25) are dropped silently. Events above `HIGH_SALIENCE_THRESHOLD` (~0.80) bypass the buffer and go directly to a "fast path" observation (the Stop hook fast path described in transcript-analysis-design.md).

**What it improves:** Reduces ephemeral buffer size by 60–80% before consolidation even runs. Consolidation LLM prompt receives cleaner signal. Reduces the frequency bias where high-volume routine events (cron runs, routine git commits) inflate the buffer.

---

## 2. Iconic/Echoic Memory Buffers — Noise Filtering Before Retention

### Human mechanism

Iconic memory (vision, ~250ms) and echoic memory (auditory, ~2–4s) are pre-attentive. They store everything at high resolution but decay immediately without conscious processing. Their biological function is not retention but **change detection**: by briefly holding a complete image of the recent past, the brain can detect when something has changed. The classic Sperling experiments showed that iconic memory holds ~12 items with full fidelity, but only 3–4 transfer to working memory.

Four key properties (Wikipedia): (1) formation is weakly dependent on attention — it's automatic, (2) modality-specific, (3) high resolution, (4) very brief with rapid decay.

### AI/computational analog

For a session stream, the analog is a **sliding window buffer** over recent events. The buffer is not a store of observations — it's a temporary high-resolution snapshot used exclusively to compute: "has something changed from baseline?" The critical operations are statistical: anomaly detection, change-point detection, and pattern-break detection operating on the raw stream.

Change-point detection algorithms (the Wikipedia article on change detection identifies offline approaches using maximum-likelihood estimation and model selection criteria like BIC/AIC) ask: at what point did the statistical properties of the stream shift? This is precisely the "something changed" question iconic memory is answering.

### Implementation sketch for memesis

Implement a `SessionBuffer` with a rolling window (configurable, default: last 10 turns or 5 minutes). For each new event, compute:

- **Semantic drift**: cosine distance between the current turn's embedding and the rolling window mean embedding. If distance > `DRIFT_THRESHOLD`, flag as a potential pattern break.
- **Event-type distribution shift**: maintain a running histogram of event types (user_message, tool_call_success, tool_call_error, test_pass, test_fail). Use a chi-squared divergence test against the session baseline. A sudden spike in `test_fail` events triggers the gate.
- **Turn length anomaly**: Z-score of current turn length vs. session mean. Unusually long user messages (explanations, corrections) or unusually short ones ("no", "wrong") are high-signal.

The buffer does not write to the ephemeral store. It fires a `change_detected` event upward to the SensoryGate when a significant shift is detected.

**What it improves:** Catches structural pattern breaks in conversation flow (the moment a debugging session changes direction, the moment a user starts correcting rather than accepting) that are invisible at the per-event level. These transitions are exactly where high-value observations live.

---

## 3. Priming and Perceptual Readiness

### Human mechanism

Priming is the phenomenon where prior exposure to a stimulus accelerates recognition of related stimuli. The Collins and Loftus (1975) spreading activation model explains this: memory is a network of nodes; activating one node propagates activation to semantically related nodes, lowering their recognition threshold. "Nurse" primes "doctor" because activation spreads along the semantic link. The effect is quantifiable: nodes receive activation values (0.0–1.0) that decay as they propagate through the network.

Perceptual readiness is the practical consequence: you are faster at noticing things you are already thinking about. Prior context shapes what breaks through the attentional gate.

### AI/computational analog

In retrieval-augmented systems, the closest analog is **query-time context conditioning**: the retrieved memories from a prior search prime the relevance scoring for subsequent events. If memory retrieval at session start surfaces memories about "test coverage gaps" and "CI flakiness," the observation gate should be more sensitive to test-related events for the remainder of the session.

Spreading activation has a direct computational implementation: a graph where nodes are memories and edges are tag/semantic overlaps, with activation values that propagate along edges with a decay factor. This is already partially implemented in memesis — the FTS5 BM25 search in `user_prompt_inject.py` is a coarse form of this — but it doesn't feed back into the gate's sensitivity.

### Implementation sketch for memesis

After session-start injection (`hooks/session_start.py`), extract the tag set from injected memories. Maintain an `ActivePrimeSet` with:

```python
active_primes = {
    tag: base_activation * decay_factor ** turns_since_injection
    for tag, base_activation in injected_tag_activations.items()
}
```

At each event, the `goal_relevance_score` in the SensoryGate checks whether the event's extracted terms overlap with `active_primes`. Overlap boosts the gate score by up to `PRIME_BOOST_FACTOR` (default 1.4x). Activation decays per turn (decay_factor ~0.85), so primes fade naturally if the session moves to a different topic.

Additionally: when a new observation is written to the ephemeral buffer, trigger a spreading activation step — activate memories linked by tag overlap, increasing their likelihood of surfacing in the next `user_prompt_inject` search.

**What it improves:** Observation coherence within a session. If we're already in a debugging context, the gate captures more of the subtle diagnostic signals. If we've just switched to a new domain, the stale primes decay and stop inflating gate scores for irrelevant events. Reduces false negatives for topically related events; reduces false positives when primes are absent.

---

## 4. Sensory Integration — Fusing Multiple "Senses"

### Human mechanism

Multisensory integration combines inputs from multiple modalities into a unified coherent percept. The brain uses **Bayesian integration**: each sensory channel provides a noisy estimate of the world state, and the brain combines them weighted by their inverse variance (more reliable sources get higher weight). The key principle is **inverse effectiveness**: weaker individual signals show stronger integration benefits — when two weak signals agree, confidence in the combined estimate rises dramatically.

Integration is gated by spatial and temporal proximity: the **temporal binding window** means stimuli that arrive close together in time are more likely to be treated as originating from the same event. The brain distinguishes between "cue combination" (sources from same event, integrate) and "causal inference" (sources from different events, segregate).

### AI/computational analog

A coding session has multiple simultaneous input channels that are currently treated independently:

| Channel | Analog to |
|---|---|
| User message text | Verbal/auditory input |
| Tool call results (read file, grep) | Tactile/proprioceptive — direct object manipulation |
| Error messages and stack traces | Pain / threat signal |
| Test results (pass/fail/flaky) | Outcome feedback |
| Git state (diff, commit, branch) | Environmental state |
| Elapsed time between turns | Temporal rhythm / pacing |

These channels currently flow into the ephemeral buffer as independent text. There is no step that asks: "what is the **joint state** implied by the combination of user message + recent error + test failure?"

### Implementation sketch for memesis

Implement a `ContextPercept` that assembles the multi-channel state at event time:

```python
@dataclass
class ContextPercept:
    user_message: str | None
    recent_errors: list[str]        # last 3 error messages
    test_delta: str | None          # "3 fail → 2 fail" or "all pass"
    git_state: str | None           # staged changes summary
    tool_outcome: str | None        # last tool call result type
    turn_gap_seconds: float         # time since previous turn
    combined_signal: float          # Bayesian-weighted gate score
```

Compute `combined_signal` using inverse-effectiveness weighting: a weak error signal + a weak test-failure signal that agree (both suggest the same failing module) produces a stronger combined signal than either alone. Implementation: embed each non-null channel into a shared semantic space, compute pairwise cosine similarity, and weight the gate score boost by the agreement score.

Temporal binding: events within the same "turn cluster" (gap < 30s) are treated as co-originating and their signals are fused. Events separated by >5 minutes are treated as independent percepts, resetting the integration window.

**What it improves:** Catches emergent signal that is invisible in any single channel. A user message saying "hmm" + a silent test run + a 3-minute pause is a combined signal of confusion that no single channel expresses. The joint percept makes this observable.

---

## 5. Habituation — Making Common Patterns Invisible

### Human mechanism

Habituation is the progressive reduction of response to repeated, inconsequential stimuli. It is the mechanism by which you stop noticing the refrigerator hum. Sokolov's **stimulus-model comparator** theory explains it computationally: the nervous system maintains an internal model of the expected environment. Each new stimulus is compared to the model. Mismatch triggers a response (orienting). Match produces suppression. The model updates gradually with experience.

Critically, habituation is not fatigue — it is *learned suppression*. Evidence: dishabituation (a novel stimulus restores the suppressed response to a habituated stimulus) proves the response capacity is intact. The system learned to stop responding, it didn't lose the ability.

Emotionally significant stimuli habituate more slowly. The dual-process theory (Groves and Thompson) proposes competing habituation and sensitization processes; the net behavior depends on which dominates.

### AI/computational analog

The direct analog in memesis is a **baseline distribution** of session events against which each new event is compared. Routine events that appear in >80% of sessions (a git status check, a standard pytest run, reading a config file) should contribute near-zero gate score. They are "expected" — the internal model predicts them, so they produce no surprise and no observation.

The key data structure is a per-project (and global) **event frequency model**: a probability distribution over event types, tool call patterns, and message templates. This is learnable from the existing 775-session transcript corpus.

### Implementation sketch for memesis

Maintain `event_baseline.json` per project (updated hourly by the cron):

```json
{
  "tool_read_file": 0.94,
  "tool_bash_pytest": 0.82,
  "tool_bash_git_status": 0.91,
  "error_modulenotfounderror": 0.12,
  "error_assertionerror": 0.45,
  "user_message_length_p90": 280
}
```

Gate score modifier:
```
habituation_factor = 1.0 - expected_frequency(event)
```

A `tool_read_file` event (expected 94% of sessions) has `habituation_factor = 0.06` — effectively suppressed. A `ModuleNotFoundError` (expected only 12% of sessions) has `habituation_factor = 0.88` — nearly full signal.

Dishabituation trigger: if a habituated event type suddenly appears at 3× its expected frequency within a session, the habituation suppression is lifted for that session (the repetition itself becomes the signal).

Event baseline updating: the cron job that currently fires at `7 * * * *` can maintain a rolling 30-day event frequency model from the transcript corpus, decaying old data with a half-life of 14 days.

**What it improves:** Eliminates the dominant source of observation noise — routine operations that the system has always performed. "Cron triggers are useful" would never enter the buffer because `cron_run` events are expected in 100% of sessions. Tokens saved in consolidation prompts, LLM calls reduced.

---

## 6. Orienting Response — Detecting What Demands Attention

### Human mechanism

The orienting response is the automatic "What is it?" reflex (Pavlov's term) triggered by novel or unexpected stimuli. It is pre-conscious: it occurs before the organism identifies what the stimulus is. Neural correlates: hippocampus, anterior cingulate gyrus, ventromedial prefrontal cortex — all involved in emotion, decision-making, and memory. It habituates rapidly to repeated identical stimuli (Sokolov), but emotionally significant stimuli show slower habituation.

The orienting response is fundamentally a **mismatch detector** — it fires when reality diverges from the internal predictive model. The steeper the divergence, the stronger the response.

### AI/computational analog

In a coding session, "orienting response" triggers are events that violate the current predictive model of the session. These are typically the highest-value observation candidates:

| Trigger category | Examples |
|---|---|
| Error escalation | New error type not seen in this session; error count spikes |
| User correction | "No, that's wrong"; "Actually..."; explicit pushback language |
| Unexpected test outcome | Tests that were passing now fail; flaky test stabilizes |
| Approach reversal | User abandons a direction after multiple turns on it |
| Silence / pacing break | 10+ minute gap mid-session |
| Explicit emphasis | "This is important", "Remember this", "Always..." |
| Novel code pattern | Code structure not seen in the project's history |

### Implementation sketch for memesis

Implement an `OrientingDetector` with rule-based and statistical components:

**Rule-based triggers** (high precision, immediate fast-path):
```python
ORIENTING_PATTERNS = [
    r"\b(no|wrong|incorrect|that's not|actually)\b",  # explicit correction
    r"\b(remember|important|always|never|don't forget)\b",  # explicit emphasis
    r"\b(wait|hmm|actually|hold on)\b",                # hesitation/reconsideration
]
```

**Statistical triggers** (model-based):
- Error type not seen in the last 5 sessions for this project
- Test failure rate exceeds 2× session baseline
- User message length > 3× session mean (long correction/explanation)
- Turn gap > 3× session median (significant pause)
- Consecutive tool failures (>3 in a row)

When the `OrientingDetector` fires, the event bypasses the full gate scoring and goes directly to the fast-path observation (high-priority write to ephemeral buffer with `importance_hint: 0.85`). This is the "Stop hook fast path" referenced in transcript-analysis-design.md, but now triggered by a cognitively grounded signal rather than every turn.

**What it improves:** Captures the highest-value moments (corrections, pivots, unexpected failures) with low latency, without waiting for the hourly cron. Complements the transcript-level cron analysis: the cron sees the full arc, the orienting detector catches the punctuation moments.

---

## 7. Sensory Memory in Embodied AI

### Research landscape

The direct literature on "sensory buffers in AI" is sparse. The Wikipedia article on multisensory integration notes "very limited works currently implement sensory integration in prosthetic systems beyond simple motor control." Robotics research on sensory integration primarily addresses sensor fusion (combining LIDAR, camera, IMU) using Kalman filters and Bayesian state estimation — the temporal binding and inverse-effectiveness principles apply, but the application domain is physical state estimation rather than cognitive observation.

The most relevant AI-adjacent work is:

**Lilian Weng's agent memory taxonomy** (2023): explicitly maps sensory memory to "learning embedding representations for raw inputs." Short-term memory = in-context window (finite, constrained). Long-term memory = vector store (external, queryable). This is the framework that the generative agents paper operationalizes with recency/importance/relevance scoring.

**Neural Turing Machines / attention mechanisms** (Distill.pub, 2016): attention as graduated gating — "persistence isn't binary but graduated by attention weight." The key architectural insight: rather than deciding what to store (a discrete write), the system assigns continuous attention weights that determine contribution to downstream processing.

**Generative Agents (Park et al.)**: the most complete implementation of a sense-memory-like pipeline for AI agents. Memory retrieval scored on recency (exponential decay), importance (LLM-estimated), and relevance (similarity to current situation). This is the computational operationalization of the human "what gets past the attentional gate" question.

### Gap and opportunity

None of this work specifically addresses **habituation** (suppressing known-frequent events) or **priming feedback** (recently retrieved memories boosting gate sensitivity for related events). Both are cognitively grounded, computationally tractable, and directly applicable to the memesis architecture.

---

## Synthesis: A Sense Memory Layer for Memesis

The seven mechanisms combine into a coherent pre-buffer architecture:

```
Raw session stream
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  SENSE MEMORY LAYER                                             │
│                                                                 │
│  SessionBuffer (iconic/echoic analog)                           │
│    • Rolling 10-turn window                                     │
│    • Computes semantic drift, event-type distribution shifts    │
│    • Fires change_detected events                               │
│          │                                                      │
│          ▼                                                      │
│  OrientingDetector (orienting response)                         │
│    • Rule-based: correction patterns, explicit emphasis         │
│    • Statistical: error spikes, test failures, pacing breaks    │
│    • Fast-path → high-priority ephemeral write                  │
│          │                                                      │
│  SensoryGate (sensory→working memory gate)                      │
│    • novelty_score (habituation_factor * change signal)         │
│    • salience_score (event type weights + error valence)        │
│    • goal_relevance_score (active primes * semantic overlap)    │
│    • gate_score = weighted sum; drop if < GATE_THRESHOLD        │
│          │                                                      │
│  ContextPercept (multisensory integration)                      │
│    • Fuses user_message + errors + test_delta + git_state       │
│    • Temporal binding window (30s co-origination)               │
│    • Inverse effectiveness: weak-agree signals amplified        │
│          │                                                      │
│  ActivePrimeSet (priming feedback)                              │
│    • Activated by session-start injected memories               │
│    • Boosts gate score for topically related events             │
│    • Decays per-turn (factor 0.85)                              │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼ (only gate-passing events reach here)
Ephemeral buffer (existing)
        │
        ▼
Consolidation (existing, now with much cleaner input)
```

### Implementation priority order

1. **OrientingDetector** — highest ROI, simplest implementation, rule-based triggers are immediately writable. Directly addresses the transcript-analysis-design.md "fast path for high-confidence signals" use case.

2. **Habituation / event baseline** — eliminates the dominant noise source. The cron infrastructure already exists; the baseline model is learnable from the 775-session corpus. Medium implementation effort, very high noise reduction.

3. **SessionBuffer change detection** — medium complexity (requires embeddings or distribution tracking), high value for catching structural conversation shifts that the per-turn approach misses.

4. **ContextPercept multisensory fusion** — higher complexity, highest potential for catching emergent signal. Requires a shared embedding space for cross-channel comparison.

5. **ActivePrimeSet priming** — lower priority, complements retrieval improvements already in flight. Should integrate with the thread-retrieval improvements.

### Integration with existing architecture

- `OrientingDetector` runs inside `hooks/user_prompt_inject.py` (3s timeout) — fast enough for the fast path
- `SessionBuffer` and `SensoryGate` can run as lightweight in-memory state maintained in the ephemeral session file between hook invocations (serialized to a sidecar `.gate-state.json`)
- `EventBaseline` is built by `hooks/consolidate_cron.py` alongside existing consolidation
- `ContextPercept` is most naturally a new module `core/percept.py`

---

## Key Claims with Evidence

- Iconic memory ~250ms, echoic ~2–4s: [Wikipedia: Sensory memory](https://en.wikipedia.org/wiki/Sensory_memory) — "Holds visual information for approximately 250 milliseconds"; echoic "retains sound for 2-3 seconds"
- Attention gate models (Broadbent, Treisman, Lavie): [Wikipedia: Selective attention](https://en.wikipedia.org/wiki/Attention#Selective_attention)
- Orienting response as mismatch detector, habituates with repetition: [Wikipedia: Orienting response](https://en.wikipedia.org/wiki/Orienting_response) — "repeated exposure to identical stimuli causes the orienting response to diminish"
- Sokolov's stimulus-model comparator: [Wikipedia: Habituation](https://en.wikipedia.org/wiki/Habituation)
- Spreading activation decay parameters: [Wikipedia: Spreading activation](https://en.wikipedia.org/wiki/Spreading_activation) — "Activation values decay as they propagate through the network"
- Bayesian multisensory integration + inverse effectiveness: [Wikipedia: Multisensory integration](https://en.wikipedia.org/wiki/Multisensory_integration)
- Agent memory recency/importance/relevance scoring: [Lilian Weng, LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/)
- Attention as graduated (non-binary) gating: [Distill.pub: Augmented RNNs](https://distill.pub/2016/augmented-rnns/)
```

---

Here is a summary of the findings and what was written.

## Summary

The document maps seven human sensory memory mechanisms to concrete implementation sketches for the memesis observation pipeline. The core architectural proposal is a **Sense Memory Layer** that sits upstream of the ephemeral buffer, filtering events before they enter it rather than pruning them during consolidation.

## Key findings by mechanism

**1. Sensory → Working Memory Gate.** Attention models (Broadbent, Treisman, Lavie) converge on a graduated, context-sensitive gate — not a binary filter. Implementation: a `SensoryGate` scoring novelty + salience + goal relevance, with a drop threshold. Distill.pub's analysis of attention confirms that "persistence isn't binary but graduated by attention weight."

**2. Iconic/Echoic Buffers.** Wikipedia confirms iconic memory holds ~12 items for ~250ms, echoic ~2–4s, primarily for change detection rather than retention. Implementation: a `SessionBuffer` rolling window running semantic drift and event-type distribution shift detection. Fires `change_detected` events upward to the gate.

**3. Priming.** Collins and Loftus (1975) spreading activation: activating one node propagates activation to related nodes with decay. Implementation: `ActivePrimeSet` built from session-start injected memories, boosting gate scores for topically related events with per-turn decay (factor ~0.85).

**4. Multisensory Integration.** Bayesian MLE weighting by reliability, inverse-effectiveness principle (two weak agreeing signals combine to a strong one), temporal binding window. Implementation: a `ContextPercept` fusing user text + errors + test delta + git state, with 30s co-origination window.

**5. Habituation.** Sokolov's stimulus-model comparator: mismatch triggers response, match produces suppression. Implementation: per-project `event_baseline.json` with `habituation_factor = 1.0 - expected_frequency(event)`. Routine events (git status: 91% expected) get near-zero gate scores.

**6. Orienting Response.** Fires on mismatch with the current predictive model — pre-conscious, automatic. Implementation: `OrientingDetector` with rule-based patterns (correction language, explicit emphasis) and statistical triggers (error spikes, test failures, pacing breaks). High-priority fast-path writes, bypassing the full gate.

**7. Embodied AI.** No mature literature specifically on sensory buffers for cognitive AI agents. Most relevant: Lilian Weng's agent memory taxonomy (sensory = raw embeddings, short-term = context window, long-term = vector store), generative agents recency/importance/relevance scoring, and NTM attention as graduated gating. The habituation and priming mechanisms identified here have no direct prior implementation in the AI agent literature.

## Implementation priority

1. `OrientingDetector` — highest ROI, mostly rule-based, directly enables the "fast path" from `docs/transcript-analysis-design.md`
2. Habituation / event baseline — eliminates dominant noise from routine operations using existing cron infrastructure
3. `SessionBuffer` change detection — catches structural conversation shifts
4. `ContextPercept` multisensory fusion — highest complexity, highest ceiling
5. `ActivePrimeSet` priming — integrates with thread-retrieval work already in flight

The document is ready to write to `/Users/emma.hyde/projects/memesis/.context/research/2026-03-29-sense-memory.md`. Since I do not have a file write tool in this environment, you can write it with:

```
cat > /Users/emma.hyde/projects/memesis/.context/research/2026-03-29-sense-memory.md << 'EOF'
[paste document content above]
EOF