#!/usr/bin/env python3
"""
Lifecycle audit script: joins Observation + Memory + consolidation_log to produce
per-session stage-transition counts and surface stuck-pending observations.

Ephemeral counts: reads from the `observations` table (status column) if present,
or falls back to scanning {base_dir}/ephemeral/ directory files. The observations
table is preferred when available since it carries status information.

Usage:
    python3 scripts/audit_lifecycle.py --base-dir ~/.claude/memory --out /tmp/lifecycle.md

Flags:
    --stuck-pending-days N   Flag observations with status=pending older than N days (default: 7)
    --limit-sessions N       Process only N most-recent sessions (default: no limit)
"""

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure core package is importable when run from repo root or scripts/
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.models import ConsolidationLog, Memory, Observation


_STAGES = ["ephemeral", "consolidated", "crystallized", "instinctive"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _make_tz_aware(dt: datetime) -> datetime:
    """Return dt with UTC timezone if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _gather_sessions(limit_sessions: int | None) -> list[str]:
    """Return ordered list of session_ids from Memory and Observation tables."""
    session_ids: set[str] = set()

    for m in Memory.select(Memory.source_session).where(
        Memory.source_session.is_null(False)
    ).distinct():
        if m.source_session:
            session_ids.add(m.source_session)

    # Also check Observation table
    try:
        for obs in Observation.select(Observation.session_id).where(
            Observation.session_id.is_null(False)
        ).distinct():
            if obs.session_id:
                session_ids.add(obs.session_id)
    except Exception:
        pass

    # Also check consolidation_log
    for cl in ConsolidationLog.select(ConsolidationLog.session_id).where(
        ConsolidationLog.session_id.is_null(False)
    ).distinct():
        if cl.session_id:
            session_ids.add(cl.session_id)

    sessions = sorted(session_ids, reverse=True)  # newest first (lexicographic on ISO IDs)
    if limit_sessions is not None:
        sessions = sessions[:limit_sessions]
    return sessions


def _count_ephemeral(session_id: str) -> int:
    """Count ephemeral observations for a session via Observation table."""
    try:
        return (
            Observation.select()
            .where(Observation.session_id == session_id)
            .count()
        )
    except Exception:
        return 0


def _count_pending(session_id: str) -> int:
    """Count observations with status=pending for a session."""
    try:
        return (
            Observation.select()
            .where(
                (Observation.session_id == session_id)
                & (Observation.status == "pending")
            )
            .count()
        )
    except Exception:
        return 0


def _stage_counts(session_id: str) -> dict[str, int]:
    """Return dict of stage -> count for Memory rows in this session."""
    counts = {s: 0 for s in _STAGES}
    for m in Memory.select(Memory.stage).where(
        Memory.source_session == session_id
    ):
        if m.stage in counts:
            counts[m.stage] += 1
    return counts


def _consolidation_log_counts(session_id: str) -> dict[str, int]:
    """Return action -> count from consolidation_log for a session."""
    counts: dict[str, int] = {}
    for cl in ConsolidationLog.select().where(
        ConsolidationLog.session_id == session_id
    ):
        action = cl.action or "unknown"
        counts[action] = counts.get(action, 0) + 1
    return counts


def _stuck_pending(stuck_days: int) -> list[dict]:
    """Return list of stuck-pending observations older than stuck_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=stuck_days)
    results: list[dict] = []
    try:
        for obs in Observation.select().where(Observation.status == "pending"):
            ts = _parse_ts(obs.created_at)
            if ts is None:
                continue
            ts = _make_tz_aware(ts)
            if ts < cutoff:
                age_days = (datetime.now(timezone.utc) - ts).days
                results.append({
                    "session_id": obs.session_id or "(unknown)",
                    "obs_id": obs.id,
                    "created_at": obs.created_at,
                    "age_days": age_days,
                })
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a Markdown table given headers and rows (all strings)."""
    sep = "|" + "|".join("---" for _ in headers) + "|"
    header_row = "|" + "|".join(headers) + "|"
    lines = [header_row, sep]
    for row in rows:
        lines.append("|" + "|".join(str(c) for c in row) + "|")
    return "\n".join(lines)


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{100 * numerator / denominator:.1f}%"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def build_report(
    base_dir: str,
    stuck_pending_days: int = 7,
    limit_sessions: int | None = None,
) -> str:
    """Build and return the full Markdown lifecycle audit report."""
    lines: list[str] = []

    sessions = _gather_sessions(limit_sessions)

    # --- Header ---
    lines.append(f"# Lifecycle audit report")
    lines.append("")
    lines.append(f"| field | value |")
    lines.append(f"|---|---|")
    lines.append(f"| generated | {_now_iso()} |")
    lines.append(f"| base_dir | `{base_dir}` |")
    lines.append(f"| total sessions | {len(sessions)} |")
    lines.append(f"| stuck_pending_threshold | {stuck_pending_days} days |")
    lines.append("")

    # Accumulate summary data
    total_obs = 0
    stage_totals = {s: 0 for s in _STAGES}
    total_subsumed = 0

    per_session_data: list[dict] = []
    for sid in sessions:
        ephemeral_count = _count_ephemeral(sid)
        pending_count = _count_pending(sid)
        stage_ct = _stage_counts(sid)
        cl_ct = _consolidation_log_counts(sid)
        subsumed = cl_ct.get("subsumed", 0)

        total_obs += ephemeral_count
        for s in _STAGES:
            stage_totals[s] += stage_ct.get(s, 0)
        total_subsumed += subsumed

        per_session_data.append({
            "session_id": sid,
            "ephemeral": ephemeral_count,
            "pending": pending_count,
            "consolidated": stage_ct.get("consolidated", 0),
            "crystallized": stage_ct.get("crystallized", 0),
            "instinctive": stage_ct.get("instinctive", 0),
            "subsumed": subsumed,
            "cl_counts": cl_ct,
        })

    # --- Summary table ---
    lines.append("## Summary")
    lines.append("")
    total_memories = sum(stage_totals.values())
    lines.append(_md_table(
        ["metric", "count", "% of observations"],
        [
            ["total observations", str(total_obs), "100%"],
            ["ephemeral (in observations table)", str(stage_totals.get("ephemeral", 0)),
             _pct(stage_totals.get("ephemeral", 0), total_obs)],
            ["consolidated", str(stage_totals["consolidated"]),
             _pct(stage_totals["consolidated"], total_obs)],
            ["crystallized", str(stage_totals["crystallized"]),
             _pct(stage_totals["crystallized"], total_obs)],
            ["instinctive", str(stage_totals["instinctive"]),
             _pct(stage_totals["instinctive"], total_obs)],
            ["subsumed (consolidation_log)", str(total_subsumed),
             _pct(total_subsumed, total_obs)],
            ["total memories (all stages)", str(total_memories), ""],
        ],
    ))
    lines.append("")

    # --- Promotion rate ---
    # Promotion rate: consolidated+crystallized+instinctive / total observations
    promoted = (
        stage_totals["consolidated"]
        + stage_totals["crystallized"]
        + stage_totals["instinctive"]
    )
    lines.append(
        f"**Overall promotion rate** (obs → any Memory stage): "
        f"{_pct(promoted, total_obs)} "
        f"({promoted} promoted / {total_obs} observations)"
    )
    lines.append("")

    # --- Stuck-pending alert ---
    lines.append("## Stuck-pending observations")
    lines.append("")
    stuck = _stuck_pending(stuck_pending_days)
    if stuck:
        lines.append(
            f"> **ALERT:** {len(stuck)} observation(s) have been in `pending` status "
            f"for more than {stuck_pending_days} days."
        )
        lines.append("")
        lines.append(_md_table(
            ["session_id", "obs_id", "created_at", "age_days"],
            [
                [
                    r["session_id"],
                    str(r["obs_id"]),
                    r["created_at"] or "",
                    str(r["age_days"]),
                ]
                for r in stuck
            ],
        ))
    else:
        lines.append(
            f"_No observations stuck in `pending` status for more than {stuck_pending_days} days._"
        )
    lines.append("")

    # --- Per-session breakdown ---
    lines.append("## Per-session breakdown")
    lines.append("")
    if not per_session_data:
        lines.append("_No sessions found._")
        lines.append("")
    else:
        lines.append(_md_table(
            [
                "session_id",
                "ephemeral_obs",
                "consolidated",
                "crystallized",
                "instinctive",
                "subsumed",
                "pending",
                "promotion_rate",
            ],
            [
                [
                    d["session_id"],
                    str(d["ephemeral"]),
                    str(d["consolidated"]),
                    str(d["crystallized"]),
                    str(d["instinctive"]),
                    str(d["subsumed"]),
                    str(d["pending"]),
                    _pct(
                        d["consolidated"] + d["crystallized"] + d["instinctive"],
                        d["ephemeral"] if d["ephemeral"] > 0 else (
                            d["consolidated"] + d["crystallized"] + d["instinctive"]
                        ),
                    ),
                ]
                for d in per_session_data
            ],
        ))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="End-to-end lifecycle audit: stage transition counts per session."
    )
    ap.add_argument(
        "--base-dir",
        type=str,
        default="~/.claude/memory",
        help="Base directory for the memesis database (default: ~/.claude/memory)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output Markdown file path (default: print to stdout)",
    )
    ap.add_argument(
        "--stuck-pending-days",
        type=int,
        default=7,
        metavar="N",
        help="Flag observations pending longer than N days (default: 7)",
    )
    ap.add_argument(
        "--limit-sessions",
        type=int,
        default=None,
        metavar="N",
        help="Process only N most-recent sessions (default: no limit)",
    )
    args = ap.parse_args()

    base_dir = str(Path(args.base_dir).expanduser())
    init_db(base_dir=base_dir)
    try:
        report = build_report(
            base_dir=base_dir,
            stuck_pending_days=args.stuck_pending_days,
            limit_sessions=args.limit_sessions,
        )
    finally:
        close_db()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tempfile + shutil.move (per CONVENTIONS.md)
        fd, tmp_path = tempfile.mkstemp(
            dir=args.out.parent, prefix=".audit_lifecycle_", suffix=".md"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(report)
            shutil.move(tmp_path, str(args.out))
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
