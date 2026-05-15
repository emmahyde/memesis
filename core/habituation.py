"""
Per-project event frequency model for habituation-based observation filtering.

Tracks how often each event type has been seen in a project and computes
a habituation factor: novel events get 1.0, routine events decay toward 0.

Model: exponential moving average over wall-clock time.

    count_new = count_old * exp(-elapsed_seconds / HALF_LIFE_SEC * ln(2)) + 1
    factor    = 1.0 / (1.0 + ln(count))

A monotonic counter (the previous model) ratchets event types into permanent
suppression — once `domain_knowledge` hits 302 the factor is pinned at ~0.15
forever, even after weeks of silence. The decay rebuilds salience over time:
a type that hasn't fired in HALF_LIFE_SEC loses half its count, so newly
re-emerging signal is treated as novel again.
"""

import json
import logging
import math
import re
import time
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

# 7-day half-life: a routine event type that goes quiet for a week loses half
# its accumulated count. Tunable via MEMESIS_HABITUATION_HALF_LIFE_SEC env var.
HALF_LIFE_SEC_DEFAULT = 7 * 24 * 3600


def _half_life() -> float:
    import os
    raw = os.environ.get("MEMESIS_HABITUATION_HALF_LIFE_SEC")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(HALF_LIFE_SEC_DEFAULT)


class _CountsProxy:
    """Dict-like view over HabituationModel._state for legacy `_counts` API.

    Reads return the (undecayed) raw count. Writes set count + stamp ts=now.
    Used by tests that pre-seed counts via `model._counts["x"] = 50`.
    """

    def __init__(self, model: "HabituationModel"):
        self._model = model

    def __getitem__(self, key: str) -> float:
        slot = self._model._state.get(key)
        if slot is None:
            raise KeyError(key)
        return slot["count"]

    def __setitem__(self, key: str, value: float):
        self._model._state[key] = {"count": float(value), "ts": time.time()}

    def __contains__(self, key: str) -> bool:
        return key in self._model._state

    def __eq__(self, other) -> bool:
        if isinstance(other, dict):
            return {k: v["count"] for k, v in self._model._state.items()} == other
        return NotImplemented

    def __repr__(self) -> str:
        return repr({k: v["count"] for k, v in self._model._state.items()})


class HabituationModel:
    """Per-project event frequency tracker with time-decayed counts."""

    def __init__(self, base_dir: Path):
        self._path = base_dir / "habituation.json"
        self._state: dict[str, dict] = self._load()

    @property
    def _counts(self) -> _CountsProxy:
        return _CountsProxy(self)

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as f:
                raw = json.load(f)
        except Exception:
            return {}

        # Migrate legacy {event_type: int} -> {event_type: {count, ts}}
        if raw and all(isinstance(v, (int, float)) for v in raw.values()):
            now = time.time()
            return {k: {"count": float(v), "ts": now} for k, v in raw.items()}

        # Defensive normalization for new schema
        out: dict[str, dict] = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "count" in v and "ts" in v:
                out[k] = {"count": float(v["count"]), "ts": float(v["ts"])}
        return out

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._state, f)

    def extract_event_signature(self, observation_block: str) -> str:
        """Parse event type from an observation block header."""
        match = _OBS_HEADER_RE.search(observation_block)
        if match:
            return match.group(1).lower()
        return "untyped"

    # These event types are always injected regardless of habituation count.
    # decision_context and shared_insight added (panel recommendation): their
    # value is in the per-instance specifics, not type-level novelty, so the
    # raw frequency counter wrongly suppressed them.
    _NEVER_SUPPRESS = frozenset({
        "correction",
        "preference_signal",
        "self_observation",
        "decision_context",
        "shared_insight",
        "untyped",
    })

    def _decayed_count(self, event_type: str, now: float) -> float:
        slot = self._state.get(event_type)
        if not slot:
            return 0.0
        elapsed = max(0.0, now - slot["ts"])
        decay = math.exp(-elapsed / _half_life() * math.log(2))
        return slot["count"] * decay

    def get_factor(self, event_type: str) -> float:
        """Compute habituation factor for an event type."""
        from .flags import get_flag

        if not get_flag("habituation_baseline"):
            return 1.0

        if event_type in self._NEVER_SUPPRESS:
            return 1.0

        count = self._decayed_count(event_type, time.time())
        # Below 1.0 the log goes negative and factor explodes. Treat any
        # subunit decayed count as effectively novel.
        if count < 1.0:
            return 1.0
        return 1.0 / (1.0 + math.log(count))

    def record_event(self, event_type: str):
        """Increment decayed count for event type and persist."""
        now = time.time()
        new_count = self._decayed_count(event_type, now) + 1.0
        self._state[event_type] = {"count": new_count, "ts": now}
        self._save()

    def filter_observations(
        self, ephemeral_content: str, threshold: float = 0.15
    ) -> tuple[str, int]:
        """Filter observation blocks by habituation factor.

        Returns (filtered_content, suppressed_count).
        When feature flag disabled, returns content unchanged.

        Counts are recorded AFTER the keep/drop decision for every block in
        the batch — the previous implementation recorded mid-loop, letting
        the first occurrence in a buffer contaminate the factor used for
        subsequent occurrences in the same batch.
        """
        from .flags import get_flag

        if not get_flag("habituation_baseline"):
            return ephemeral_content, 0

        blocks = _OBS_SPLIT_RE.split(ephemeral_content)
        kept = []
        suppressed = 0
        pending_events: list[str] = []

        for block in blocks:
            if not block.startswith("## ["):
                kept.append(block)
                continue

            event_type = self.extract_event_signature(block)
            factor = self.get_factor(event_type)
            pending_events.append(event_type)

            if factor >= threshold:
                kept.append(block)
            else:
                suppressed += 1

        for ev in pending_events:
            self.record_event(ev)

        return "".join(kept), suppressed
