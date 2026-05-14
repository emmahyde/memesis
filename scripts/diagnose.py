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

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, get_base_dir, get_db_path, init_db
from core.manifest import ManifestGenerator
from core.models import ConsolidationLog, Memory, RetrievalLog, db
from core.relevance import RelevanceEngine
from core.self_reflection import SelfReflector, SELF_MODEL_TITLE

from peewee import Case, fn


def header(text: str) -> str:
    return f"\n{chr(0x2500) * 60}\n  {text}\n{chr(0x2500) * 60}"


def diagnose(project_context: str = None):
    if project_context is None:
        project_context = os.getcwd()

    base_dir = init_db(project_context=project_context)
    relevance = RelevanceEngine()
    manifest = ManifestGenerator()
    reflector = SelfReflector()

    print(header("MEMESIS — Memory System Diagnostics"))
    print(f"  Project: {project_context}")
    print(f"  Store:   {base_dir}")
    print(f"  Time:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # -- Stage counts --
    print(header("Stage Counts"))
    total = 0
    for stage in ("instinctive", "crystallized", "consolidated", "ephemeral"):
        active = list(Memory.by_stage(stage))
        all_inc = list(Memory.by_stage(stage, include_archived=True))
        archived_in_stage = len(all_inc) - len(active)
        total += len(active)
        suffix = f" (+{archived_in_stage} archived)" if archived_in_stage > 0 else ""
        print(f"  {stage:14s}  {len(active):4d} active{suffix}")

    archived_count = Memory.select().where(Memory.archived_at.is_null(False)).count()
    print(f"  {'archived':14s}  {archived_count:4d} total")
    print(f"  {'TOTAL':14s}  {total:4d} active")

    # -- Token budget --
    print(header("Token Budget"))
    token_count, fraction = manifest.estimate_token_budget()
    print(f"  Injection size:  ~{token_count:,} tokens ({fraction:.1%} of 200K window)")

    # -- Relevance scores --
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

    # -- Archive candidates --
    candidates = relevance.get_archival_candidates(project_context)
    if candidates:
        print(header(f"Archive Candidates ({len(candidates)} below threshold)"))
        for c in candidates[:10]:
            print(f"    {getattr(c, '_relevance', 0):.3f}  {c.title or '(untitled)'}")
    else:
        print(header("Archive Candidates"))
        print("  (none — all memories above threshold)")

    # -- Archived memories --
    archived = list(
        Memory.select()
        .where(Memory.archived_at.is_null(False))
        .order_by(Memory.archived_at.desc())
    )
    if archived:
        print(header(f"Archived ({len(archived)})"))
        for a in archived[:10]:
            print(f"    [{(a.stage or '?')[:5]}]  {a.title or '(untitled)'}")
            print(f"           archived: {(a.archived_at or '?')[:10]}")
    else:
        print(header("Archived"))
        print("  (none)")

    # -- Self-model state --
    print(header("Self-Model"))
    model = reflector._find_self_model()
    if model:
        content = model.content or ""
        tendencies = [
            line.strip("# ").strip()
            for line in content.split("\n")
            if line.startswith("### ") and "DEPRECATED" not in content.split(line)[0][-200:]
        ]
        if tendencies:
            print(f"  {len(tendencies)} known tendencies:")
            for t in tendencies:
                print(f"    * {t}")
        for line in content.split("\n"):
            if line.startswith("Last updated:"):
                print(f"\n  {line.strip()}")
                break
        print(f"  Importance: {model.importance}")
    else:
        print("  (not seeded yet)")

    # -- Memory effectiveness --
    print(header("Memory Effectiveness"))
    total_injections = RetrievalLog.select().count()
    total_used = RetrievalLog.select().where(RetrievalLog.was_used == 1).count()
    unique_sessions = (
        RetrievalLog.select(fn.COUNT(fn.DISTINCT(RetrievalLog.session_id))).scalar() or 0
    )

    if total_injections > 0:
        print(f"  Total injections: {total_injections} across {unique_sessions} sessions")
        print(f"  Used: {total_used} ({total_used/total_injections:.0%})")
        print(f"  Unused: {total_injections - total_used} ({(total_injections-total_used)/total_injections:.0%})")

        # Most effective
        gold = (
            RetrievalLog.select(
                RetrievalLog.memory_id,
                fn.COUNT(RetrievalLog.id).alias('inj'),
                fn.SUM(Case(None, [(RetrievalLog.was_used == 1, 1)], 0)).alias('used'),
            )
            .group_by(RetrievalLog.memory_id)
            .having(fn.COUNT(RetrievalLog.id) >= 3)
            .order_by(fn.SUM(Case(None, [(RetrievalLog.was_used == 1, 1)], 0)).cast('REAL') / fn.COUNT(RetrievalLog.id))
            .limit(5)
        )
        gold_rows = list(gold)
        if gold_rows:
            print(f"\n  Most effective (>=3 injections, by use rate):")
            for row in gold_rows:
                try:
                    mem = Memory.get_by_id(row.memory_id)
                    title = mem.title
                except Memory.DoesNotExist:
                    title = row.memory_id[:8]
                inj = row.inj
                used = row.used or 0
                rate = used / inj if inj > 0 else 0
                print(f"    {rate:.0%} used ({used}/{inj})  {title}")

        # Noise
        noise = (
            RetrievalLog.select(
                RetrievalLog.memory_id,
                fn.COUNT(RetrievalLog.id).alias('inj'),
            )
            .where((RetrievalLog.was_used == 0) | (RetrievalLog.was_used.is_null()))
            .group_by(RetrievalLog.memory_id)
            .having(fn.COUNT(RetrievalLog.id) >= 5)
            .order_by(fn.COUNT(RetrievalLog.id).desc())
            .limit(5)
        )
        noise_rows = list(noise)
        if noise_rows:
            print(f"\n  Potential noise (injected 5+ times, never used):")
            for row in noise_rows:
                try:
                    mem = Memory.get_by_id(row.memory_id)
                    title = mem.title
                except Memory.DoesNotExist:
                    title = row.memory_id[:8]
                print(f"    {row.inj}x injected, 0 used  {title}")
    else:
        print("  (no retrieval history yet — run a few sessions first)")

    # -- Recent consolidation --
    print(header("Recent Consolidation (last 20 decisions)"))
    rows = list(
        ConsolidationLog.select()
        .order_by(ConsolidationLog.timestamp.desc())
        .limit(20)
    )

    if rows:
        actions = {}
        for r in rows:
            a = r.action or "?"
            actions[a] = actions.get(a, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(actions.items()))
        print(f"  {summary}")
        print()
        for r in rows[:10]:
            ts = (r.timestamp or "?")[:16]
            print(f"    {ts}  {(r.action or ''):10s}  {(r.rationale or '')[:60]}")
    else:
        print("  (no consolidation history yet)")

    # -- Consolidation counter --
    counter_path = base_dir / "meta" / "consolidation-count.json"
    if counter_path.exists():
        try:
            count = json.loads(counter_path.read_text()).get("count", 0)
            next_reflection = 5 - (count % 5)
            print(f"\n  Consolidations: {count}  (next self-reflection in {next_reflection})")
        except Exception:
            pass

    print(f"\n{chr(0x2500) * 60}")
    close_db()


if __name__ == "__main__":
    ctx = sys.argv[1] if len(sys.argv) > 1 else None
    diagnose(ctx)
