"""
Affect aggregation for transcript-extraction windows.

Adapter over `core.somatic.classify_valence` (the existing per-text valence
classifier) that aggregates affect across all [USER ...] lines in a rendered
hierarchical extraction window. Used as an importance prior on Stage 1
observations.

Design:
- Reuses the existing somatic lexicon — does not duplicate detection logic.
- Aggregates by max-intensity over user turns in the window (Kensinger 2009:
  the strongest emotional moment encodes the entire surrounding span).
- Captures up to 2 evidence quotes per window so issue-card synthesis can
  attribute reactions to specific user lines.
- Stateless and deterministic — no LLM, no session state.

Theoretical basis:
- Kensinger 2009 (Emotion 9:99-113) — emotional encoding privileges entire
  episodes around high-affect moments.
- Park et al. 2023 (arXiv 2304.03442 §3.2) — emotional weight as importance
  heuristic.
- Damasio 1994 — somatic-marker pre-conscious importance gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.somatic import classify_valence

_USER_LINE_RE = re.compile(r"^\[USER(?::L\d+)?\]\s*(.+)$", re.MULTILINE)


@dataclass
class WindowAffect:
    """Aggregated affect signal over the user turns in one extraction window."""

    valence: str = "neutral"  # neutral | friction | surprise | delight | uncertainty | mixed
    max_boost: float = 0.0  # max somatic importance boost across user turns
    has_repetition: bool = False
    has_pushback: bool = False
    has_uncertainty: bool = False  # user expressed doubt/nervousness in this window
    evidence_quotes: list[str] = field(default_factory=list)
    user_turn_count: int = 0
    nontrivial_turn_count: int = 0  # user turns ≥60 chars

    @property
    def importance_prior(self) -> float:
        """Capped additive prior to apply on extracted observations.

        Capped at +0.20 to keep the LLM-provided importance score dominant
        and prevent affect from blowing past the 0..1 range. Repetition adds
        +0.05 because user repeating themselves is a robust signal that
        prior context was missed and the reiteration is load-bearing.
        """
        base = min(self.max_boost, 0.15)
        if self.has_repetition:
            base += 0.05
        return min(base, 0.20)

    def to_dict(self) -> dict:
        return {
            "valence": self.valence,
            "max_boost": round(self.max_boost, 3),
            "importance_prior": round(self.importance_prior, 3),
            "has_repetition": self.has_repetition,
            "has_pushback": self.has_pushback,
            "evidence_quotes": self.evidence_quotes[:2],
            "user_turn_count": self.user_turn_count,
            "nontrivial_turn_count": self.nontrivial_turn_count,
        }


# Interruption marker — appears in tool/assistant lines, not user turns.
# A user interrupt is behavioral friction regardless of lexical content.
_INTERRUPTION_RE = re.compile(
    r"\[Request interrupted by user\]|"
    r"request.*?cancelled|"
    r"\[Request cancelled\]",
    re.IGNORECASE,
)

# Quick lexicon for repetition / pushback that somatic.py doesn't surface
# as a separate signal. These are high-precision, low-recall patterns.
_REPETITION_RE = re.compile(
    r"\b(again|third\s+time|second\s+time|once\s+more|"
    r"already\s+(said|told|asked)|like\s+I\s+said)\b",
    re.IGNORECASE,
)
_PUSHBACK_RE = re.compile(
    r"\b(actually|wait|not\s+quite|not\s+really|wrong|incorrect|"
    r"redo|rollback|rather|instead|pivot|reject(ed)?)\b",
    re.IGNORECASE,
)


def aggregate_window_affect(window_text: str) -> WindowAffect:
    """Score affect across all user turns in a rendered extraction window."""
    if not window_text:
        return WindowAffect()

    user_texts = _USER_LINE_RE.findall(window_text)
    if not user_texts:
        return WindowAffect()

    affect = WindowAffect(user_turn_count=len(user_texts))
    valences: list[str] = []
    max_boost = 0.0
    quotes: list[tuple[float, str]] = []

    # Interruption markers appear outside [USER] lines — scan full window.
    if _INTERRUPTION_RE.search(window_text):
        affect.has_pushback = True
        max_boost = max(max_boost, 0.25)  # friction-level boost
        valences.append("friction")
        quotes.append((0.25, "[Request interrupted by user]"))

    for line in user_texts:
        if len(line) >= 60:
            affect.nontrivial_turn_count += 1
        result = classify_valence(line)
        if result.valence != "neutral":
            valences.append(result.valence)
        if result.importance_boost > max_boost:
            max_boost = result.importance_boost
        if result.importance_boost > 0:
            quote = line[:160].rstrip()
            quotes.append((result.importance_boost, quote))
        if _REPETITION_RE.search(line):
            affect.has_repetition = True
        if _PUSHBACK_RE.search(line):
            affect.has_pushback = True
        if result.emotion_scores.get("uncertainty", 0) > 0:
            affect.has_uncertainty = True

    affect.max_boost = max_boost

    if valences:
        if "friction" in valences and "delight" in valences:
            affect.valence = "mixed"
        else:
            counts = {v: valences.count(v) for v in set(valences)}
            affect.valence = max(counts.items(), key=lambda x: x[1])[0]

    quotes.sort(key=lambda x: x[0], reverse=True)
    affect.evidence_quotes = [q for _, q in quotes[:2]]
    return affect


def format_affect_hint(affect: WindowAffect) -> str:
    """Render WindowAffect as a Stage 1 prompt hint block.

    The somatic detector saw the user turns before the LLM did. Surfacing that
    signal lets the LLM (a) treat friction/delight as a strong durability cue,
    and (b) attribute user reaction to specific facts. Empty for neutral
    windows so the prompt stays compact.
    """
    if affect.max_boost == 0.0 and not affect.has_pushback and not affect.has_repetition and not affect.has_uncertainty:
        return ""
    parts: list[str] = ["AFFECT HINT (somatic pre-pass over user turns):"]
    parts.append(f"  valence={affect.valence}  intensity={affect.max_boost:.2f}")
    flags: list[str] = []
    if affect.has_pushback:
        flags.append("pushback")
    if affect.has_repetition:
        flags.append("repetition")
    if affect.has_uncertainty:
        flags.append("uncertainty")
    if flags:
        parts.append(f"  signals: {', '.join(flags)}")
    if affect.evidence_quotes:
        quoted = "; ".join(f'"{q}"' for q in affect.evidence_quotes[:2])
        parts.append(f"  evidence: {quoted}")
    guidance = (
        "Use this signal: non-neutral valence + nontrivial intensity is a strong "
        "durability cue. Pushback or repetition indicates a load-bearing correction "
        "the user already had to surface — bias toward extracting (not skipping) "
        "and prefer kind=correction or kind=preference when appropriate."
    )
    if affect.has_uncertainty:
        guidance += (
            " Uncertainty signal: user expressed doubt or nervousness about a decision "
            "in this window. Capture their uncertain state as a fact — it is durable "
            "and true regardless of what was decided. Do not lower the observation's "
            "importance or confidence because of the uncertainty."
        )
    parts.append(guidance)
    return "\n".join(parts) + "\n"


def apply_affect_prior(observations: list[dict], affect: WindowAffect) -> list[dict]:
    """Attach affect metadata + boost importance on observations from this window.

    Mutates observations in-place and returns the same list. Each observation
    gets:
      - importance bumped by affect.importance_prior, capped at 1.0
      - new field `affect` with the window's aggregated signal
      - new field `raw_importance_pre_affect` preserving the original score
    """
    if not observations:
        return observations
    boost = affect.importance_prior
    if boost <= 0:
        for obs in observations:
            obs["affect"] = affect.to_dict()
        return observations
    for obs in observations:
        original = float(obs.get("importance", 0.0))
        obs["raw_importance_pre_affect"] = original
        obs["importance"] = min(1.0, original + boost)
        obs["affect"] = affect.to_dict()
    return observations
