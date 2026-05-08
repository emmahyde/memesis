#!/usr/bin/env python3
"""
Quick smoke test of the full memory pipeline.

Checks:
  - DB initializes correctly (migrations run)
  - Native memory ingestor runs without errors
  - FTS search finds seeded memories
  - Relevance engine scores without crashing
  - Instinctive memories are present
  - Transcript cron tick can discover transcripts

Usage:
    python3 scripts/smoke_test.py --base-dir ~/.claude/memory
    python3 scripts/smoke_test.py  # uses default memory path
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PASS = "✓"
FAIL = "✗"
WARN = "⚠"


def check(label: str, fn) -> bool:
    try:
        result = fn()
        msg = f"  {result}" if isinstance(result, str) else ""
        print(f"  {PASS}  {label}{msg}")
        return True
    except Exception as e:
        print(f"  {FAIL}  {label}")
        print(f"       {type(e).__name__}: {e}")
        return False


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--project-context", default=os.getcwd())
    args = parser.parse_args()

    base_dir = args.base_dir
    project_context = args.project_context

    print(f"\n{'─'*55}")
    print(f"  memesis smoke test")
    print(f"  base-dir:        {base_dir}")
    print(f"  project-context: {project_context}")
    print(f"{'─'*55}\n")

    passed = 0
    total = 0

    from core.database import init_db, close_db

    # --- Init ---
    print("Database")
    def do_init():
        bd = init_db(project_context=project_context, base_dir=base_dir)
        return f"→ {bd}"
    total += 1
    if check("init_db() + migrations", do_init):
        passed += 1
    else:
        print("\n  ⚠ Cannot proceed without DB init. Check base-dir path.\n")
        sys.exit(1)

    # --- Ingest ---
    print("\nNative Memory Ingest")
    from core.ingest import NativeMemoryIngestor
    def do_ingest():
        r = NativeMemoryIngestor().ingest(project_context)
        return f"{len(r['ingested'])} ingested, {r['skipped']} skipped, source={r['source']}"
    total += 1
    if check("ingest native memories", do_ingest):
        passed += 1

    # --- Memory count ---
    print("\nMemory Store")
    from core.models import Memory
    def check_instinctive():
        n = Memory.select().where(Memory.stage == "instinctive").count()
        if n == 0:
            raise RuntimeError("No instinctive memories — instinctive layer not seeded")
        return f"{n} instinctive"
    def check_total():
        n = Memory.select().where(Memory.archived_at.is_null(True)).count()
        return f"{n} active memories total"

    for label, fn in [("instinctive memories present", check_instinctive),
                      ("total active memories", check_total)]:
        total += 1
        if check(label, fn):
            passed += 1

    # --- FTS search ---
    print("\nFTS Search")
    from core.database import db
    def check_fts():
        cursor = db.execute_sql("SELECT COUNT(*) FROM memories_fts")
        n = cursor.fetchone()[0]
        if n == 0:
            raise RuntimeError("FTS index is empty — memories may not be indexed")
        return f"{n} rows indexed"
    def check_fts_search():
        cursor = db.execute_sql(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? LIMIT 3",
            ("memory",)
        )
        rows = cursor.fetchall()
        return f"query 'memory' → {len(rows)} result(s)"
    for label, fn in [("FTS index populated", check_fts), ("FTS query works", check_fts_search)]:
        total += 1
        if check(label, fn):
            passed += 1

    # --- Relevance scoring ---
    print("\nRelevance Engine")
    from core.relevance import RelevanceEngine
    def check_relevance():
        eng = RelevanceEngine()
        mems = list(Memory.select().where(Memory.archived_at.is_null(True)).limit(3))
        if not mems:
            return "no memories to score (skipped)"
        scores = [eng.compute_relevance(m, project_context=project_context) for m in mems]
        return f"scored {len(scores)} memories, range [{min(scores):.2f}, {max(scores):.2f}]"
    total += 1
    if check("compute_score() runs without error", check_relevance):
        passed += 1

    # --- Vec store ---
    print("\nVector Store")
    from core.database import get_vec_store
    def check_vec():
        vs = get_vec_store()
        if vs is None or not vs.available:
            return "(unavailable — sqlite-vec not installed, FTS-only mode)"
        return "available"
    total += 1
    if check("vec store check", check_vec):
        passed += 1

    # --- Transcript discovery ---
    print("\nTranscript Discovery")
    from core.transcript_ingest import discover_transcripts
    def check_transcripts():
        ts = discover_transcripts()
        return f"{len(ts)} transcript(s) found"
    total += 1
    if check("discover_transcripts()", check_transcripts):
        passed += 1

    # --- Summary ---
    print(f"\n{'─'*55}")
    status = "PASS" if passed == total else f"PARTIAL ({passed}/{total})"
    icon = "✓" if passed == total else "⚠"
    print(f"  {icon} {status} — {passed}/{total} checks passed")
    print(f"{'─'*55}\n")

    close_db()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
