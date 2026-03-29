"""
Somatic marker classification — tags observations with emotional valence.

Four valence categories:
- neutral: no emotional signal detected
- friction: frustration, conflict, correction, failure
- surprise: unexpected result, discovery, contradiction
- delight: success, praise, excitement, satisfaction

Non-neutral valence bumps the observation's importance score.
Pure rule-based — no LLM call.
"""

import re
from dataclasses import dataclass

# Importance boosts by valence (added to base importance)
VALENCE_BOOSTS = {
    "neutral": 0.0,
    "friction": 0.25,
    "surprise": 0.20,
    "delight": 0.10,
}

# Pattern lists — checked in priority order (friction > surprise > delight > neutral)
_FRICTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bno[,.]?\s+that'?s\s+wrong\b",
        r"\bactually\b",
        r"\bnot\s+what\s+I\b",
        r"\bI\s+said\b",
        r"\bfrustrat",
        r"\banno[yi]",
        r"\bwrong\b",
        r"\bbroken\b",
        r"\bfail(?:ed|ure|ing|s)?\b",
        r"\berror\b",
        r"\bbug\b",
        r"\bcrash(?:ed|es|ing)?\b",
        r"\bregress(?:ion|ed)?\b",
        r"\bwaste[ds]?\b",
        r"\bstuck\b",
        r"\bblock(?:ed|er|ing)?\b",
        r"\bconfus(?:ed|ing)\b",
        r"\bugh\b",
        r"\barg\b",
        r"\bfuck\b",
        r"\bshit\b",
        r"\bdamn\b",
        r"\bforget\s+(?:it|this)\b",
        r"\bdelete\s+all\b",
        r"\bnuke\b",
    ]
]

_SURPRISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bunexpect(?:ed|edly)?\b",
        r"\bsurpris(?:ed|ing|ingly)?\b",
        r"\bwow\b",
        r"\bwhoa\b",
        r"\bwait\s+what\b",
        r"\bholy\s+shit\b",
        r"\boh\s+my\b",
        r"\bdidn'?t\s+(?:know|expect|realize)\b",
        r"\bturns?\s+out\b",
        r"\bcontradicts?\b",
        r"\bactually\s+(?:it|this|that)\s+(?:is|was)\b",
        r"\bnever\s+(?:seen|noticed|knew)\b",
        r"\bdiscover(?:ed|y)?\b",
    ]
]

_DELIGHT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bperfect\b",
        r"\bexcellent\b",
        r"\bamazin[gly]?\b",
        r"\bbeautiful\b",
        r"\blove\s+(?:it|this|that)\b",
        r"\bgreat\s+(?:job|work)\b",
        r"\bnice\b",
        r"\bawesome\b",
        r"\bbrilliant\b",
        r"\bexactly\s+(?:right|what)\b",
        r"\bnailed\s+it\b",
        r"\byes!\b",
        r"\b(?:we|you)'?re\s+genius",
        r"\bship\s+it\b",
        r"\bcelebrat",
    ]
]


@dataclass
class SomaticResult:
    """Result of somatic marker classification."""
    valence: str  # "neutral", "friction", "surprise", "delight"
    importance_boost: float  # how much to add to base importance
    matched_patterns: list[str]  # which patterns fired (for debugging)


def classify_valence(text: str) -> SomaticResult:
    """Classify the emotional valence of an observation text.

    Priority order: friction > surprise > delight > neutral.
    Returns the highest-priority match.
    """
    from .flags import get_flag

    if not get_flag("somatic_markers"):
        return SomaticResult(valence="neutral", importance_boost=0.0, matched_patterns=[])

    # Check in priority order
    friction_matches = [p.pattern for p in _FRICTION_PATTERNS if p.search(text)]
    if friction_matches:
        return SomaticResult(
            valence="friction",
            importance_boost=VALENCE_BOOSTS["friction"],
            matched_patterns=friction_matches,
        )

    surprise_matches = [p.pattern for p in _SURPRISE_PATTERNS if p.search(text)]
    if surprise_matches:
        return SomaticResult(
            valence="surprise",
            importance_boost=VALENCE_BOOSTS["surprise"],
            matched_patterns=surprise_matches,
        )

    delight_matches = [p.pattern for p in _DELIGHT_PATTERNS if p.search(text)]
    if delight_matches:
        return SomaticResult(
            valence="delight",
            importance_boost=VALENCE_BOOSTS["delight"],
            matched_patterns=delight_matches,
        )

    return SomaticResult(valence="neutral", importance_boost=0.0, matched_patterns=[])
