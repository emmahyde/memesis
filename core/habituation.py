"""
Per-project event frequency model for habituation-based observation filtering.

Tracks how often each event type has been seen in a project and computes
a habituation factor: novel events get 1.0, routine events decay toward 0.

Formula: habituation_factor = 1.0 / (1.0 + ln(count))

At count=0: 1.0 (novel)
At count=10: ~0.30 (habituated)
At count=100: ~0.18 (deeply habituated)
"""

import json
import logging
import math
import re
from pathlib import Path

# RISK-11: experimental flag scaffold.
# habituation is production-validated (event-frequency decay model confirmed through consolidation filtering).
# Opt-in override: include "habituation" in MEMESIS_EXPERIMENTAL_MODULES env var to force-exclude from scoring.
experimental: bool = False

logger = logging.getLogger(__name__)

# Pattern matching observation block headers: ## [2026-03-29T12:00:00] correction
# Matches "## [timestamp] event_type" where event_type is a word (not punctuation).
# The capture group intentionally rejects markdown bullets ("-", "*") and similar.
_OBS_HEADER_RE = re.compile(r"^##\s+\[\S+\]\s+(\w\S*)", re.MULTILINE)
# Split on observation headers while keeping the delimiter
_OBS_SPLIT_RE = re.compile(r"(?=^##\s+\[)", re.MULTILINE)


class HabituationModel:
    """Per-project event frequency tracker with observation filtering."""

    def __init__(self, base_dir: Path):
        self._path = base_dir / "habituation.json"
        self._counts: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._counts, f)

    def extract_event_signature(self, observation_block: str) -> str:
        """Parse event type from an observation block header."""
        match = _OBS_HEADER_RE.search(observation_block)
        if match:
            return match.group(1).lower()
        return "untyped"

    # These event types are always injected regardless of habituation count.
    # Corrections and preference signals are the highest-value observations —
    # suppressing them defeats the purpose of the memory system.
    _NEVER_SUPPRESS = frozenset({"correction", "preference_signal", "self_observation"})

    def get_factor(self, event_type: str) -> float:
        """Compute habituation factor for an event type."""
        from .flags import get_flag

        if not get_flag("habituation_baseline"):
            return 1.0

        if event_type in self._NEVER_SUPPRESS:
            return 1.0

        count = self._counts.get(event_type, 0)
        if count <= 0:
            return 1.0
        return 1.0 / (1.0 + math.log(count))

    def record_event(self, event_type: str):
        """Increment count for event type and persist."""
        self._counts[event_type] = self._counts.get(event_type, 0) + 1
        self._save()

    def filter_observations(
        self, ephemeral_content: str, threshold: float = 0.3
    ) -> tuple[str, int]:
        """Filter observation blocks by habituation factor.

        Returns (filtered_content, suppressed_count).
        When feature flag disabled, returns content unchanged.
        """
        from .flags import get_flag

        if not get_flag("habituation_baseline"):
            return ephemeral_content, 0

        blocks = _OBS_SPLIT_RE.split(ephemeral_content)
        kept = []
        suppressed = 0

        for block in blocks:
            # Preserve non-observation content (session header, etc.)
            if not block.startswith("## ["):
                kept.append(block)
                continue

            event_type = self.extract_event_signature(block)
            factor = self.get_factor(event_type)
            self.record_event(event_type)

            if factor >= threshold:
                kept.append(block)
            else:
                suppressed += 1

        return "".join(kept), suppressed
