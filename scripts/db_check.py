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
