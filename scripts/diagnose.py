#!/usr/bin/env python3
"""Memory system diagnostics — a mirror for the agent to see itself.

Usage:
    python3 scripts/diagnose.py [project_context]

    If project_context is omitted, uses the current directory.

Prints a readable summary of the full memory system state:
  - Stage counts and archived count
  - Relevance scores (top and bottom)
  - Archive candidates (approaching threshold)
  - Self-model tendencies
  - Recent consolidation decisions
  - Token budget usage
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.manifest import ManifestGenerator
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector, SELF_MODEL_TITLE
from core.storage import MemoryStore


def header(text: str) -> str:
    return f"\n{'─' * 60}\n  {text}\n{'─' * 60}"


def diagnose(project_context: str = None):
    if project_context is None:
        project_context = os.getcwd()

    store = MemoryStore(project_context=project_context)
    relevance = RelevanceEngine(store)
    manifest = ManifestGenerator(store)
    reflector = SelfReflector(store)

    print(header("MEMESIS — Memory System Diagnostics"))
    print(f"  Project: {project_context}")
    print(f"  Store:   {store.base_dir}")
    print(f"  Time:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Stage counts ──────────────────────────────────────────
    print(header("Stage Counts"))
    total = 0
    for stage in ("instinctive", "crystallized", "consolidated", "ephemeral"):
        active = store.list_by_stage(stage)
        all_inc = store.list_by_stage(stage, include_archived=True)
        archived_in_stage = len(all_inc) - len(active)
        total += len(active)
        suffix = f" (+{archived_in_stage} archived)" if archived_in_stage > 0 else ""
        print(f"  {stage:14s}  {len(active):4d} active{suffix}")

    archived = store.list_archived()
    print(f"  {'archived':14s}  {len(archived):4d} total")
    print(f"  {'TOTAL':14s}  {total:4d} active")

    # ── Token budget ──────────────────────────────────────────
    print(header("Token Budget"))
    token_count, fraction = manifest.estimate_token_budget()
    print(f"  Injection size:  ~{token_count:,} tokens ({fraction:.1%} of 200K window)")

    # ── Relevance scores ──────────────────────────────────────
    scored = relevance.score_all(project_context)
    if scored:
        print(header(f"Relevance Scores ({len(scored)} active memories)"))

        print("  Top 10:")
        for s in scored[:10]:
            print(f"    {s['relevance']:.3f}  [{s['stage'][:5]}]  {s['title'] or '(untitled)'}")

        if len(scored) > 10:
            print(f"\n  Bottom 5:")
            for s in scored[-5:]:
                days = s.get("days_since_activity", 0)
                print(f"    {s['relevance']:.3f}  [{s['stage'][:5]}]  {s['title'] or '(untitled)'}  ({days:.0f}d ago)")
    else:
        print(header("Relevance Scores"))
        print("  (no active memories to score)")

    # ── Archive candidates ────────────────────────────────────
    candidates = relevance.get_archival_candidates(project_context)
    if candidates:
        print(header(f"Archive Candidates ({len(candidates)} below threshold)"))
        for c in candidates[:10]:
            print(f"    {c.get('relevance', 0):.3f}  {c.get('title', '(untitled)')}")
    else:
        print(header("Archive Candidates"))
        print("  (none — all memories above threshold)")

    # ── Archived memories ─────────────────────────────────────
    if archived:
        print(header(f"Archived ({len(archived)})"))
        for a in archived[:10]:
            print(f"    [{a.get('stage', '?')[:5]}]  {a.get('title', '(untitled)')}")
            print(f"           archived: {a.get('archived_at', '?')[:10]}")
    else:
        print(header("Archived"))
        print("  (none)")

    # ── Self-model state ──────────────────────────────────────
    print(header("Self-Model"))
    model = reflector._find_self_model()
    if model:
        full = store.get(model["id"])
        content = full.get("content", "")
        # Extract tendency headers
        tendencies = [
            line.strip("# ").strip()
            for line in content.split("\n")
            if line.startswith("### ") and "DEPRECATED" not in content.split(line)[0][-200:]
        ]
        if tendencies:
            print(f"  {len(tendencies)} known tendencies:")
            for t in tendencies:
                print(f"    • {t}")
        # Find last updated date
        for line in content.split("\n"):
            if line.startswith("Last updated:"):
                print(f"\n  {line.strip()}")
                break
        print(f"  Importance: {model.get('importance', '?')}")
    else:
        print("  (not seeded yet)")

    # ── Memory effectiveness ────────────────────────────────────
    print(header("Memory Effectiveness"))
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Overall injection/usage stats
        total_injections = conn.execute("SELECT COUNT(*) FROM retrieval_log").fetchone()[0]
        total_used = conn.execute("SELECT COUNT(*) FROM retrieval_log WHERE was_used = 1").fetchone()[0]
        unique_sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM retrieval_log").fetchone()[0]

        if total_injections > 0:
            print(f"  Total injections: {total_injections} across {unique_sessions} sessions")
            print(f"  Used: {total_used} ({total_used/total_injections:.0%})")
            print(f"  Unused: {total_injections - total_used} ({(total_injections-total_used)/total_injections:.0%})")

            # Most effective (highest use rate, min 3 injections)
            gold = conn.execute("""
                SELECT memory_id,
                       COUNT(*) as inj,
                       SUM(CASE WHEN was_used = 1 THEN 1 ELSE 0 END) as used
                FROM retrieval_log
                GROUP BY memory_id
                HAVING inj >= 3
                ORDER BY CAST(used AS REAL) / inj DESC
                LIMIT 5
            """).fetchall()
            if gold:
                print(f"\n  Most effective (≥3 injections, by use rate):")
                for row in gold:
                    mem = None
                    try:
                        mem = store.get(row["memory_id"])
                    except (KeyError, ValueError):
                        pass
                    title = mem["title"] if mem else row["memory_id"][:8]
                    rate = row["used"] / row["inj"]
                    print(f"    {rate:.0%} used ({row['used']}/{row['inj']})  {title}")

            # Noise: injected 5+ times, never used
            noise = conn.execute("""
                SELECT memory_id, COUNT(*) as inj
                FROM retrieval_log
                WHERE was_used = 0 OR was_used IS NULL
                GROUP BY memory_id
                HAVING inj >= 5
                ORDER BY inj DESC
                LIMIT 5
            """).fetchall()
            if noise:
                print(f"\n  Potential noise (injected 5+ times, never used):")
                for row in noise:
                    mem = None
                    try:
                        mem = store.get(row["memory_id"])
                    except (KeyError, ValueError):
                        pass
                    title = mem["title"] if mem else row["memory_id"][:8]
                    print(f"    {row['inj']}x injected, 0 used  {title}")
        else:
            print("  (no retrieval history yet — run a few sessions first)")

    # ── Recent consolidation ──────────────────────────────────
    print(header("Recent Consolidation (last 20 decisions)"))
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM consolidation_log ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()

    if rows:
        # Summary counts
        actions = {}
        for r in rows:
            a = r["action"]
            actions[a] = actions.get(a, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(actions.items()))
        print(f"  {summary}")
        print()
        for r in rows[:10]:
            ts = r["timestamp"][:16] if r["timestamp"] else "?"
            print(f"    {ts}  {r['action']:10s}  {r['rationale'][:60] if r['rationale'] else ''}")
    else:
        print("  (no consolidation history yet)")

    # ── Consolidation counter ─────────────────────────────────
    counter_path = store.base_dir / "meta" / "consolidation-count.json"
    if counter_path.exists():
        import json
        try:
            count = json.loads(counter_path.read_text()).get("count", 0)
            next_reflection = 5 - (count % 5)
            print(f"\n  Consolidations: {count}  (next self-reflection in {next_reflection})")
        except Exception:
            pass

    print(f"\n{'─' * 60}")
    store.close()


if __name__ == "__main__":
    ctx = sys.argv[1] if len(sys.argv) > 1 else None
    diagnose(ctx)
