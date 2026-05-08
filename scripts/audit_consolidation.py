#!/usr/bin/env python3
"""
Consolidation decision quality audit.

Reads consolidation_log and the memories table to answer:
  - Are the LLM decisions reasonable? (distribution, prune rate)
  - Do promoted memories survive? (survivorship check)
  - Are there anomalies worth investigating? (flags)
  - What did the LLM spend on this? (token cost summary)

Usage:
    python3 scripts/audit_consolidation.py --base-dir ~/.claude/memory
    python3 scripts/audit_consolidation.py --base-dir ~/.claude/memory --sessions 10
    python3 scripts/audit_consolidation.py --base-dir ~/.claude/memory --prune-samples 20 --out /tmp/audit.md

Flags:
    --sessions N       Limit to N most-recent sessions (default: all)
    --prune-samples N  Number of recent prune decisions to spot-check (default: 10)
    --out PATH         Write report to file instead of stdout
    --no-color         Disable ANSI color (implied when writing to file)
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.models import ConsolidationLog, Memory

# ---------------------------------------------------------------------------
# Thresholds / constants
# ---------------------------------------------------------------------------

PRUNE_RATE_WARN = 0.60      # warn if >60% of decisions are prune
PRUNE_RATE_CRITICAL = 0.80  # critical if >80%
PROMOTE_GHOST_WARN = 0.25   # warn if >25% of promoted memories are missing from DB

_DESTRUCTIVE = {"pruned", "deprecated", "subsumed", "archived", "pending_delete"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truncate(text: str | None, width: int = 120) -> str:
    if not text:
        return "(none)"
    return shorten(text.strip(), width=width, placeholder="…")


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{n / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _recent_sessions(limit: int | None) -> list[str]:
    seen: set[str] = set()
    for row in ConsolidationLog.select(ConsolidationLog.session_id).where(
        ConsolidationLog.session_id.is_null(False)
    ).distinct():
        if row.session_id:
            seen.add(row.session_id)
    sessions = sorted(seen, reverse=True)
    return sessions[:limit] if limit else sessions


def _logs_for_sessions(session_ids: list[str]) -> list[ConsolidationLog]:
    return list(
        ConsolidationLog.select()
        .where(ConsolidationLog.session_id.in_(session_ids))
        .order_by(ConsolidationLog.timestamp.desc())
    )


def _memory_exists(memory_id: str) -> tuple[bool, str | None]:
    """Return (exists, stage)."""
    try:
        m = Memory.get_by_id(memory_id)
        return True, m.stage
    except Memory.DoesNotExist:
        return False, None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _decision_distribution(logs: list[ConsolidationLog]) -> dict[str, int]:
    dist: dict[str, int] = defaultdict(int)
    for log in logs:
        dist[log.action or "unknown"] += 1
    return dict(sorted(dist.items(), key=lambda kv: -kv[1]))


def _prune_samples(logs: list[ConsolidationLog], n: int) -> list[ConsolidationLog]:
    pruned = [l for l in logs if l.action in _DESTRUCTIVE]
    return pruned[:n]


def _promote_survivorship(logs: list[ConsolidationLog]) -> list[dict]:
    promoted = [l for l in logs if l.action == "promoted"]
    results = []
    for log in promoted:
        if not log.memory_id:
            continue
        exists, stage = _memory_exists(log.memory_id)
        results.append({
            "memory_id": log.memory_id,
            "session_id": log.session_id,
            "timestamp": log.timestamp,
            "to_stage": log.to_stage,
            "exists": exists,
            "current_stage": stage,
            "rationale": log.rationale,
        })
    return results


def _token_summary(logs: list[ConsolidationLog]) -> dict:
    total_in = total_out = total_latency = n_with_tokens = n_with_latency = 0
    for log in logs:
        if log.input_tokens is not None:
            total_in += log.input_tokens
            total_out += (log.output_tokens or 0)
            n_with_tokens += 1
        if log.latency_ms is not None:
            total_latency += log.latency_ms
            n_with_latency += 1
    return {
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "n_with_tokens": n_with_tokens,
        "avg_latency_ms": (total_latency // n_with_latency) if n_with_latency else None,
        "n_with_latency": n_with_latency,
    }


def _anomalies(
    dist: dict[str, int],
    promote_results: list[dict],
    logs: list[ConsolidationLog],
) -> list[str]:
    flags = []
    total = sum(dist.values())
    prune_count = sum(dist.get(a, 0) for a in _DESTRUCTIVE)

    if total and prune_count / total >= PRUNE_RATE_CRITICAL:
        flags.append(
            f"CRITICAL: prune rate {_pct(prune_count, total)} exceeds {int(PRUNE_RATE_CRITICAL*100)}% threshold — LLM may be over-discarding"
        )
    elif total and prune_count / total >= PRUNE_RATE_WARN:
        flags.append(
            f"WARN: prune rate {_pct(prune_count, total)} exceeds {int(PRUNE_RATE_WARN*100)}% — review prune rationales below"
        )

    # Promotes with empty rationale
    bad_promotes = [
        r for r in promote_results if not (r.get("rationale") or "").strip()
    ]
    if bad_promotes:
        flags.append(
            f"WARN: {len(bad_promotes)} promote decision(s) have no rationale — Pydantic gate may not be enforcing it"
        )

    # Ghost promotes (promoted but not in DB)
    ghosts = [r for r in promote_results if not r["exists"]]
    if promote_results and len(ghosts) / len(promote_results) >= PROMOTE_GHOST_WARN:
        flags.append(
            f"WARN: {len(ghosts)}/{len(promote_results)} promoted memories missing from DB — check two-phase delete or manual prune"
        )
    elif ghosts:
        flags.append(
            f"INFO: {len(ghosts)} promoted memory/memories no longer in DB (may be intentional)"
        )

    # Decisions with no memory_id
    no_id = [l for l in logs if not l.memory_id and l.action not in ("unknown", None)]
    if no_id:
        flags.append(f"INFO: {len(no_id)} log entries have no memory_id (may be session-level actions)")

    return flags


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _render(
    sessions: list[str],
    dist: dict[str, int],
    prune_sample: list[ConsolidationLog],
    promote_results: list[dict],
    token_summary: dict,
    flags: list[str],
) -> str:
    total = sum(dist.values())
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"\n{'#' * level} {text}")

    def row(*cols: str) -> None:
        lines.append("| " + " | ".join(cols) + " |")

    h(1, f"Consolidation Decision Audit — {_now_iso()}")
    lines.append(f"\nSessions analyzed: **{len(sessions)}** · Total decisions: **{total}**")

    # --- Decision distribution ---
    h(2, "Decision Distribution")
    row("Action", "Count", "Rate")
    row("---", "---", "---")
    for action, count in dist.items():
        flag = " ⚠" if action in _DESTRUCTIVE and count / max(total, 1) >= PRUNE_RATE_WARN else ""
        row(f"`{action}`{flag}", str(count), _pct(count, total))

    # --- Anomaly flags ---
    if flags:
        h(2, "Flags")
        for f in flags:
            severity = "🔴" if f.startswith("CRITICAL") else "🟡" if f.startswith("WARN") else "ℹ️"
            lines.append(f"\n{severity} {f}")
    else:
        h(2, "Flags")
        lines.append("\n✅ No anomalies detected.")

    # --- Prune spot-check ---
    h(2, f"Prune Spot-Check (last {len(prune_sample)} destructive decisions)")
    if not prune_sample:
        lines.append("\n_No prune decisions recorded._")
    else:
        for i, log in enumerate(prune_sample, 1):
            lines.append(f"\n**{i}.** `{log.action}` · session `{(log.session_id or '')[:12]}…` · {log.timestamp or '?'}")
            lines.append(f"   - memory_id: `{log.memory_id or '(none)'}`")
            lines.append(f"   - rationale: _{_truncate(log.rationale, 200)}_")
            if log.from_stage:
                lines.append(f"   - from stage: `{log.from_stage}`")

    # --- Promote survivorship ---
    h(2, f"Promote Survivorship ({len(promote_results)} promotes)")
    if not promote_results:
        lines.append("\n_No promote decisions recorded._")
    else:
        alive = sum(1 for r in promote_results if r["exists"])
        ghosts = len(promote_results) - alive
        lines.append(f"\n**Alive:** {alive} · **Missing:** {ghosts}")
        if ghosts:
            lines.append("\n**Missing promoted memories:**")
            row("memory_id", "session", "to_stage", "rationale")
            row("---", "---", "---", "---")
            for r in promote_results:
                if not r["exists"]:
                    row(
                        f"`{r['memory_id'][:16]}…`",
                        f"`{(r['session_id'] or '')[:12]}…`",
                        r["to_stage"] or "?",
                        _truncate(r["rationale"], 60),
                    )

    # --- Token / cost summary ---
    h(2, "LLM Token Summary")
    ts = token_summary
    if ts["n_with_tokens"] == 0:
        lines.append("\n_No token data in log (consolidation_log.input_tokens all NULL — instrumentation not yet firing)._")
    else:
        lines.append(f"\n- Decisions with token data: **{ts['n_with_tokens']}** / {total}")
        lines.append(f"- Total input tokens: **{ts['total_input_tokens']:,}**")
        lines.append(f"- Total output tokens: **{ts['total_output_tokens']:,}**")
        # Rough cost at claude-haiku-4 pricing ($0.25/Mtok in, $1.25/Mtok out)
        cost_est = (ts["total_input_tokens"] * 0.25 + ts["total_output_tokens"] * 1.25) / 1_000_000
        lines.append(f"- Estimated cost (Haiku-4 rates): **${cost_est:.4f}**")
        if ts["avg_latency_ms"] is not None:
            lines.append(f"- Avg latency: **{ts['avg_latency_ms']} ms**")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"), help="Memory base directory")
    parser.add_argument("--sessions", type=int, default=None, metavar="N", help="Limit to N most-recent sessions")
    parser.add_argument("--prune-samples", type=int, default=10, metavar="N", help="Prune decisions to spot-check")
    parser.add_argument("--out", type=str, default=None, metavar="PATH", help="Write report to file")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    init_db(args.base_dir)
    try:
        sessions = _recent_sessions(args.sessions)
        if not sessions:
            print("No consolidation_log entries found. Run a consolidation pass first.", file=sys.stderr)
            sys.exit(0)

        logs = _logs_for_sessions(sessions)
        dist = _decision_distribution(logs)
        prune_sample = _prune_samples(logs, args.prune_samples)
        promote_results = _promote_survivorship(logs)
        token_summary = _token_summary(logs)
        flags = _anomalies(dist, promote_results, logs)

        report = _render(
            sessions=sessions,
            dist=dist,
            prune_sample=prune_sample,
            promote_results=promote_results,
            token_summary=token_summary,
            flags=flags,
        )

        if args.out:
            Path(args.out).write_text(report)
            print(f"Report written to {args.out}")
        else:
            print(report)
    finally:
        close_db()


if __name__ == "__main__":
    main()
