"""
Feature flags for A/B testing memesis capabilities.

Reads from {base_dir}/flags.json if it exists, otherwise returns defaults.
All flags default to True (features enabled) so the system works without
a flags file.

Usage:
    from core.flags import get_flag

    if get_flag("hybrid_rrf"):
        # use hybrid search
    else:
        # fallback to FTS-only
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULTS = {
    "hybrid_rrf": True,
    "prompt_aware_tier2": True,
    "thompson_sampling": True,
    "provenance_signals": True,
    "orienting_detector": True,
    "habituation_baseline": True,
    "somatic_markers": True,
    "replay_priority": True,
    "sm2_spaced_injection": True,
    "reconsolidation": True,
    "saturation_decay": True,
    "integration_factor": True,
    "affect_awareness": True,
}

_cache: dict | None = None


def _load() -> dict:
    """Load flags from disk, merging with defaults."""
    global _cache
    if _cache is not None:
        return _cache

    from .database import get_base_dir

    flags = dict(DEFAULTS)
    base_dir = get_base_dir()
    if base_dir:
        flags_path = base_dir / "flags.json"
        if flags_path.exists():
            try:
                with open(flags_path) as f:
                    overrides = json.load(f)
                flags.update(overrides)
                logger.debug("Loaded flags from %s: %s", flags_path, overrides)
            except Exception as e:
                logger.warning("Failed to read flags.json: %s", e)

    _cache = flags
    return flags


def get_flag(name: str) -> bool:
    """Get a feature flag value. Unknown flags default to True."""
    return _load().get(name, True)


def reload():
    """Clear cached flags so next get_flag() re-reads from disk."""
    global _cache
    _cache = None
