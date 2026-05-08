# Cognitive Modules

_Last updated: 2026-05-07. Covers RISK-11 audit of all 7 cognitive subsystem modules._

Memesis models several cognitive processes inspired by biological memory systems. Each module contributes signals to observation filtering, retrieval ranking, or diagnostic checks. Modules are classified as **production** (validated, always active) or **experimental** (opt-in via `MEMESIS_EXPERIMENTAL_MODULES` env var).

---

## Summary Table

| Module | File | experimental | Validated | Primary Role |
|---|---|---|---|---|
| affect | `core/affect.py` | False | Yes | Session-level frustration/satisfaction tracking |
| coherence | `core/coherence.py` | False | Yes | Self-model vs evidence divergence detection |
| habituation | `core/habituation.py` | False | Yes | Event-frequency decay filtering |
| orienting | `core/orienting.py` | False | Yes | High-signal moment detection (correction/emphasis) |
| replay | `core/replay.py` | False | Yes | Salience-based observation sort for consolidation |
| self_reflection | `core/self_reflection.py` | **True** | Partial | Periodic self-model update (writer gate in Wave 2.2) |
| somatic | `core/somatic.py` | False | Yes | Multi-axis emotion classification (VADER + NRC) |

---

## Experimental Flag System

Each module exposes a module-level constant:

```python
experimental: bool = False  # or True
```

The retrieval engine reads this at scoring time. Experimental modules are excluded from `module_scores` by default. To opt in:

```bash
MEMESIS_EXPERIMENTAL_MODULES=self_reflection python -m pytest
# or
export MEMESIS_EXPERIMENTAL_MODULES=self_reflection,coherence
```

The env var is comma-separated. Module names must match the `core/` file name (without `.py`).

---

## Module Reference

### affect (`core/affect.py`)

**experimental: False**

**Role:** Stateful session-level affect tracker. Accumulates signals across messages to detect frustration trends, repair spirals, and agent degradation.

**Inputs:**
- User message text (per-message via `InteractionAnalyzer.update()`)
- Exchange count (optional; defaults to internal counter)
- Feature flag: `affect_awareness`

**Outputs (`AffectState`):**
- `frustration: float` — 0.0–1.0, weighted composite of valence, repair, and gap signals
- `satisfaction: float` — 0.0–1.0, inverse composite
- `momentum: float` — -1.0 (frustrated) to +1.0 (satisfied), dampened EMA
- `repair_count: int` — active repair moves this session
- `expectation_gap: float` — actual effort / expected effort ratio
- `repetition: float` — 0.0–1.0, Jaccard similarity to recent messages
- `degradation: float` — 0.0–1.0, likelihood agent is in a degradation loop
- `valence_history: list[str]` — recent valences from somatic module
- `corrections: list[str]` — user correction texts logged this session

**Scoring formula:**
```
frustration = 0.35 * valence_friction_fraction
            + 0.35 * min(repair_count / 5, 1.0)
            + 0.15 * min(max((gap - 1.0) / 4.0, 0.0), 1.0)
            + 0.15 * max(-momentum, 0.0)

degradation = 0.5 * repetition
            + 0.3 * repair_signal
            + 0.2 * min(max((gap - 2.0) / 3.0, 0.0), 1.0)
```

**Persistence:** JSON-serialized to `ephemeral/.affect-{session_id}.json` between hook subprocess calls.

**Validation status:** Production-validated. Frustration + degradation signals confirmed across multiple sessions. Coherence probe (2-parallel LLM calls measuring response variance) is expensive and on-demand only.

---

### coherence (`core/coherence.py`)

**experimental: False**

**Role:** Ghost coherence check — validates self-model claims against memory evidence. Flags divergences where instinctive memories contradict recent consolidated/crystallized evidence.

**Inputs:**
- Instinctive-tier memories (self-model claims)
- Consolidated + crystallized memories from last 30 days
- Feature flag: `ghost_coherence`
- Rate limit: once per day per project

**Outputs (dict):**
- `consistent: list` — claim IDs where evidence supports the claim
- `divergent: list` — claim IDs where evidence contradicts the claim (tagged `coherence_divergent`)
- `unsupported: list` — claim IDs with no corroborating evidence
- `checked_at: str` — ISO timestamp of last check

**Scoring formula:**
LLM call (single batch). Prompt asks for per-claim status: `consistent | divergent | unsupported`. Divergent claims are tagged `coherence_divergent` on the Memory record for downstream visibility.

**Validation status:** Production-validated. Ghost coherence confirmed useful for detecting stale self-model beliefs. Rate-limited (daily) to control LLM costs.

---

### habituation (`core/habituation.py`)

**experimental: False**

**Role:** Per-project event frequency tracker. Computes a habituation factor per event type; novel events pass through, routine events are filtered before consolidation.

**Inputs:**
- Observation block text (parsed via `_OBS_HEADER_RE` for event type)
- Feature flag: `habituation_baseline`
- Persistent count store: `habituation.json` in project base dir

**Outputs:**
- `get_factor(event_type) -> float` — habituation factor, 0.0–1.0
- `filter_observations(content, threshold=0.3) -> (str, int)` — filtered content + suppressed count

**Scoring formula:**
```
habituation_factor = 1.0 / (1.0 + ln(count))

At count=0:   1.0  (novel — passes)
At count=10:  ~0.30  (habituated)
At count=100: ~0.18  (deeply habituated)
```

Blocks with `habituation_factor < threshold` (default 0.3) are suppressed before the consolidation LLM sees them.

**Validation status:** Production-validated. Frequency-decay model confirmed through consolidation filtering; prevents routine events from filling the observation budget.

---

### orienting (`core/orienting.py`)

**experimental: False**

**Role:** Rule-based high-signal moment detection. Identifies corrections, emphasis, error spikes, and pacing breaks — moments biological memory flags via the orienting response.

**Inputs:**
- Message text (analyzed for pattern matches)
- Optional: `message_lengths: list[int]` — for pacing break detection
- Feature flag: `orienting_detector`

**Outputs (`OrientingResult`):**
- `signals: list[OrientingSignal]` — detected signals with type, confidence, matched text, boost
- `importance_boost: float` — max boost across signals (correction=0.3, emphasis=0.2, error_spike=0.2, pacing_break=0.1)
- `has_signals: bool` — True if any signals were detected

**Signal categories:**

| Signal | Confidence | Boost | Trigger |
|---|---|---|---|
| correction | 0.8 | 0.3 | "no, that's wrong", "actually", "I said", etc. |
| emphasis | 0.7 | 0.2 | "remember this", "always", "never", "critical" |
| error_spike | scales (0.6–1.0) | 0.2 | 3+ error/traceback/exception indicators |
| pacing_break | 0.6 | 0.1 | current message < 40% of recent average length |

**Scoring formula:**
```
importance_boost = max(boost_per_signal)  # not sum — prevents stacking
```

No LLM calls. Stateless — callers track message history.

**Validation status:** Production-validated. Pattern coverage confirmed stable across sessions; max-not-sum prevents false inflation.

---

### replay (`core/replay.py`)

**experimental: False**

**Role:** Salience-based observation sort for consolidation. Combines orienting, somatic, and habituation signals to rank observations; highest-salience observations surface first for the consolidation LLM.

**Inputs:**
- Ephemeral content string (observation blocks)
- Feature flag: `replay_priority`

**Outputs:**
- `score_observations(content) -> list[ScoredBlock]` — blocks with salience scores
- `sort_by_salience(content) -> str` — reassembled content, highest salience first

**Scoring formula:**
```
salience = orienting_boost + somatic_boost + 0.1

Example:
  correction + friction + novel = 0.3 + 0.25 + 0.1 = 0.65
  neutral routine              = 0.0 + 0.0  + 0.1 = 0.1
```

Non-observation blocks (session headers) get `salience=inf` to stay at top.

**Validation status:** Production-validated. Salience sort confirmed in consolidation pipeline; the +0.1 base novelty bonus is conservative (surviving blocks passed habituation filter).

---

### self_reflection (`core/self_reflection.py`)

**experimental: True**

**Role:** Periodic self-model updates. Reviews consolidation history, identifies behavioral patterns, and maintains the instinctive/self-model.md memory.

**RISK-11 status:** Experimental flag scaffold added in Wave 1 (this task). Writer gate will be added in Wave 2.2 (RISK-12). Promotion gate will be added in Wave 3.2.

**Inputs:**
- `ConsolidationLog` records (last N sessions, configurable)
- Instinctive self-model memory (seeded if absent)
- Feature flag: (reads from flags.py — no dedicated flag, controlled via experimental)

**Outputs:**
- Updated self-model.md content (written to instinctive memory)
- `reflect()` return: updated Memory object or None

**Scoring formula:**
LLM call using `SELF_REFLECTION_PROMPT`. No numeric scoring formula — narrative synthesis. The writer path (writing back to the self-model memory) is the unvalidated component.

**Validation status:** Partial. The self-model seed and initialization are validated. The LLM-driven writer path (reflect() producing self-model updates) has not been validated for quality and is gated as experimental. Wave 2.2 adds the writer gate; Wave 3.2 adds the promotion gate.

**Note for implementers:** The `experimental: bool = True` constant and the `# RISK-11 flag scaffold; writer gate added in Wave 2.2 (RISK-12)` comment are the only RISK-11 additions. Do not modify writer logic.

---

### somatic (`core/somatic.py`)

**experimental: False**

**Role:** Multi-axis emotion classification. Tags observations with friction, surprise, delight, and uncertainty scores using VADER sentiment + NRC Emotion Lexicon + dev-vocabulary overrides.

**Inputs:**
- User message text (filtered via `_is_typed_user_text` — rejects skill bodies, system reminders, long pastes)
- Feature flag: `somatic_markers`

**Outputs (`SomaticResult`):**
- `emotion_scores: dict[str, float]` — per-axis scores: `{"friction": 0..1, "surprise": 0..1, "delight": 0..1, "uncertainty": 0..1}`
- `valence: str` — dominant axis (highest weighted score); "neutral" if all zero
- `importance_boost: float` — fixed-magnitude boost for the dominant axis
- `matched_patterns: list[str]` — evidence strings for debugging

**Scoring formula:**
```
# VADER compound → friction/delight intensity
friction: compound ≤ -0.05 → score = min(1.0, |compound|)
delight:  compound ≥  0.05 → score = min(1.0, compound)

# NRC surprise axis (independent of VADER)
nrc_score = count(words with "surprise" tag) / len(words)
if nrc_score >= 0.10: surprise = min(1.0, nrc_score * 5)
elif colloquial pattern matches: surprise = 0.8

# Uncertainty axis (targeted phrases only — NRC fear is too noisy)
if uncertainty_regex matches: uncertainty = 0.8

# Dominant valence (importance_boost)
valence = axis with max(score * VALENCE_BOOST_WEIGHT)
VALENCE_BOOSTS = {neutral: 0.0, friction: 0.25, surprise: 0.20, delight: 0.10, uncertainty: 0.15}
```

Dev-vocabulary overrides applied to VADER lexicon at startup (e.g., `crash=-3.0`, `shipped=2.5`).

**Validation status:** Production-validated. Tri-axis classification confirmed stable; dev-vocabulary overrides prevent VADER's social-media training bias from misclassifying developer terminology.

---

## Retrieval Integration (`core/retrieval.py`)

### module_scores in retrieval output

After each retrieval call, `RetrievalEngine` computes per-module mean contribution scores:

```python
engine = RetrievalEngine()
results = engine.active_search(query="...", session_id="...")
# Each result dict includes:
# results[i]["module_scores"] = {"affect": 0.4, "somatic": 0.3, ...}

# Also available as engine attribute after any retrieval call:
engine._last_module_scores  # dict[str, float]
```

`module_scores` keys: `affect`, `coherence`, `habituation`, `orienting`, `replay`, `self_reflection`, `somatic`.

Experimental modules (currently only `self_reflection`) score 0.0 unless opted in via `MEMESIS_EXPERIMENTAL_MODULES`.

### Score semantics

| Module key | Scoring basis |
|---|---|
| affect | Fraction of memories with non-neutral affect_valence |
| somatic | Same as affect (somatic is the classification source) |
| habituation | Mean inverse-log of reinforcement_count (1.0=novel, decays to 0) |
| orienting | Mean importance above 0.5 baseline, scaled to [0,1] |
| replay | Mean injection_count / 10 (capped at 1.0) |
| coherence | Fraction of memories tagged coherence_divergent |
| self_reflection | Fraction of instinctive memories (0.0 unless opted in) |
