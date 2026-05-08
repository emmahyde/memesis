#!/usr/bin/env python3
"""
Dedup and quality cleanup for the memory store.

Finds and handles:
  1. Duplicate memories (same title, active) — archives the older/worse version
  2. Empty memories (no meaningful content) — archives them
  3. Frontmatter-polluted memories (content starts with '---') — a sign of the
     old ingest bug where full_content was stored; lower quality for FTS

Usage:
    python3 scripts/dedup_cleanup.py --base-dir ~/.claude/memory  # dry-run by default
    python3 scripts/dedup_cleanup.py --base-dir ~/.claude/memory --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.models import Memory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_frontmatter_polluted(content: str | None) -> bool:
    """Content starts with YAML frontmatter — old ingest bug."""
    return bool(content and content.strip().startswith("---"))


def _is_empty(memory: Memory) -> bool:
    body = (memory.content or "").strip()
    if not body:
        return True
    # Strip frontmatter and check if body is empty
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
    return not body or body == "---"


def _archive(memory: Memory, reason: str, apply: bool) -> None:
    if apply:
        Memory.update(archived_at=_now_iso()).where(Memory.id == memory.id).execute()
        print(f"  ✓ ARCHIVED: [{memory.id[:8]}] {(memory.title or '(untitled)')[:50]} — {reason}")
    else:
        print(f"  ~ WOULD ARCHIVE: [{memory.id[:8]}] {(memory.title or '(untitled)')[:50]} — {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--project-context", default=os.getcwd(), metavar="PATH")
    parser.add_argument("--apply", action="store_true", help="Actually archive (default: dry-run)")
    args = parser.parse_args()

    init_db(project_context=args.project_context, base_dir=args.base_dir)
    try:
        active = list(
            Memory.select()
            .where(Memory.archived_at.is_null(True))
            .order_by(Memory.created_at.asc())  # oldest first — we keep newest
        )

        print(f"\nMemory dedup + quality cleanup {'(DRY RUN — pass --apply to commit)' if not args.apply else '(APPLYING)'}")
        print(f"Active memories: {len(active)}\n")

        archived = 0

        # --- 1. Empty memories ---
        print("=== Empty memories ===")
        found_empty = False
        for m in active:
            if _is_empty(m):
                found_empty = True
                _archive(m, "empty content", args.apply)
                archived += 1
        if not found_empty:
            print("  None found.")

        # --- 2. Frontmatter-polluted memories ---
        print("\n=== Frontmatter-polluted memories (old ingest bug) ===")
        found_polluted = False
        for m in active:
            if _is_frontmatter_polluted(m.content):
                # Check if there's a non-polluted version with same title
                same_title = [x for x in active if x.title == m.title and x.id != m.id and not _is_frontmatter_polluted(x.content)]
                if same_title:
                    found_polluted = True
                    _archive(m, f"frontmatter-polluted, clean version exists ({same_title[0].id[:8]})", args.apply)
                    archived += 1
                else:
                    print(f"  ~ SKIP: [{m.id[:8]}] {(m.title or '(untitled)')[:50]} — polluted but no clean version to replace it")
        if not found_polluted:
            print("  None found (or no clean replacement available).")

        # --- 3. Duplicate titles (same title, different content) ---
        print("\n=== Duplicate titles ===")
        by_title: dict[str, list[Memory]] = defaultdict(list)
        for m in active:
            if m.title:
                by_title[m.title].append(m)
        found_dupes = False
        for title, group in by_title.items():
            if len(group) > 1:
                found_dupes = True
                # Sort: prefer non-polluted, then higher importance, then newest
                def _rank(m: Memory) -> tuple:
                    polluted = 1 if _is_frontmatter_polluted(m.content) else 0
                    return (polluted, -(m.importance or 0.0))
                group.sort(key=_rank)
                keeper = group[0]
                print(f"  Duplicates for '{title[:50]}':")
                print(f"    KEEP: [{keeper.id[:8]}] importance={keeper.importance:.2f} polluted={_is_frontmatter_polluted(keeper.content)}")
                for dupe in group[1:]:
                    _archive(dupe, f"duplicate of {keeper.id[:8]}", args.apply)
                    archived += 1
        if not found_dupes:
            print("  None found.")

        print(f"\n{'Applied' if args.apply else 'Would archive'}: {archived} memory/memories")
    finally:
        close_db()


if __name__ == "__main__":
    main()
