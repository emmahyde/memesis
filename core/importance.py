"""
Importance calibration — counteract the collapsed-distribution problem.

The LLM importance scorer clusters its output around 0.50–0.60, leaving the
score with almost no ranking variance (canvas review 2026-05-15 §3). This module
applies deterministic post-LLM adjustments so the stored `importance` carries
real signal:

  * Per-kind floor — a memory of a given curated kind is at least as important
    as that kind's floor (the §1 taxonomy table). A `goal` is never trivial; a
    `fact` rarely load-bearing.
  * Additive axes — concrete, verifiable signal nudges the score up: an action
    item to act on, or numeric evidence backing the claim.

The fuzzy axes from §3 (external-validity, vendored-ownership,
mechanically-enforced) need judgment and stay in the LLM prompt rather than a
brittle heuristic here.
"""

from __future__ import annotations

import re

# Per-kind importance floor — aligned with consolidation-prompt bands
# (core/prompts.py:270-282, consequence-anchored rubric from commit 8bccf7c).
# Floors raised 2026-05-23 to match prompt prose; calibrate_importance applies
# max(score, floor) so raising floors only lifts under-scored rows.
KIND_IMPORTANCE_FLOOR: dict[str, float] = {
    "correction": 0.85,  # explicit user fix; prompt band 0.85–1.00
    "directive": 0.85,   # behavioral imperative; prompt band 0.85–1.00
    "goal": 0.85,        # north-star objective; prompt band 0.85+
    "decision": 0.75,    # settled choice; prompt band 0.75–0.84
    "preference": 0.75,  # subjective stance; prompt band 0.75–0.84 (was opinion 0.55)
    "lesson": 0.70,      # pattern from >=2 incidents; unchanged
    "fact": 0.30,        # pinned claim; unchanged
}

# Additive-axis bonus magnitude.
_ACTION_ITEM_BONUS = 0.10
_NUMERIC_EVIDENCE_BONUS = 0.10

# An action item — something with a concrete future response.
_ACTION_ITEM_RE = re.compile(
    r"\b(should|must|need to|needs to|todo|fixme|unresolved|deferred|open question)\b",
    re.IGNORECASE,
)
# Numeric evidence — a measurement, ratio, percentage, or distribution.
_NUMERIC_EVIDENCE_RE = re.compile(
    r"\d+\s*%|\b\d+\s*x\b|\b\d+/\d+(?:/\d+)*\b|\b\d+\.\d+\b",
    re.IGNORECASE,
)

# Distribution-collapse detection (§3): flag if too many scores land mid-band.
COLLAPSE_BAND = (0.45, 0.65)
COLLAPSE_FRACTION_THRESHOLD = 0.60


def has_action_item(text: str) -> bool:
    """True if the text carries an action item — something to act on later."""
    return bool(text) and bool(_ACTION_ITEM_RE.search(text))


def has_numeric_evidence(text: str) -> bool:
    """True if the text carries numeric evidence (measurement / ratio / percentage)."""
    return bool(text) and bool(_NUMERIC_EVIDENCE_RE.search(text))


def calibrate_importance(
    base: float | None,
    kind: str | None = None,
    content: str = "",
    # Legacy parameter alias kept for call sites not yet updated; ignored when
    # kind is provided.
    memory_kind: str | None = None,
) -> float:
    """Apply deterministic calibration to an LLM-scored importance.

    Raises the score to the per-kind floor, then adds bounded bonuses for an
    action item and for numeric evidence. Result is clamped to [0.0, 1.0].
    """
    score = 0.5 if base is None else float(base)

    effective_kind = kind or memory_kind or ""
    floor = KIND_IMPORTANCE_FLOOR.get(effective_kind)
    if floor is not None:
        score = max(score, floor)

    text = content or ""
    if has_action_item(text):
        score += _ACTION_ITEM_BONUS
    if has_numeric_evidence(text):
        score += _NUMERIC_EVIDENCE_BONUS

    return max(0.0, min(1.0, score))


def distribution_is_collapsed(scores: list[float]) -> tuple[bool, float]:
    """Return (collapsed, fraction_in_band) for an importance distribution.

    Collapsed when more than COLLAPSE_FRACTION_THRESHOLD of scores fall inside
    COLLAPSE_BAND — the symptom the calibration is meant to cure.
    """
    if not scores:
        return False, 0.0
    lo, hi = COLLAPSE_BAND
    in_band = sum(1 for s in scores if lo <= s <= hi)
    fraction = in_band / len(scores)
    return fraction > COLLAPSE_FRACTION_THRESHOLD, fraction
