#!/usr/bin/env python3
"""
Analyze Claude API costs from Claude Code session transcripts.

Reads JSONL session files from ~/.claude/projects/, extracts usage data,
and calculates costs using official Anthropic pricing.

Pricing source: https://platform.claude.com/docs/en/about-claude/pricing
Last verified: 2026-03-30

Usage:
    python3 scripts/cost.py                    # Last 24 hours
    python3 scripts/cost.py 7d                 # Last 7 days
    python3 scripts/cost.py 24h --by-session   # Breakdown per session
    python3 scripts/cost.py 24h --by-project   # Breakdown per project (default)
    python3 scripts/cost.py 24h --json         # Machine-readable output
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Pricing (USD per million tokens) — Anthropic API, standard tier
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# Last updated: 2026-03-30
#
# Cache pricing is expressed as multipliers of base input:
#   5-min cache write (first write): 1.25x base input
#   1-hour cache write (first write): 2.00x base input
#   Cache hit / refresh:              0.10x base input
#
# IMPORTANT: In Claude Code, cache_creation_input_tokens in the JSONL
# includes both TRUE first-writes and REFRESHES (extending TTL on already-
# cached content). Refreshes are billed at the hit rate (0.1x), not the
# write rate (1.25x). Since Claude Code re-sends the same system prompt,
# tools, and CLAUDE.md on every turn, most "creation" tokens are refreshes.
#
# Calibrated against claude_code.cost.usage Datadog metric (emitted by
# Claude Code with correct billing). Using refresh rate for cache_creation
# matches the dashboard within ~10%.
#
# Usage fields are DISJOINT:
#   total_input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
#   Each field is billed at its own rate. They do NOT overlap.
# ---------------------------------------------------------------------------

PRICING = {
    # model-family: (base_input, output, cache_write_5m, cache_write_1h, cache_hit_refresh)
    # cache_hit_refresh = 0.1x base input — used for BOTH reads and creation-refreshes
    "claude-opus-4-6":   (5.0,   25.0,  6.25,  10.0,  0.50),
    "claude-opus-4-5":   (5.0,   25.0,  6.25,  10.0,  0.50),
    "claude-opus-4-1":   (15.0,  75.0,  18.75, 30.0,  1.50),
    "claude-opus-4":     (15.0,  75.0,  18.75, 30.0,  1.50),
    "claude-sonnet-4-6": (3.0,   15.0,  3.75,  6.0,   0.30),
    "claude-sonnet-4-5": (3.0,   15.0,  3.75,  6.0,   0.30),
    "claude-sonnet-4":   (3.0,   15.0,  3.75,  6.0,   0.30),
    "claude-haiku-4-5":  (1.0,   5.0,   1.25,  2.0,   0.10),
    "claude-haiku-3-5":  (0.80,  4.0,   1.0,   1.6,   0.08),
    "claude-haiku-3":    (0.25,  1.25,  0.30,  0.50,  0.03),
}

# Mapping from full model IDs to pricing keys
def resolve_model(model_str: str) -> str | None:
    """Map a model string to its pricing key."""
    for key in PRICING:
        if key in model_str:
            return key
    # Bedrock format: us.anthropic.claude-opus-4-6-v1
    for key in PRICING:
        bedrock_name = key.replace("claude-", "")
        if bedrock_name in model_str:
            return key
    return None


def compute_cost(usage: dict, model_key: str) -> dict:
    """Compute cost from a usage dict. Returns cost breakdown.

    cache_creation_input_tokens are billed at the refresh/hit rate (0.1x base),
    NOT the first-write rate (1.25x base). In Claude Code, the system prompt,
    tools, and context are re-sent every turn — the API recognizes this as a
    cache refresh, not a new write. This is calibrated against Claude Code's
    own claude_code.cost.usage metric.
    """
    base_input, output, _cache_5m, _cache_1h, cache_hit = PRICING[model_key]

    input_tok = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    cache_create_tok = usage.get("cache_creation_input_tokens", 0)
    cache_read_tok = usage.get("cache_read_input_tokens", 0)

    input_cost = (input_tok / 1e6) * base_input
    output_cost = (output_tok / 1e6) * output
    # Both cache creation (refresh) and cache read billed at hit rate
    cache_write_cost = (cache_create_tok / 1e6) * cache_hit
    read_cost = (cache_read_tok / 1e6) * cache_hit

    return {
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "cache_write_tokens": cache_create_tok,
        "cache_read_tokens": cache_read_tok,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cache_write_cost": cache_write_cost,
        "cache_read_cost": read_cost,
        "total_cost": input_cost + output_cost + cache_write_cost + read_cost,
    }


def parse_duration(s: str) -> timedelta:
    import re
    match = re.match(r'^(\d+)([dhwm])$', s.strip().lower())
    if not match:
        raise ValueError(f"Invalid duration: '{s}'. Use 24h, 7d, 2w, 1m.")
    n, unit = int(match.group(1)), match.group(2)
    if unit == 'h': return timedelta(hours=n)
    if unit == 'd': return timedelta(days=n)
    if unit == 'w': return timedelta(weeks=n)
    if unit == 'm': return timedelta(days=n * 30)


def scan_sessions(cutoff: datetime) -> list[dict]:
    """Scan all JSONL files and extract usage records."""
    base = Path.home() / ".claude" / "projects"
    cutoff_ts = cutoff.timestamp()
    cutoff_iso = cutoff.isoformat()

    all_files = [f for f in base.rglob("*.jsonl") if f.stat().st_mtime > cutoff_ts]

    records = []
    for fpath in all_files:
        is_subagent = "subagent" in str(fpath)
        # Derive project name
        if is_subagent:
            project_dir = fpath.parent.parent.parent.name
        else:
            project_dir = fpath.parent.name

        session_id = fpath.stem[:12]

        with open(fpath) as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                ts = msg.get("timestamp", "")
                if ts and ts < cutoff_iso:
                    continue

                m = msg.get("message", {})
                usage = m.get("usage")
                model = m.get("model", "")

                if not usage or not model:
                    continue

                model_key = resolve_model(model)
                if not model_key:
                    continue

                records.append({
                    "model": model,
                    "model_key": model_key,
                    "usage": usage,
                    "is_subagent": is_subagent,
                    "project": project_dir,
                    "session_id": session_id,
                    "timestamp": ts,
                })

    return records


def aggregate(records: list[dict], group_by: str = "model_key") -> dict:
    """Aggregate records by a grouping key."""
    groups = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "cache_write_cost": 0.0,
        "cache_read_cost": 0.0,
        "total_cost": 0.0,
    })

    for rec in records:
        key = rec[group_by]
        cost = compute_cost(rec["usage"], rec["model_key"])
        g = groups[key]
        g["calls"] += 1
        for field in cost:
            g[field] += cost[field]

    return dict(groups)


def fmt_cost(v: float) -> str:
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.2f}"


def fmt_tok(v: int) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return str(v)


def print_model_breakdown(records: list[dict]):
    by_model = aggregate(records, "model_key")

    print(f"{'MODEL':<22} {'CALLS':>7} {'INPUT':>10} {'OUTPUT':>10} {'C.WRITE':>10} {'C.READ':>10} {'TOTAL':>10}")
    print("-" * 85)

    grand_total = 0
    for model in sorted(by_model, key=lambda m: by_model[m]["total_cost"], reverse=True):
        d = by_model[model]
        grand_total += d["total_cost"]
        print(
            f"{model:<22} {d['calls']:>7,} "
            f"{fmt_cost(d['input_cost']):>10} "
            f"{fmt_cost(d['output_cost']):>10} "
            f"{fmt_cost(d['cache_write_cost']):>10} "
            f"{fmt_cost(d['cache_read_cost']):>10} "
            f"{fmt_cost(d['total_cost']):>10}"
        )
        # Token counts on second line
        print(
            f"{'':>30} "
            f"{fmt_tok(d['input_tokens']):>10} "
            f"{fmt_tok(d['output_tokens']):>10} "
            f"{fmt_tok(d['cache_write_tokens']):>10} "
            f"{fmt_tok(d['cache_read_tokens']):>10}"
        )

    print("-" * 85)
    print(f"{'TOTAL':<22} {len(records):>7,} {'':>10} {'':>10} {'':>10} {'':>10} {fmt_cost(grand_total):>10}")


def print_group_breakdown(records: list[dict], group_by: str, label: str):
    groups = aggregate(records, group_by)

    print(f"\n{'=' * 70}")
    print(f"  BY {label.upper()}")
    print(f"{'=' * 70}")

    grand_total = 0
    for key in sorted(groups, key=lambda k: groups[k]["total_cost"], reverse=True):
        d = groups[key]
        grand_total += d["total_cost"]

        # Sub-breakdown by model within this group
        sub_records = [r for r in records if r[group_by] == key]
        by_model = aggregate(sub_records, "model_key")

        model_parts = []
        for m in sorted(by_model, key=lambda x: by_model[x]["total_cost"], reverse=True):
            md = by_model[m]
            short_name = m.replace("claude-", "")
            model_parts.append(f"{short_name}: {fmt_cost(md['total_cost'])} ({md['calls']} calls)")

        print(f"\n  {fmt_cost(d['total_cost']):>10}  {key}")
        print(f"  {'':>10}  {d['calls']:,} calls | {' | '.join(model_parts)}")


def main():
    duration_str = "24h"
    by_session = False
    by_project = True
    as_json = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--by-session":
            by_session = True; by_project = False; i += 1
        elif args[i] == "--by-project":
            by_project = True; i += 1
        elif args[i] == "--json":
            as_json = True; i += 1
        elif not args[i].startswith("--"):
            duration_str = args[i]; i += 1
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    since = parse_duration(duration_str)
    cutoff = datetime.now() - since

    print(f"Scanning sessions since {cutoff.strftime('%Y-%m-%d %H:%M')}...\n", file=sys.stderr)

    records = scan_sessions(cutoff)
    if not records:
        print("No usage data found.", file=sys.stderr)
        return

    if as_json:
        by_model = aggregate(records, "model_key")
        by_proj = aggregate(records, "project")
        total = sum(d["total_cost"] for d in by_model.values())
        print(json.dumps({
            "period": duration_str,
            "cutoff": cutoff.isoformat(),
            "total_cost": round(total, 2),
            "total_calls": len(records),
            "by_model": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in by_model.items()},
            "by_project": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in by_proj.items()},
        }, indent=2))
        return

    print(f"{'=' * 85}")
    print(f"  COST BREAKDOWN BY MODEL")
    print(f"{'=' * 85}")
    print_model_breakdown(records)

    # Main vs subagent split
    main_records = [r for r in records if not r["is_subagent"]]
    sub_records = [r for r in records if r["is_subagent"]]
    main_cost = sum(compute_cost(r["usage"], r["model_key"])["total_cost"] for r in main_records)
    sub_cost = sum(compute_cost(r["usage"], r["model_key"])["total_cost"] for r in sub_records)
    print(f"\n  Main sessions: {fmt_cost(main_cost)} ({len(main_records):,} calls)")
    print(f"  Subagents:     {fmt_cost(sub_cost)} ({len(sub_records):,} calls)")

    if by_project:
        print_group_breakdown(records, "project", "project")

    if by_session:
        print_group_breakdown(records, "session_id", "session")

    total = sum(compute_cost(r["usage"], r["model_key"])["total_cost"] for r in records)
    print(f"\n{'=' * 85}")
    print(f"  TOTAL: {fmt_cost(total)}")
    print(f"{'=' * 85}")


if __name__ == "__main__":
    main()
