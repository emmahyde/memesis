"""
registry_status.py — show the state of every self-reflection rule in the registry.

Usage:
    python3 scripts/registry_status.py [--root PATH]

Output columns:
    rule_id         canonical rule identifier from list_rules()
    fire_count      total fires recorded in audit log (0 if never fired)
    confirmed       Y if fire_count >= 3 (aggregate_audit "confirmed"), else N
    has_override    Y if rule_id in RULE_OVERRIDES, else N
    knobs           comma-separated list of parameter knobs from RULE_METADATA
    state           one of:
                      active             — confirmed + has override
                      dormant_confirmed  — confirmed but no override (ACTION NEEDED)
                      dormant_unconfirmed — has override but rule never confirmed
                      untracked          — no override and not confirmed

Rules are sorted by state priority: dormant_confirmed first (most actionable),
then active, then dormant_unconfirmed, then untracked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.rule_registry import RULE_METADATA, RULE_OVERRIDES
from core.self_reflection_extraction import aggregate_audit, list_rules


_STATE_SORT_ORDER = {
    "dormant_confirmed": 0,
    "active": 1,
    "dormant_unconfirmed": 2,
    "untracked": 3,
}


def _classify_state(confirmed: bool, has_override: bool) -> str:
    if confirmed and has_override:
        return "active"
    if confirmed and not has_override:
        return "dormant_confirmed"
    if not confirmed and has_override:
        return "dormant_unconfirmed"
    return "untracked"


def build_rows(root: Path | None = None) -> list[dict]:
    """Return list of row dicts for all known rules."""
    audit = aggregate_audit(root=root)
    known_rule_ids = list_rules()

    # Also include rules present in RULE_OVERRIDES but absent from list_rules()
    # (forward-wired overrides for rules not yet registered in self_reflection_extraction)
    all_rule_ids = list(dict.fromkeys(known_rule_ids + list(RULE_OVERRIDES.keys())))

    rows: list[dict] = []
    for rule_id in all_rule_ids:
        slot = audit.get(rule_id, {})
        fire_count = slot.get("fire_count", 0)
        confirmed = slot.get("confidence") == "confirmed"
        has_override = rule_id in RULE_OVERRIDES
        meta = RULE_METADATA.get(rule_id, {})
        knobs = meta.get("knobs", [])
        state = _classify_state(confirmed, has_override)
        rows.append(
            {
                "rule_id": rule_id,
                "fire_count": fire_count,
                "confirmed": confirmed,
                "has_override": has_override,
                "knobs": knobs,
                "state": state,
            }
        )

    rows.sort(key=lambda r: (_STATE_SORT_ORDER.get(r["state"], 99), r["rule_id"]))
    return rows


def render_table(rows: list[dict]) -> str:
    """Render rows as a plain-text table."""
    headers = ["rule_id", "fires", "confirmed", "override", "knobs", "state"]
    col_widths = [len(h) for h in headers]

    formatted: list[list[str]] = []
    for r in rows:
        cells = [
            r["rule_id"],
            str(r["fire_count"]),
            "Y" if r["confirmed"] else "N",
            "Y" if r["has_override"] else "N",
            ", ".join(r["knobs"]) if r["knobs"] else "—",
            r["state"],
        ]
        formatted.append(cells)
        for i, c in enumerate(cells):
            col_widths[i] = max(col_widths[i], len(c))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_row = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    lines: list[str] = [sep, header_row, sep]
    for cells in formatted:
        line = "| " + " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(cells)) + " |"
        lines.append(line)
    lines.append(sep)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show active/dormant/unmapped self-reflection rules with fire counts."
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=None,
        help="Path to memesis root dir (defaults to ~/.claude/memesis).",
    )
    args = parser.parse_args()

    root = Path(args.root) if args.root else None
    rows = build_rows(root=root)
    print(render_table(rows))

    # Summary line
    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    action_needed = by_state.get("dormant_confirmed", 0)
    if action_needed:
        print(
            f"\n  ACTION NEEDED: {action_needed} confirmed rule(s) have no override entry."
        )
    else:
        print("\n  All confirmed rules have override entries.")

    total = len(rows)
    print(
        f"  Total rules: {total} "
        f"(active={by_state.get('active', 0)}, "
        f"dormant_confirmed={by_state.get('dormant_confirmed', 0)}, "
        f"dormant_unconfirmed={by_state.get('dormant_unconfirmed', 0)}, "
        f"untracked={by_state.get('untracked', 0)})"
    )


if __name__ == "__main__":
    main()
