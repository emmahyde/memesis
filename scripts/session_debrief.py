#!/usr/bin/env python3
"""
Session debrief — what did the memory system learn in the most recent session?

Shows: injected memories, consolidation decisions, hypothesis activity,
and any memories created or promoted.

Usage:
    python3 scripts/session_debrief.py --base-dir ~/.claude/memory
    python3 scripts/session_debrief.py --base-dir ~/.claude/memory --session <session_id>
    python3 scripts/session_debrief.py --base-dir ~/.claude/memory --last N  # N-th most recent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.models import ConsolidationLog, Memory, RetrievalLog


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truncate(text: str | None, width: int = 80) -> str:
    if not text:
        return "(none)"
    return shorten(text.strip(), width=width, placeholder="…")


def _latest_sessions(n: int = 10) -> list[str]:
    seen: set[str] = set()
    # Check both retrieval and consolidation logs for sessions
    for row in RetrievalLog.select(RetrievalLog.session_id).where(
        RetrievalLog.session_id.is_null(False)
    ).distinct():
        if row.session_id:
            seen.add(row.session_id)
    for row in ConsolidationLog.select(ConsolidationLog.session_id).where(
        ConsolidationLog.session_id.is_null(False)
    ).distinct():
        if row.session_id:
            seen.add(row.session_id)
    # Sort by most recent timestamp
    sessions_with_ts = {}
    for sid in seen:
        latest_rl = (
            RetrievalLog.select(RetrievalLog.timestamp)
            .where(RetrievalLog.session_id == sid)
            .order_by(RetrievalLog.timestamp.desc())
            .first()
        )
        latest_cl = (
            ConsolidationLog.select(ConsolidationLog.timestamp)
            .where(ConsolidationLog.session_id == sid)
            .order_by(ConsolidationLog.timestamp.desc())
            .first()
        )
        ts = max(
            latest_rl.timestamp if latest_rl else "",
            latest_cl.timestamp if latest_cl else "",
        )
        sessions_with_ts[sid] = ts
    return sorted(seen, key=lambda s: sessions_with_ts.get(s, ""), reverse=True)[:n]


def _injection_summary(session_id: str) -> dict:
    logs = list(
        RetrievalLog.select()
        .where(
            RetrievalLog.session_id == session_id,
            RetrievalLog.retrieval_type == "injected",
            RetrievalLog.memory_id.is_null(False),
        )
    )
    injected = []
    for log in logs:
        try:
            m = Memory.get_by_id(log.memory_id)
            injected.append({
                "title": m.title or "(untitled)",
                "stage": m.stage,
                "was_used": bool(log.was_used),
            })
        except Memory.DoesNotExist:
            injected.append({
                "title": f"(deleted: {log.memory_id[:8]}…)",
                "stage": "?",
                "was_used": bool(log.was_used),
            })
    used_count = sum(1 for i in injected if i["was_used"])
    return {"memories": injected, "total": len(injected), "used": used_count}


def _consolidation_summary(session_id: str) -> dict:
    logs = list(
        ConsolidationLog.select()
        .where(ConsolidationLog.session_id == session_id)
        .order_by(ConsolidationLog.timestamp.asc())
    )
    by_action: dict[str, list] = {}
    for log in logs:
        action = log.action or "unknown"
        by_action.setdefault(action, []).append(log)
    return {"by_action": by_action, "total": len(logs)}


def _hypothesis_summary(session_id: str) -> dict:
    """Find hypothesis activities in this session."""
    hypotheses = list(
        Memory.select().where(
            Memory.kind == "hypothesis",
            Memory.archived_at.is_null(True),
        )
    )
    # Find hypotheses that gained evidence in this session
    with_session_evidence = []
    for h in hypotheses:
        try:
            sessions = json.loads(h.evidence_session_ids or "[]")
        except Exception:
            sessions = []
        if session_id in sessions:
            with_session_evidence.append(h)

    return {
        "total_pending": len(hypotheses),
        "confirmed_this_session": with_session_evidence,
    }


def _new_memories(session_id: str) -> list[dict]:
    mems = list(
        Memory.select().where(
            Memory.source_session == session_id,
            Memory.archived_at.is_null(True),
        )
    )
    return [
        {
            "title": m.title or "(untitled)",
            "stage": m.stage,
            "importance": m.importance,
        }
        for m in mems
    ]


def _render(session_id: str) -> str:
    lines = []

    def h(level: int, text: str) -> None:
        lines.append(f"\n{'#' * level} {text}")

    h(1, f"Session Debrief — {_now_iso()}")
    lines.append(f"\nSession: `{session_id[:20]}…`")

    # --- Injection ---
    inj = _injection_summary(session_id)
    h(2, f"Memory Injection ({inj['total']} injected, {inj['used']} used)")
    if not inj["memories"]:
        lines.append("\n_No injection events logged for this session._")
    else:
        for m in inj["memories"]:
            used_marker = "✓" if m["was_used"] else "·"
            lines.append(f"  {used_marker} [{m['stage'][:5]}] {_truncate(m['title'], 60)}")
        if inj["total"] > 0:
            hit_rate = f"{inj['used'] / inj['total'] * 100:.0f}%"
            lines.append(f"\nHit rate: **{hit_rate}**")

    # --- Consolidation ---
    cons = _consolidation_summary(session_id)
    h(2, f"Consolidation Decisions ({cons['total']} total)")
    if cons["total"] == 0:
        lines.append("\n_No consolidation decisions for this session._")
    else:
        for action, entries in sorted(cons["by_action"].items()):
            lines.append(f"\n**{action}** ({len(entries)}):")
            for e in entries[:3]:
                mem_title = "(unknown)"
                if e.memory_id:
                    try:
                        m = Memory.get_by_id(e.memory_id)
                        mem_title = m.title or "(untitled)"
                    except Memory.DoesNotExist:
                        mem_title = f"(deleted: {e.memory_id[:8]}…)"
                lines.append(f"  - {_truncate(mem_title, 50)}")
                if e.rationale:
                    lines.append(f"    _{_truncate(e.rationale, 80)}_")
            if len(entries) > 3:
                lines.append(f"  _…and {len(entries) - 3} more_")

    # --- New memories created ---
    new_mems = _new_memories(session_id)
    h(2, f"New Memories Created ({len(new_mems)})")
    if not new_mems:
        lines.append("\n_No memories created in this session._")
    else:
        for m in new_mems:
            lines.append(
                f"  - [{m['stage'][:5]}] {_truncate(m['title'], 55)} "
                f"(importance: {m['importance']:.2f})"
            )

    # --- Hypothesis activity ---
    hyp = _hypothesis_summary(session_id)
    h(2, f"Hypothesis Activity")
    lines.append(f"\nTotal pending hypotheses: **{hyp['total_pending']}**")
    if hyp["confirmed_this_session"]:
        lines.append(f"\nConfirmed by this session ({len(hyp['confirmed_this_session'])}):")
        for h_mem in hyp["confirmed_this_session"]:
            lines.append(
                f"  - {_truncate(h_mem.title, 55)} "
                f"(evidence: {h_mem.evidence_count or 0}, "
                f"sessions: {len(json.loads(h_mem.evidence_session_ids or '[]'))})"
            )
    else:
        lines.append("\n_No hypotheses confirmed by this session._")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--project-context", default=os.getcwd(), metavar="PATH")
    parser.add_argument("--session", type=str, default=None, help="Specific session ID")
    parser.add_argument("--last", type=int, default=1, metavar="N",
                        help="Nth most recent session (default: 1 = latest)")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List recent session IDs and exit")
    args = parser.parse_args()

    init_db(project_context=args.project_context, base_dir=args.base_dir)
    try:
        sessions = _latest_sessions(n=20)

        if args.list_sessions:
            for i, sid in enumerate(sessions, 1):
                print(f"{i:3}. {sid}")
            return

        if not sessions:
            print("No sessions found in retrieval or consolidation logs.", file=sys.stderr)
            return

        if args.session:
            session_id = args.session
        else:
            idx = max(0, min(args.last - 1, len(sessions) - 1))
            session_id = sessions[idx]

        print(_render(session_id))
    finally:
        close_db()


if __name__ == "__main__":
    main()
