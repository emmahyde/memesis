#!/usr/bin/env python3
"""
Append an observation to the ephemeral session buffer with file locking.

Usage:
    python3 append_observation.py <buffer_path> <observation_text> [--type TYPE]

Supported types: correction, preference_signal, shared_insight, domain_knowledge,
                 workflow_pattern, self_observation, decision_context

When --type is provided, the observation is formatted with a timestamp header
and type tag. Without --type, raw text is appended (backward compatible).

Uses fcntl.flock to coordinate with consolidate_cron.py — ensures no
observation is lost if a cron consolidation runs mid-append.
"""
import argparse
import fcntl
import sys
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.prompts import OBSERVATION_TYPES, format_observation


def append(buffer_path: str, observation: str, obs_type: str = None) -> None:
    """
    Append an observation to the ephemeral buffer with file locking.

    Args:
        buffer_path: Path to the ephemeral session markdown file.
        observation: Observation text.
        obs_type: Optional observation type for structured formatting.
    """
    path = Path(buffer_path)
    lock_path = path.parent / ".lock"

    path.parent.mkdir(parents=True, exist_ok=True)

    if obs_type and obs_type in OBSERVATION_TYPES:
        text = format_observation(observation, obs_type=obs_type)
    else:
        text = observation.rstrip("\n") + "\n\n"

    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Append observation to ephemeral buffer"
    )
    parser.add_argument("buffer_path", help="Path to the ephemeral session buffer")
    parser.add_argument("observation", help="Observation text")
    parser.add_argument(
        "--type",
        dest="obs_type",
        choices=list(OBSERVATION_TYPES.keys()),
        help="Observation type for structured metadata",
    )

    args = parser.parse_args()
    append(args.buffer_path, args.observation, obs_type=args.obs_type)
