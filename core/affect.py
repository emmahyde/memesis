"""
InteractionAnalyzer — stateful session-level affect tracking.

Accumulates signals across messages within a session to detect:
- Frustration/satisfaction trends (from somatic valence history)
- Conversational repair spirals (re-explanations, redirections, escalations)
- Expectation gaps (implicit "this should be easy" vs actual effort)
- Message repetition (user saying the same thing = agent degradation)

Two intervention layers:
- Cheap: repetition + repair patterns → degradation likelihood (no LLM)
- Expensive: coherence probe → 2 parallel LLM calls measuring response variance

Stateful by design: maintains a sliding window of message-level signals.
Session-scoped: state persists via JSON between hook subprocess calls.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .somatic import classify_valence, SomaticResult

logger = logging.getLogger(__name__)


@dataclass
class AffectState:
    """Composite affect state for the current session."""

    frustration: float = 0.0       # 0.0–1.0, weighted composite
    satisfaction: float = 0.0      # 0.0–1.0, weighted composite
    momentum: float = 0.0          # -1.0 (frustrated) to +1.0 (satisfied)
    repair_count: int = 0          # active repair moves this session
    expectation_gap: float = 1.0   # ratio: actual_effort / expected_effort
    repetition: float = 0.0        # 0.0–1.0, how much user repeats themselves
    degradation: float = 0.0       # 0.0–1.0, likelihood agent is degrading
    valence_history: list[str] = field(default_factory=list)  # last N valences
    corrections: list[str] = field(default_factory=list)      # user corrections log

    @property
    def needs_guidance(self) -> bool:
        """True if frustration is high enough to warrant intervention."""
        return self.frustration > 0.6

    @property
    def likely_degraded(self) -> bool:
        """True if signals suggest agent quality degradation, not task difficulty."""
        return self.degradation > 0.5


# --- Repair move patterns ---

_REPAIR_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bno[,.]?\s+I\s+meant\b",
        r"\bwhat\s+I(?:'m|\s+am)\s+saying\b",
        r"\blike\s+I\s+said\b",
        r"\bI\s+already\s+(?:said|told|asked)\b",
        r"\bforget\s+(?:that|it)\b",
        r"\bnever\s*mind\b",
        r"\blet'?s\s+try\s+(?:something|a\s+different)\b",
        r"\bjust\s+do\b",
        r"\bstop\b",
        r"\bwhy\s+can'?t\s+you\b",
        r"\bplease\s+just\b",
        r"\bagain\b",
    ]
]

# --- Expectation markers ---

_LOW_EFFORT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bjust\s+\w+",
        r"\bsimply\b",
        r"\bquickly?\b",
        r"\breal\s+quick\b",
        r"\bcan\s+you\b",
        r"\bshould\s+be\s+(?:easy|simple|straightforward)\b",
    ]
]

WINDOW_SIZE = 8
MOMENTUM_DAMPENING = 0.3
# How many recent messages to track for repetition detection
REPETITION_WINDOW = 5
# Jaccard similarity threshold to count as "repeating"
REPETITION_THRESHOLD = 0.4

_VALENCE_W = 0.35
_REPAIR_W = 0.35
_GAP_W = 0.15
_MOMENTUM_W = 0.15


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens for similarity comparison."""
    return set(re.findall(r'\b[a-z]{3,}\b', text.lower()))


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class InteractionAnalyzer:
    """
    Stateful session-level affect tracker.

    Call update() with each user message. The returned AffectState reflects
    accumulated signals across the session, not just the current message.

    State persists across hook invocations via a JSON file in the
    ephemeral directory, keyed by session ID.
    """

    # Max corrections to track (prevents unbounded growth)
    MAX_CORRECTIONS = 10

    def __init__(self):
        self._valence_window: list[str] = []
        self._repair_count: int = 0
        self._exchange_count: int = 0
        self._expected_effort: float = 1.0
        self._momentum: float = 0.0
        self._recent_messages: list[str] = []  # last N messages for repetition
        self._corrections: list[str] = []      # user corrections (for worklog)
        self._pivot_at: int = 0                # exchange count at last topic pivot

    def to_dict(self) -> dict:
        """Serialize internal state for persistence between hook calls."""
        return {
            "valence_window": self._valence_window,
            "repair_count": self._repair_count,
            "exchange_count": self._exchange_count,
            "expected_effort": self._expected_effort,
            "momentum": self._momentum,
            "recent_messages": self._recent_messages,
            "corrections": self._corrections,
            "pivot_at": self._pivot_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InteractionAnalyzer":
        """Restore from serialized state."""
        analyzer = cls()
        analyzer._valence_window = data.get("valence_window", [])
        analyzer._repair_count = data.get("repair_count", 0)
        analyzer._exchange_count = data.get("exchange_count", 0)
        analyzer._expected_effort = data.get("expected_effort", 1.0)
        analyzer._momentum = data.get("momentum", 0.0)
        analyzer._recent_messages = data.get("recent_messages", [])
        analyzer._corrections = data.get("corrections", [])
        analyzer._pivot_at = data.get("pivot_at", 0)
        return analyzer

    def update(
        self,
        message: str,
        exchange_count: int | None = None,
    ) -> AffectState:
        """
        Process a user message and return updated affect state.

        Args:
            message: The user's message text.
            exchange_count: Total exchanges in this task so far (for gap calc).
                If None, uses internal counter.

        Returns:
            Current AffectState reflecting accumulated session signals.
        """
        from .flags import get_flag

        if not get_flag("affect_awareness"):
            return AffectState()

        self._exchange_count = exchange_count if exchange_count is not None else self._exchange_count + 1

        # 1. Per-message valence via somatic
        somatic = classify_valence(message)
        self._valence_window.append(somatic.valence)
        if len(self._valence_window) > WINDOW_SIZE:
            self._valence_window = self._valence_window[-WINDOW_SIZE:]

        # 2. Repair tracking + correction logging
        repairs_this_message = sum(1 for p in _REPAIR_PATTERNS if p.search(message))
        if repairs_this_message > 0:
            self._repair_count += min(repairs_this_message, 2)
            # Log the correction — truncated for storage
            correction = message[:200].strip()
            if correction and len(self._corrections) < self.MAX_CORRECTIONS:
                self._corrections.append(correction)
        elif somatic.valence == "delight":
            self._repair_count = max(0, self._repair_count - 1)

        # 3. Repetition detection (needed before gap calc for pivot detection)
        repetition = self._compute_repetition(message)

        # 4. Expectation gap — resets on each new "just do X" pivot
        low_effort_signals = sum(1 for p in _LOW_EFFORT_PATTERNS if p.search(message))
        if low_effort_signals > 0:
            self._expected_effort = 1.0
            self._pivot_at = self._exchange_count  # new task starts here
        exchanges_since_pivot = self._exchange_count - self._pivot_at
        gap = exchanges_since_pivot / max(self._expected_effort, 0.5)

        # 5. Composite scores
        valence_frustration = self._valence_frustration_score()
        valence_satisfaction = self._valence_satisfaction_score()
        repair_signal = min(1.0, self._repair_count / 5.0)
        gap_signal = min(1.0, max(0.0, (gap - 1.0) / 4.0))

        frustration = (
            _VALENCE_W * valence_frustration
            + _REPAIR_W * repair_signal
            + _GAP_W * gap_signal
            + _MOMENTUM_W * max(0.0, -self._momentum)
        )

        satisfaction = (
            _VALENCE_W * valence_satisfaction
            + _REPAIR_W * (1.0 - repair_signal)
            + _GAP_W * (1.0 - gap_signal)
            + _MOMENTUM_W * max(0.0, self._momentum)
        )

        # 6. Degradation — high repetition + high repair = agent is looping
        degradation = min(1.0, (
            0.5 * repetition
            + 0.3 * repair_signal
            + 0.2 * min(1.0, max(0.0, (gap - 2.0) / 3.0))
        ))

        # 7. Update momentum with dampening
        if somatic.valence == "friction":
            target = -1.0
        elif somatic.valence == "delight":
            target = 1.0
        else:
            target = 0.0

        self._momentum += MOMENTUM_DAMPENING * (target - self._momentum)

        return AffectState(
            frustration=min(1.0, frustration),
            satisfaction=min(1.0, satisfaction),
            momentum=max(-1.0, min(1.0, self._momentum)),
            repair_count=self._repair_count,
            expectation_gap=gap,
            repetition=repetition,
            degradation=degradation,
            valence_history=list(self._valence_window),
            corrections=list(self._corrections),
        )

    def current_state(self) -> AffectState:
        """Return an AffectState computed from current internal state.

        Does not consume a new message — useful for reading accumulated affect
        at hook boundaries without injecting a synthetic message into the
        valence window.  All scores are derived from the same formulas used
        in update(), so the result is consistent with previous update() calls.

        Returns:
            AffectState reflecting all signals accumulated so far this session.
            If no messages have been processed yet, returns a neutral state.
        """
        from .flags import get_flag

        if not get_flag("affect_awareness"):
            return AffectState()

        # If no messages processed, return neutral baseline
        if not self._valence_window:
            return AffectState(
                momentum=max(-1.0, min(1.0, self._momentum)),
                repair_count=self._repair_count,
                corrections=list(self._corrections),
            )

        exchanges_since_pivot = self._exchange_count - self._pivot_at
        gap = exchanges_since_pivot / max(self._expected_effort, 0.5)

        valence_frustration = self._valence_frustration_score()
        valence_satisfaction = self._valence_satisfaction_score()
        repair_signal = min(1.0, self._repair_count / 5.0)
        gap_signal = min(1.0, max(0.0, (gap - 1.0) / 4.0))

        # Repetition: use mean pairwise similarity across the recent window
        # (no new message, so we measure cohesion of existing window)
        if len(self._recent_messages) >= 2:
            sims = []
            tokens = [_tokenize(m) for m in self._recent_messages]
            for i in range(len(tokens)):
                for j in range(i + 1, len(tokens)):
                    sims.append(_jaccard(tokens[i], tokens[j]))
            repetition = sum(sims) / len(sims) if sims else 0.0
        else:
            repetition = 0.0

        frustration = (
            _VALENCE_W * valence_frustration
            + _REPAIR_W * repair_signal
            + _GAP_W * gap_signal
            + _MOMENTUM_W * max(0.0, -self._momentum)
        )

        satisfaction = (
            _VALENCE_W * valence_satisfaction
            + _REPAIR_W * (1.0 - repair_signal)
            + _GAP_W * (1.0 - gap_signal)
            + _MOMENTUM_W * max(0.0, self._momentum)
        )

        degradation = min(1.0, (
            0.5 * repetition
            + 0.3 * repair_signal
            + 0.2 * min(1.0, max(0.0, (gap - 2.0) / 3.0))
        ))

        return AffectState(
            frustration=min(1.0, frustration),
            satisfaction=min(1.0, satisfaction),
            momentum=max(-1.0, min(1.0, self._momentum)),
            repair_count=self._repair_count,
            expectation_gap=gap,
            repetition=repetition,
            degradation=degradation,
            valence_history=list(self._valence_window),
            corrections=list(self._corrections),
        )

    def reset(self):
        """Reset all state. Call at session boundaries."""
        self.__init__()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_repetition(self, message: str) -> float:
        """Measure how much the current message repeats recent ones.

        Uses Jaccard similarity on word tokens. Returns the max similarity
        to any message in the recent window (0.0 = novel, 1.0 = identical).
        """
        current_tokens = _tokenize(message)
        max_sim = 0.0

        for prev in self._recent_messages:
            sim = _jaccard(current_tokens, _tokenize(prev))
            max_sim = max(max_sim, sim)

        # Update window
        self._recent_messages.append(message)
        if len(self._recent_messages) > REPETITION_WINDOW:
            self._recent_messages = self._recent_messages[-REPETITION_WINDOW:]

        return max_sim

    def _valence_frustration_score(self) -> float:
        """Fraction of recent valences that are friction."""
        if not self._valence_window:
            return 0.0
        return self._valence_window.count("friction") / len(self._valence_window)

    def _valence_satisfaction_score(self) -> float:
        """Fraction of recent valences that are delight."""
        if not self._valence_window:
            return 0.0
        return self._valence_window.count("delight") / len(self._valence_window)


# ------------------------------------------------------------------
# Coherence probe — expensive, on-demand degradation detection
# ------------------------------------------------------------------

COHERENCE_PROBE_PROMPT = """You are evaluating a user's request. Respond with ONLY a brief (2-3 sentence) plan for how to address it. No code, no implementation — just the approach.

User request: {message}"""


@dataclass
class CoherenceResult:
    """Result of a coherence probe — measures response variance."""
    variance: float         # 0.0 (identical) to 1.0 (completely different)
    likely_degraded: bool   # True if variance suggests context pollution
    response_a: str
    response_b: str


def coherence_probe(message: str) -> CoherenceResult:
    """Send the same prompt to 2 parallel LLM calls and measure variance.

    High variance (responses diverge significantly) suggests the model's
    context is polluted or the task is ambiguous. Combined with repair/
    repetition signals, high variance strongly indicates degradation.

    This is EXPENSIVE (~1-2s, 2 API calls). Only call when frustration
    threshold is crossed.

    Returns:
        CoherenceResult with variance score and the two responses.
    """
    from .llm import call_llm

    prompt = COHERENCE_PROBE_PROMPT.format(message=message[:500])

    # Fire both calls in parallel with temperature > 0 to get variance
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(
            call_llm, prompt, max_tokens=256, temperature=0.7
        )
        future_b = executor.submit(
            call_llm, prompt, max_tokens=256, temperature=0.7
        )
        response_a = future_a.result()
        response_b = future_b.result()

    # Measure divergence via Jaccard distance on tokens
    tokens_a = _tokenize(response_a)
    tokens_b = _tokenize(response_b)
    similarity = _jaccard(tokens_a, tokens_b)
    variance = 1.0 - similarity

    # High variance (> 0.6) with the same prompt suggests the model
    # would give inconsistent answers — a degradation signal
    return CoherenceResult(
        variance=variance,
        likely_degraded=variance > 0.6,
        response_a=response_a,
        response_b=response_b,
    )


# ------------------------------------------------------------------
# Persistence helpers
# ------------------------------------------------------------------

def _state_path(base_dir: Path, session_id: str) -> Path:
    """Path to the affect state JSON file for a session."""
    return base_dir / "ephemeral" / f".affect-{session_id}.json"


def load_analyzer(base_dir: Path, session_id: str) -> InteractionAnalyzer:
    """Load analyzer state from disk, or create fresh if none exists."""
    path = _state_path(base_dir, session_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return InteractionAnalyzer.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load affect state: %s", e)
    return InteractionAnalyzer()


def save_analyzer(analyzer: InteractionAnalyzer, base_dir: Path, session_id: str) -> None:
    """Persist analyzer state to disk."""
    path = _state_path(base_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analyzer.to_dict()), encoding="utf-8")


def format_guidance(state: AffectState) -> str:
    """Format affect-aware guidance for injection into the assistant prompt.

    Three modes:
    - Degradation detected: recommend compaction or new session
    - Task difficulty: write a plan, maintain a WORKLOG, don't retry failed approaches
    - No guidance needed: return empty string

    Returns empty string if no intervention is needed.
    """
    if not state.needs_guidance:
        return ""

    if state.likely_degraded:
        parts = ["[Affect signal: session quality may be degrading"]
        if state.repetition > REPETITION_THRESHOLD:
            parts.append("— user is repeating themselves")
        if state.repair_count >= 3:
            parts.append(f"— {state.repair_count} repair moves (agent not learning)")
        parts.append(
            "— suggest compacting context or starting a fresh session"
            " rather than retrying the same approach]"
        )
        return " ".join(parts)

    # Task difficulty — switch from ad-hoc to structured execution
    lines = [
        f"[Affect signal: task is proving difficult ({state.repair_count} corrections).",
        "STOP flying blind. Before your next attempt:",
        "1. Write a concrete plan (steps, expected outcome per step) and follow it strictly.",
        "2. Check/create .WORKLOG.md — log what you are about to try BEFORE trying it.",
        "   If an approach is already logged as failed, do NOT retry it.",
    ]

    if state.corrections:
        lines.append("User corrections so far (do not repeat these mistakes):")
        for c in state.corrections[-5:]:  # last 5 corrections
            lines.append(f"  - \"{c}\"")

    lines.append("]")
    return "\n".join(lines)
