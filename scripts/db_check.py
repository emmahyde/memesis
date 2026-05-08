#!/usr/bin/env python3
"""
DB integrity check — validates Memory rows via proper apsw/Peewee interface.

Checks:
  1. content_hash consistency (stored hash matches md5(content))
  2. Empty/null content memories
  3. Stage validity (only valid stages present)
  4. Importance range (must be in [0.0, 1.0])
  5. FTS index coverage (all active memories indexed)

Usage:
    python3 scripts/db_check.py --base-dir ~/.claude/memory
    python3 scripts/db_check.py --base-dir ~/.claude/memory --fix
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db, db
from core.models import Memory

VALID_STAGES = {"ephemeral", "consolidated", "crystallized", "instinctive", "archived"}


def _check_content_hash(fix: bool) -> int:
    """Check that content_hash matches md5(content) for all memories."""
    issues = 0
    for m in Memory.select().where(Memory.archived_at.is_null(True)):
        content = m.content or ""
        if not content:
            continue
        expected = hashlib.md5(content.encode("utf-8")).hexdigest()
        stored = m.content_hash or ""
        if stored != expected:
            issues += 1
            print(f"  HASH MISMATCH: [{m.id[:8]}] {(m.title or '(untitled)')[:40]}")
            print(f"    stored:   {stored[:16]}...")
            print(f"    expected: {expected[:16]}...")
            if fix:
                Memory.update(content_hash=expected).where(Memory.id == m.id).execute()
                print(f"    FIXED")
    return issues


def _check_empty_content(fix: bool) -> int:
    """Find memories with no meaningful content."""
    issues = 0
    for m in Memory.select().where(Memory.archived_at.is_null(True)):
        content = (m.content or "").strip()
        if not content or content == "---":
            issues += 1
            print(f"  EMPTY: [{m.id[:8]}] {(m.title or '(untitled)')[:40]} [{m.stage}]")
            if fix:
                from datetime import datetime, timezone
                Memory.update(archived_at=datetime.now(timezone.utc).isoformat()).where(Memory.id == m.id).execute()
                print(f"    ARCHIVED")
    return issues


def _check_stage_validity() -> int:
    """Find memories with invalid stage values."""
    issues = 0
    for m in Memory.select():
        if m.stage not in VALID_STAGES:
            issues += 1
            print(f"  INVALID STAGE: [{m.id[:8]}] stage={m.stage!r}")
    return issues


def _check_importance_range(fix: bool) -> int:
    """Find memories with out-of-range importance."""
    issues = 0
    for m in Memory.select().where(Memory.archived_at.is_null(True)):
        imp = m.importance
        if imp is not None and not (0.0 <= imp <= 1.0):
            issues += 1
            print(f"  BAD IMPORTANCE: [{m.id[:8]}] importance={imp}")
            if fix:
                fixed = max(0.0, min(1.0, imp))
                Memory.update(importance=fixed).where(Memory.id == m.id).execute()
                print(f"    FIXED → {fixed}")
    return issues


def _check_fts_coverage() -> int:
    """Check FTS index covers all active non-archived memories."""
    active = Memory.select().where(Memory.archived_at.is_null(True)).count()
    fts_count = db.execute_sql("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    total = Memory.select().count()
    if fts_count < active:
        print(f"  FTS COVERAGE: {fts_count} indexed vs {active} active ({total} total incl. archived)")
        return 1
    return 0


def _check_orphaned_observations(fix: bool) -> int:
    """Find pending observations whose session already ran consolidation.

    These are stranded by the consolidator's _refs_for_observation lookup
    failing to pair the LLM-decision text with the captured row. They
    will never advance without intervention.
    """
    try:
        rows = db.execute_sql(
            """
            SELECT o.id, o.session_id
            FROM observations o
            WHERE o.status = 'pending'
              AND EXISTS (
                  SELECT 1 FROM consolidation_log cl WHERE cl.session_id = o.session_id
              )
            """
        ).fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    print(f"  ORPHANED: {len(rows)} pending observations whose session already consolidated")
    if fix:
        ids = [r[0] for r in rows]
        # Batch update by id list (small enough — bound by pending count)
        placeholders = ",".join("?" * len(ids))
        db.execute_sql(
            f"UPDATE observations SET status='orphaned' WHERE id IN ({placeholders})",
            ids,
        )
        print(f"    FIXED — marked 'orphaned'")
    return 1


def _check_aged_pending(fix: bool, max_age_days: int = 7) -> int:
    """Pending observations older than max_age_days are almost certainly stale.

    Either the cron crashed before cycle-12's failed-status fix landed, or
    the cron hasn't run in over a week (broken hook config). Either way,
    surface them so backlog math reflects truth.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    try:
        rows = db.execute_sql(
            "SELECT id FROM observations WHERE status='pending' AND created_at < ?",
            (cutoff,),
        ).fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    print(f"  AGED PENDING: {len(rows)} observation(s) older than {max_age_days}d still 'pending'")
    if fix:
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        db.execute_sql(
            f"UPDATE observations SET status='aged' WHERE id IN ({placeholders})",
            ids,
        )
        print(f"    FIXED — marked 'aged'")
    return 1


def _check_consolidation_errors(base_dir: str) -> int:
    """Surface recent consolidation cron failures from meta/consolidation-errors.jsonl.

    Cron writes structured error records here when process_buffer crashes —
    without this surface, errors vanish into stderr and stalled pipelines
    are invisible until backlog audits.
    """
    err_path = Path(base_dir) / "meta" / "consolidation-errors.jsonl"
    if not err_path.exists():
        return 0

    try:
        lines = err_path.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return 0

    if not lines:
        return 0

    import json as _json
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    recent = []
    for line in lines:
        try:
            rec = _json.loads(line)
            if rec.get("ts", "") >= cutoff:
                recent.append(rec)
        except Exception:
            continue

    if not recent:
        return 0

    print(f"  CRON FAILURES: {len(recent)} consolidation error(s) in the last 7 days")
    by_type: dict[str, int] = {}
    for rec in recent:
        et = rec.get("error_type", "Unknown")
        by_type[et] = by_type.get(et, 0) + 1
    for et, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {et}: {cnt}")
    if recent:
        last = recent[-1]
        print(f"    last: {last.get('ts', '')[:19]} — {last.get('error', '')[:100]}")
    return 1


def _check_observation_backlog() -> int:
    """Flag stalled consolidation: observations stuck in 'pending' status.

    A healthy pipeline keeps the pending count low — pre_compact processes
    observations into Memory rows. Sustained backlog means consolidation
    isn't firing (hook misconfigured, LLM call failing, or rate-limited).
    """
    try:
        pending = db.execute_sql(
            "SELECT COUNT(*) FROM observations WHERE status = 'pending'"
        ).fetchone()[0]
        total = db.execute_sql("SELECT COUNT(*) FROM observations").fetchone()[0]
    except Exception:
        return 0  # Schema may not have observations table on older DBs

    if total == 0:
        return 0

    last_consolidation = db.execute_sql(
        "SELECT MAX(timestamp) FROM consolidation_log"
    ).fetchone()[0]
    last_observation = db.execute_sql(
        "SELECT MAX(created_at) FROM observations"
    ).fetchone()[0]

    backlog_ratio = pending / total if total else 0
    issues = 0

    if backlog_ratio >= 0.5:
        issues += 1
        print(f"  STALLED PIPELINE: {pending}/{total} observations still 'pending' ({backlog_ratio:.0%})")
        print(f"    last consolidation: {last_consolidation}")
        print(f"    most recent observation: {last_observation}")
        print(f"    → check pre_compact hook, LLM transport, and consolidator logs")
    elif pending > 100:
        issues += 1
        print(f"  PENDING BACKLOG: {pending} observations awaiting consolidation")
        print(f"    last consolidation: {last_consolidation}")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--project-context", default=os.getcwd())
    parser.add_argument("--fix", action="store_true", help="Fix issues where possible")
    args = parser.parse_args()

    init_db(project_context=args.project_context, base_dir=args.base_dir)
    try:
        total_issues = 0

        print(f"\nDB integrity check {'(--fix mode)' if args.fix else '(read-only)'}")
        print(f"base-dir: {args.base_dir}\n")

        print("=== content_hash consistency ===")
        n = _check_content_hash(args.fix)
        if n == 0:
            print("  ✓ All hashes consistent")
        total_issues += n

        print("\n=== Empty content memories ===")
        n = _check_empty_content(args.fix)
        if n == 0:
            print("  ✓ None found")
        total_issues += n

        print("\n=== Stage validity ===")
        n = _check_stage_validity()
        if n == 0:
            print("  ✓ All stages valid")
        total_issues += n

        print("\n=== Importance range [0, 1] ===")
        n = _check_importance_range(args.fix)
        if n == 0:
            print("  ✓ All within range")
        total_issues += n

        print("\n=== FTS index coverage ===")
        n = _check_fts_coverage()
        if n == 0:
            print("  ✓ FTS fully covered")
        total_issues += n

        print("\n=== Orphaned observations (post-consolidation) ===")
        n = _check_orphaned_observations(args.fix)
        if n == 0:
            print("  ✓ No orphaned observations")
        total_issues += n

        print("\n=== Aged pending observations (>7d) ===")
        n = _check_aged_pending(args.fix)
        if n == 0:
            print("  ✓ No aged-pending observations")
        total_issues += n

        print("\n=== Observation backlog (consolidation health) ===")
        n = _check_observation_backlog()
        if n == 0:
            print("  ✓ No stalled-consolidation backlog")
        total_issues += n

        print("\n=== Recent consolidation cron errors ===")
        n = _check_consolidation_errors(args.base_dir)
        if n == 0:
            print("  ✓ No errors logged in last 7 days")
        total_issues += n

        print(f"\n{'─'*40}")
        if total_issues == 0:
            print("✓ CLEAN — no integrity issues found")
        else:
            action = "fixed" if args.fix else "found"
            print(f"⚠ {total_issues} issue(s) {action} — {'run with --fix to repair' if not args.fix else 'repaired'}")
    finally:
        close_db()


if __name__ == "__main__":
    main()
