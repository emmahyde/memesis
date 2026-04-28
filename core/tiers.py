"""Tier policy constants for the memory salience/TTL system.

Tier definitions
----------------
T1 — instinctive:  Seed memories; never expire.  Low activation floor because
     these are foundational beliefs that should surface even when dormant.
T2 — crystallized: Long-lived consolidated knowledge; 180-day TTL.  Low floor
     — these memories are high-value and should survive low-traffic windows.
T3 — consolidated: Active working knowledge; 90-day TTL.  Moderate floor —
     memories that aren't retrieved regularly should be pruned sooner.
T4 — ephemeral:    Short-term observations; 30-day TTL.  Higher floor — only
     keep ephemeral memories that are actively relevant.

Activation floor rationale (C2)
--------------------------------
T1/T2 floors are set low (0.05) because these are curated, high-value memories
that should not be pruned on activation alone.  T3/T4 floors are set higher
(0.15) to aggressively reclaim space from stale short-horizon memories.

Decay tau values (from compute_activation docstring in core/observability.py)
------------------------------------------------------------------------------
T1: 720h (30 days) — very slow decay; instinctive memories stay salient
T2: 168h (7 days)  — moderate decay; crystallized memories fade weekly
T3:  48h (2 days)  — fast decay; consolidated memories need regular access
T4:  12h           — very fast decay; ephemeral memories fade in half a day
"""

# ---------------------------------------------------------------------------
# Stage → Tier mapping
# ---------------------------------------------------------------------------

_STAGE_TO_TIER: dict[str, str] = {
    "instinctive": "T1",
    "crystallized": "T2",
    "consolidated": "T3",
    "ephemeral": "T4",
}


def stage_to_tier(stage: str) -> str:
    """Return the tier string for a given memory stage.

    Parameters
    ----------
    stage:
        The ``Memory.stage`` value (e.g. ``"instinctive"``, ``"crystallized"``,
        ``"consolidated"``, ``"ephemeral"``).  Unknown or ``"archived"`` stages
        fall through to ``"T4"`` (most aggressive expiry) as a safe default.

    Returns
    -------
    str
        One of ``"T1"``, ``"T2"``, ``"T3"``, ``"T4"``.
    """
    return _STAGE_TO_TIER.get(stage, "T4")


# ---------------------------------------------------------------------------
# TTL constants  (B2, B4)
# ---------------------------------------------------------------------------

_TIER_TTL_SECONDS: dict[str, int | None] = {
    "T1": None,
    "T2": 180 * 86400,
    "T3": 90 * 86400,
    "T4": 30 * 86400,
}


def tier_ttl(tier: str) -> int | None:
    """Return the TTL in integer seconds for a given tier, or ``None`` for T1.

    Parameters
    ----------
    tier:
        One of ``"T1"``, ``"T2"``, ``"T3"``, ``"T4"``.  Unknown tier values
        fall through to T4 (30 days).

    Returns
    -------
    int | None
        Seconds until expiry, or ``None`` (no expiry) for T1.
    """
    return _TIER_TTL_SECONDS.get(tier, _TIER_TTL_SECONDS["T4"])


# ---------------------------------------------------------------------------
# Activation floor constants  (C2)
# ---------------------------------------------------------------------------

_TIER_ACTIVATION_FLOOR: dict[str, float] = {
    "T1": 0.05,
    "T2": 0.05,
    "T3": 0.15,
    "T4": 0.15,
}


def tier_activation_floor(tier: str) -> float:
    """Return the minimum activation score below which a memory is prune-eligible.

    T1/T2 use a low floor (0.05) to protect high-value memories from premature
    pruning.  T3/T4 use a higher floor (0.15) to reclaim space aggressively.

    Parameters
    ----------
    tier:
        One of ``"T1"``, ``"T2"``, ``"T3"``, ``"T4"``.  Unknown tier values
        fall through to the T4 floor.

    Returns
    -------
    float
        Activation score floor for this tier.
    """
    return _TIER_ACTIVATION_FLOOR.get(tier, _TIER_ACTIVATION_FLOOR["T4"])


# ---------------------------------------------------------------------------
# Decay tau constants  (from compute_activation docstring in observability.py)
# ---------------------------------------------------------------------------

_TIER_DECAY_TAU_HOURS: dict[str, int] = {
    "T1": 720,
    "T2": 168,
    "T3": 48,
    "T4": 12,
}


def tier_decay_tau_hours(tier: str) -> int:
    """Return the exponential decay time-constant τ in hours for a given tier.

    At ``age_hours == tau``, the recency factor drops to ``1/e ≈ 0.368``.
    Values promoted from ``compute_activation`` docstring in
    ``core/observability.py``.

    Parameters
    ----------
    tier:
        One of ``"T1"``, ``"T2"``, ``"T3"``, ``"T4"``.  Unknown tier values
        fall through to T4 (12h).

    Returns
    -------
    int
        Tau in hours.
    """
    return _TIER_DECAY_TAU_HOURS.get(tier, _TIER_DECAY_TAU_HOURS["T4"])
