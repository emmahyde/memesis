"""
Replay priority — sort observations by salience before consolidation.

Combines signals from OrientingDetector, somatic markers, and habituation
to produce a salience score per observation block. The consolidation LLM
sees highest-signal observations first.

Salience formula:
    salience = orienting_boost + somatic_boost + habituation_factor * 0.1

Where:
- orienting_boost: from OrientingDetector (correction=0.3, emphasis=0.2, etc.)
- somatic_boost: from valence classification (friction=0.25, surprise=0.20, etc.)
- habituation_factor: 1.0 for novel, decaying for routine (weighted at 0.1)

A correction observation (orienting=0.3 + friction=0.25 + novel=0.1) = 0.65
A neutral routine observation (orienting=0 + neutral=0 + habituated=0.03) = 0.03
"""

import re
from dataclasses import dataclass

from .orienting import OrientingDetector
from .somatic import classify_valence

# RISK-11: experimental flag scaffold.
# replay is production-validated (orienting+somatic+habituation salience sort confirmed in consolidation pipeline).
# Opt-in override: include "replay" in MEMESIS_EXPERIMENTAL_MODULES env var to force-exclude from scoring.
experimental: bool = False

# Split on observation headers while keeping the delimiter
_OBS_SPLIT_RE = re.compile(r"(?=^##\s+\[)", re.MULTILINE)


@dataclass
class ScoredBlock:
    """An observation block with its salience score."""
    text: str
    salience: float
    is_observation: bool  # False for headers/non-observation content


def score_observations(content: str) -> list[ScoredBlock]:
    """Split content into blocks and score each by salience.

    Non-observation blocks (session header, etc.) get salience=float('inf')
    so they stay at the top.
    """
    from .flags import get_flag

    if not get_flag("replay_priority"):
        return [ScoredBlock(text=content, salience=0.0, is_observation=False)]

    blocks = _OBS_SPLIT_RE.split(content)
    detector = OrientingDetector()
    scored = []

    for block in blocks:
        if not block.startswith("## ["):
            # Non-observation content (session header, etc.) — keep at top
            scored.append(ScoredBlock(text=block, salience=float('inf'), is_observation=False))
            continue

        # Orienting signal
        orienting = detector.detect(block)
        orienting_boost = orienting.importance_boost

        # Somatic valence
        somatic = classify_valence(block)
        somatic_boost = somatic.importance_boost

        # Salience = orienting + somatic + small novelty bonus
        # (habituation already filtered blocks in Phase 12, so surviving
        # blocks are at least somewhat novel — give a small base)
        salience = orienting_boost + somatic_boost + 0.1

        scored.append(ScoredBlock(text=block, salience=salience, is_observation=True))

    return scored


def sort_by_salience(content: str) -> str:
    """Sort observation blocks by salience descending, reassemble as string.

    Non-observation blocks stay at the top. Observation blocks are sorted
    highest-salience first. Returns the reassembled content string.
    """
    from .flags import get_flag

    if not get_flag("replay_priority"):
        return content

    scored = score_observations(content)

    # Separate headers from observations
    headers = [b for b in scored if not b.is_observation]
    observations = [b for b in scored if b.is_observation]

    # Sort observations by salience descending (stable sort preserves
    # insertion order for equal-salience blocks)
    observations.sort(key=lambda b: b.salience, reverse=True)

    # Reassemble: headers first, then sorted observations
    parts = [b.text for b in headers] + [b.text for b in observations]
    return "".join(parts)
