#!/usr/bin/env python3
"""
Retrieval hit-rate audit.

Reads retrieval_log (injected entries) to answer:
  - What fraction of injected memories were actually used?
  - Does hit rate vary by retrieval_type (instinctive/crystallized/FTS)?
  - Which memories are injected frequently but never used? (high injection, zero usage)
  - Trend: is the hit rate improving over time?

Usage:
    python3 scripts/audit_retrieval.py --base-dir ~/.claude/memory
    python3 scripts/audit_retrieval.py --base-dir ~/.claude/memory --sessions 20
    python3 scripts/audit_retrieval.py --base-dir ~/.claude/memory --out /tmp/retrieval.md

Flags:
    --sessions N    Limit to N most-recent sessions (default: all)
    --zombie-min N  Min injection count to flag as never-used zombie (default: 3)
    --out PATH      Write report to file instead of stdout
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
from core.models import Memory, RetrievalLog


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truncate(text: str | None, width: int = 80) -> str:
    if not text:
        return "(none)"
    return shorten(text.strip(), width=width, placeholder="…")


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{n / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _recent_sessions(limit: int | None) -> list[str]:
    seen: set[str] = set()
    for row in RetrievalLog.select(RetrievalLog.session_id).where(
        RetrievalLog.session_id.is_null(False),
        RetrievalLog.retrieval_type == "injected",
    ).distinct():
        if row.session_id:
            seen.add(row.session_id)
    sessions = sorted(seen, reverse=True)
    return sessions[:limit] if limit else sessions


def _injected_logs(session_ids: list[str]) -> list[RetrievalLog]:
    return list(
        RetrievalLog.select()
        .where(
            RetrievalLog.session_id.in_(session_ids),
            RetrievalLog.retrieval_type == "injected",
            RetrievalLog.memory_id.is_null(False),
        )
        .order_by(RetrievalLog.timestamp.desc())
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _hit_rate_by_type(logs: list[RetrievalLog]) -> dict[str, dict]:
    by_type: dict[str, dict] = defaultdict(lambda: {"total": 0, "used": 0})
    for log in logs:
        rtype = log.retrieval_type or "unknown"
        by_type[rtype]["total"] += 1
        if log.was_used:
            by_type[rtype]["used"] += 1
    return dict(by_type)


def _zombie_memories(logs: list[RetrievalLog], min_injections: int) -> list[dict]:
    """Memories injected ≥ min_injections times with was_used always 0."""
    counts: dict[str, dict] = defaultdict(lambda: {"total": 0, "used": 0})
    for log in logs:
        if log.memory_id:
            counts[log.memory_id]["total"] += 1
            if log.was_used:
                counts[log.memory_id]["used"] += 1

    zombies = []
    for memory_id, stats in counts.items():
        if stats["total"] >= min_injections and stats["used"] == 0:
            try:
                m = Memory.get_by_id(memory_id)
                zombies.append({
                    "memory_id": memory_id,
                    "injections": stats["total"],
                    "stage": m.stage,
                    "importance": m.importance,
                    "title": m.title,
                })
            except Memory.DoesNotExist:
                zombies.append({
                    "memory_id": memory_id,
                    "injections": stats["total"],
                    "stage": "DELETED",
                    "importance": None,
                    "title": "(memory deleted)",
                })

    return sorted(zombies, key=lambda z: -z["injections"])


def _session_trend(logs: list[RetrievalLog], n: int = 10) -> list[dict]:
    """Per-session hit rate for N most recent sessions."""
    by_session: dict[str, dict] = defaultdict(lambda: {"total": 0, "used": 0, "ts": ""})
    for log in logs:
        sid = log.session_id or "unknown"
        by_session[sid]["total"] += 1
        if log.was_used:
            by_session[sid]["used"] += 1
        if log.timestamp and log.timestamp > by_session[sid]["ts"]:
            by_session[sid]["ts"] = log.timestamp

    ordered = sorted(by_session.items(), key=lambda kv: kv[1]["ts"], reverse=True)[:n]
    return [
        {
            "session_id": sid,
            "total": s["total"],
            "used": s["used"],
            "ts": s["ts"],
        }
        for sid, s in ordered
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _render(
    sessions: list[str],
    logs: list[RetrievalLog],
    by_type: dict[str, dict],
    zombies: list[dict],
    trend: list[dict],
    zombie_min: int,
) -> str:
    total = len(logs)
    used = sum(1 for l in logs if l.was_used)
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"\n{'#' * level} {text}")

    def row(*cols: str) -> None:
        lines.append("| " + " | ".join(cols) + " |")

    h(1, f"Retrieval Hit-Rate Audit — {_now_iso()}")
    lines.append(f"\nSessions: **{len(sessions)}** · Total injections: **{total}** · Used: **{used}** ({_pct(used, total)})")

    # --- By retrieval type ---
    h(2, "Hit Rate by Retrieval Type")
    if not by_type:
        lines.append("\n_No injected entries found._")
    else:
        row("Type", "Injected", "Used", "Hit Rate")
        row("---", "---", "---", "---")
        for rtype, stats in sorted(by_type.items()):
            hr = _pct(stats["used"], stats["total"])
            flag = " ⚠" if stats["total"] > 0 and stats["used"] / stats["total"] < 0.2 else ""
            row(f"`{rtype}`{flag}", str(stats["total"]), str(stats["used"]), hr)

    # --- Overall signal flag ---
    h(2, "Signal Assessment")
    if total == 0:
        lines.append("\n⚠️ No injection data — run at least one session with memory injection enabled.")
    elif used == 0:
        lines.append("\n🔴 **Zero memories marked `was_used=1`.** Either the heuristic threshold is too high, or `track_usage()` is not firing (check `pre_compact.py` hook). The importance scoring feedback loop is broken.")
    elif used / total < 0.1:
        lines.append(f"\n🟡 **Hit rate {_pct(used, total)} is very low.** Most injected memories are not surfaced in LLM responses. Consider: raising importance threshold for injection, or reviewing `_compute_usage_score` threshold.")
    elif used / total < 0.3:
        lines.append(f"\n🟡 Hit rate {_pct(used, total)} is below 30%. Worth monitoring — may indicate topic drift or over-injection.")
    else:
        lines.append(f"\n✅ Hit rate {_pct(used, total)} — retrieval appears useful.")

    # --- Zombie memories ---
    h(2, f"Zombie Memories (≥{zombie_min} injections, 0 uses)")
    if not zombies:
        lines.append(f"\n✅ No zombies with ≥{zombie_min} injections and zero usage.")
    else:
        lines.append(f"\n{len(zombies)} zombie(s) detected. These are being repeatedly injected but never referenced:")
        row("memory_id", "injections", "stage", "importance", "title")
        row("---", "---", "---", "---", "---")
        for z in zombies[:15]:
            imp = f"{z['importance']:.2f}" if z["importance"] is not None else "?"
            row(
                f"`{z['memory_id'][:14]}…`",
                str(z["injections"]),
                z["stage"] or "?",
                imp,
                _truncate(z["title"], 50),
            )
        if len(zombies) > 15:
            lines.append(f"\n_… and {len(zombies) - 15} more. Run with `--zombie-min` to adjust threshold._")

    # --- Session trend ---
    h(2, "Recent Session Trend (last 10 sessions)")
    if not trend:
        lines.append("\n_No session data._")
    else:
        row("Session", "Injected", "Used", "Hit Rate", "Timestamp")
        row("---", "---", "---", "---", "---")
        for s in trend:
            row(
                f"`{s['session_id'][:12]}…`",
                str(s["total"]),
                str(s["used"]),
                _pct(s["used"], s["total"]),
                (s["ts"] or "")[:19],
            )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--sessions", type=int, default=None, metavar="N")
    parser.add_argument("--zombie-min", type=int, default=3, metavar="N")
    parser.add_argument("--out", type=str, default=None, metavar="PATH")
    args = parser.parse_args()

    init_db(args.base_dir)
    try:
        sessions = _recent_sessions(args.sessions)
        if not sessions:
            print("No injected retrieval_log entries found.", file=sys.stderr)
            sys.exit(0)

        logs = _injected_logs(sessions)
        by_type = _hit_rate_by_type(logs)
        zombies = _zombie_memories(logs, args.zombie_min)
        trend = _session_trend(logs)

        report = _render(
            sessions=sessions,
            logs=logs,
            by_type=by_type,
            zombies=zombies,
            trend=trend,
            zombie_min=args.zombie_min,
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
