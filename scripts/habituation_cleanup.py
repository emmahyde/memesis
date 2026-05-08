#!/usr/bin/env python3
"""
Habituation model cleanup — remove invalid event types from habituation.json.

The habituation model accumulated counts for '-' (302+) and other non-word
characters when the _OBS_HEADER_RE regex bug extracted markdown bullets as
event types. These counts will never decrease and artificially inflate factors
for anything that looks like those strings. This script removes them.

Usage:
    python3 scripts/habituation_cleanup.py --base-dir ~/.claude/projects/PROJ/memory
    python3 scripts/habituation_cleanup.py --base-dir ~/.claude/projects/PROJ/memory --dry-run
    python3 scripts/habituation_cleanup.py --base-dir ~/.claude/projects/PROJ/memory --all-projects
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.habituation import HabituationModel

# Only keep event types that are valid observation type names (word characters only).
# The bug extracted punctuation like '-', '*', etc. as event types.
_VALID_EVENT_PATTERN = re.compile(r"^\w+$")

# Known observation types that should be kept regardless
KNOWN_OBSERVATION_TYPES = frozenset({
    "correction",
    "preference_signal",
    "shared_insight",
    "domain_knowledge",
    "workflow_pattern",
    "self_observation",
    "decision_context",
    "personality",
    "aesthetic",
    "collaboration_dynamic",
    "system_change",
    "untyped",
})


def cleanup_one(base_dir: Path, dry_run: bool) -> dict:
    """Clean up habituation.json in a single memory directory.

    Returns {removed: {event_type: count}, kept: {event_type: count}}.
    """
    hab_path = base_dir / "habituation.json"
    if not hab_path.exists():
        return {"removed": {}, "kept": {}}

    with open(hab_path) as f:
        counts: dict[str, int] = json.load(f)

    removed = {}
    kept = {}
    for event_type, count in counts.items():
        # Keep if it's a valid word-character event type
        if _VALID_EVENT_PATTERN.match(event_type):
            kept[event_type] = count
        else:
            removed[event_type] = count

    if removed and not dry_run:
        with open(hab_path, "w") as f:
            json.dump(kept, f, indent=2)

    return {"removed": removed, "kept": kept}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-dir", help="Memory base directory (e.g. ~/.claude/projects/PROJ/memory)")
    parser.add_argument("--all-projects", action="store_true", help="Clean all projects under ~/.claude/projects/")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without changing files")
    args = parser.parse_args()

    targets: list[Path] = []
    if args.all_projects:
        projects_base = Path.home() / ".claude" / "projects"
        if projects_base.exists():
            for proj_dir in projects_base.iterdir():
                mem_dir = proj_dir / "memory"
                if (mem_dir / "habituation.json").exists():
                    targets.append(mem_dir)
    elif args.base_dir:
        targets.append(Path(os.path.expanduser(args.base_dir)))
    else:
        parser.error("Provide --base-dir or --all-projects")

    if not targets:
        print("No habituation.json files found.")
        return

    total_removed = 0
    for mem_dir in targets:
        result = cleanup_one(mem_dir, dry_run=args.dry_run)
        removed = result["removed"]
        kept = result["kept"]

        if removed:
            action = "[dry-run] would remove" if args.dry_run else "Removed"
            print(f"\n{mem_dir}:")
            print(f"  {action}:")
            for evt, cnt in sorted(removed.items(), key=lambda x: -x[1]):
                print(f"    {evt!r}: count={cnt}")
            print(f"  Kept: {len(kept)} valid event types")
            total_removed += len(removed)
        else:
            print(f"\n{mem_dir}: clean (no invalid event types)")

    print(f"\n{'─'*40}")
    if total_removed == 0:
        print("✓ All habituation.json files clean")
    else:
        action = "Would remove" if args.dry_run else "Removed"
        print(f"{'~' if args.dry_run else '✓'} {action} {total_removed} invalid event type(s)")


if __name__ == "__main__":
    main()
