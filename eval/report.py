#!/usr/bin/env python3
"""
Eval report — produces scored metrics from the eval suite.

Runs against both synthetic fixtures and live observations (if available),
measures retrieval quality, and writes a timestamped JSON report.

Usage:
    python3 eval/report.py                    # Full report
    python3 eval/report.py --live-only        # Live observations only
    python3 eval/report.py --synthetic-only   # Synthetic fixtures only
"""

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import Memory
from core.retrieval import RetrievalEngine
from eval.metrics import precision_at_k, mrr as mrr_metric, injection_utility_rate
from eval.conftest import SYNTHETIC_MEMORIES, seed_store, EVAL_OBSERVATIONS_DB, seed_from_observations
from eval.longmemeval_adapter import LongMemEvalAdapter

REPORT_DIR = Path(__file__).parent / "reports"


# ---------------------------------------------------------------------------
# Retrieval scoring helpers
# ---------------------------------------------------------------------------

def score_injection(engine: RetrievalEngine, session_id: str, memories: list) -> dict:
    """Score injection quality: what fraction of memories appear in context."""
    context = engine.inject_for_session(session_id=session_id)
    context_len = len(context)

    by_stage = {}
    for mem in memories:
        stage = mem.stage
        title = mem.title or ""
        content_snippet = (mem.content or "")[:100]
        found = title in context or content_snippet in context

        if stage not in by_stage:
            by_stage[stage] = {"total": 0, "found": 0}
        by_stage[stage]["total"] += 1
        if found:
            by_stage[stage]["found"] += 1

    total = sum(s["total"] for s in by_stage.values())
    found = sum(s["found"] for s in by_stage.values())

    stage_rates = {}
    for stage, counts in by_stage.items():
        stage_rates[stage] = counts["found"] / max(counts["total"], 1)

    return {
        "injection_rate": found / max(total, 1),
        "injected_count": found,
        "total_memories": total,
        "context_length_chars": context_len,
        "by_stage": stage_rates,
    }


def score_fts(queries_and_expected: list[tuple[str, set[str]]]) -> dict:
    """Score FTS quality: precision and recall for known queries."""
    total_queries = len(queries_and_expected)
    total_hits = 0
    total_relevant_found = 0
    total_relevant = 0
    per_query = []

    for query, expected_titles in queries_and_expected:
        try:
            results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=10)
        except Exception:
            results = []

        result_titles = {r.title for r in results}
        hits = len(results)
        relevant_found = len(expected_titles & result_titles)

        total_hits += hits
        total_relevant_found += relevant_found
        total_relevant += len(expected_titles)

        per_query.append({
            "query": query,
            "hits": hits,
            "relevant_found": relevant_found,
            "relevant_total": len(expected_titles),
            "precision": relevant_found / max(hits, 1),
            "recall": relevant_found / max(len(expected_titles), 1),
        })

    return {
        "total_queries": total_queries,
        "avg_hits": total_hits / max(total_queries, 1),
        "macro_precision": sum(q["precision"] for q in per_query) / max(total_queries, 1),
        "macro_recall": sum(q["recall"] for q in per_query) / max(total_queries, 1),
        "per_query": per_query,
    }


# ---------------------------------------------------------------------------
# Synthetic eval
# ---------------------------------------------------------------------------

def run_synthetic_eval() -> dict:
    """Run eval against the 20 synthetic memories."""
    with tempfile.TemporaryDirectory() as tmp:
        init_db(base_dir=str(Path(tmp) / "synthetic"))
        try:
            seed_store()
            memories = list(Memory.select())
            engine = RetrievalEngine()

            # Injection scoring
            injection = score_injection(engine, "synthetic_eval", memories)

            # FTS scoring — queries that should hit known memories
            fts_queries = [
                ("ruby style", {"Ruby String Style"}),
                ("deploy window", {"Deploy Window"}),
                ("secret vault", {"Secret Management"}),
                ("API versioning", {"API Versioning Policy"}),
                ("test coverage", {"Test Coverage Question"}),
            ]
            fts = score_fts(fts_queries)

            # LongMemEval (stub retrieval — baseline)
            def fts_retrieval(query):
                try:
                    results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=5)
                    return [r.content for r in results]
                except Exception:
                    return []

            adapter = LongMemEvalAdapter(retrieval_fn=fts_retrieval)
            lme_results = adapter.run_fixture()
            lme_agg = adapter.aggregate(lme_results)

            # Stage distribution
            stages = {}
            for m in memories:
                stages[m.stage] = stages.get(m.stage, 0) + 1

            return {
                "memory_count": len(memories),
                "stages": stages,
                "injection": injection,
                "fts": {k: v for k, v in fts.items() if k != "per_query"},
                "fts_detail": fts["per_query"],
                "longmemeval": {
                    "accuracy": lme_agg["accuracy"],
                    "by_category": lme_agg.get("by_category", {}),
                    "total": lme_agg["total"],
                },
            }
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Live eval
# ---------------------------------------------------------------------------

def run_live_eval() -> dict | None:
    """Run eval against real observations from reduce pipeline."""
    if not EVAL_OBSERVATIONS_DB.exists():
        return None

    with tempfile.TemporaryDirectory() as tmp:
        init_db(base_dir=str(Path(tmp) / "live"))
        try:
            ids = seed_from_observations()
            memories = list(Memory.select())
            engine = RetrievalEngine()

            # Injection scoring
            injection = score_injection(engine, "live_eval", memories)

            # FTS — use top observation titles as queries (self-retrieval test)
            top_memories = sorted(memories, key=lambda m: m.reinforcement_count or 0, reverse=True)[:10]
            fts_queries = []
            for m in top_memories:
                # Extract a keyword from the title
                words = (m.title or "").split()
                if len(words) >= 2:
                    query = " ".join(words[:3])
                    fts_queries.append((query, {m.title}))

            fts = score_fts(fts_queries) if fts_queries else {
                "total_queries": 0, "avg_hits": 0, "macro_precision": 0, "macro_recall": 0,
            }

            # LongMemEval against real memories
            def fts_retrieval(query):
                try:
                    results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=5)
                    return [r.content for r in results]
                except Exception:
                    return []

            adapter = LongMemEvalAdapter(retrieval_fn=fts_retrieval)
            lme_results = adapter.run_fixture()
            lme_agg = adapter.aggregate(lme_results)

            # Stage distribution
            stages = {}
            for m in memories:
                stages[m.stage] = stages.get(m.stage, 0) + 1

            # Observation type distribution
            obs_types = {}
            for m in memories:
                for tag in m.tag_list:
                    if tag.startswith("type:"):
                        t = tag.split(":", 1)[1]
                        obs_types[t] = obs_types.get(t, 0) + 1

            # Top observations by reinforcement
            top = []
            for m in top_memories[:5]:
                top.append({
                    "title": m.title,
                    "reinforcement_count": m.reinforcement_count,
                    "stage": m.stage,
                    "importance": m.importance,
                })

            return {
                "memory_count": len(memories),
                "stages": stages,
                "observation_types": obs_types,
                "top_observations": top,
                "injection": injection,
                "fts": {k: v for k, v in fts.items() if k != "per_query"},
                "fts_detail": fts.get("per_query", []),
                "longmemeval": {
                    "accuracy": lme_agg["accuracy"],
                    "by_category": lme_agg.get("by_category", {}),
                    "total": lme_agg["total"],
                },
            }
        finally:
            close_db()


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(synthetic: bool = True, live: bool = True) -> dict:
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "seed": 42,
    }

    if synthetic:
        report["synthetic"] = run_synthetic_eval()

    if live:
        live_result = run_live_eval()
        if live_result:
            report["live"] = live_result
        else:
            report["live"] = {"error": "eval-observations.db not found"}

    return report


def print_report(report: dict):
    """Pretty-print the report to stdout."""
    print("=" * 65)
    print("  MEMESIS EVAL REPORT")
    print(f"  {report['timestamp']}")
    print("=" * 65)

    for section_name in ("synthetic", "live"):
        section = report.get(section_name)
        if not section or "error" in section:
            if section and "error" in section:
                print(f"\n  [{section_name.upper()}] {section['error']}")
            continue

        print(f"\n  [{section_name.upper()}]")
        print(f"  Memories: {section['memory_count']}")
        print(f"  Stages: {section['stages']}")

        inj = section["injection"]
        print(f"\n  Injection:")
        print(f"    Rate:     {inj['injection_rate']:.1%} ({inj['injected_count']}/{inj['total_memories']})")
        print(f"    Context:  {inj['context_length_chars']:,} chars")
        for stage, rate in inj.get("by_stage", {}).items():
            print(f"    {stage:20s} {rate:.0%}")

        fts = section["fts"]
        if fts.get("total_queries", 0) > 0:
            print(f"\n  FTS:")
            print(f"    Queries:   {fts['total_queries']}")
            print(f"    Avg hits:  {fts['avg_hits']:.1f}")
            print(f"    Precision: {fts['macro_precision']:.1%}")
            print(f"    Recall:    {fts['macro_recall']:.1%}")

        lme = section.get("longmemeval", {})
        if lme.get("total", 0) > 0:
            print(f"\n  LongMemEval:")
            print(f"    Accuracy:  {lme['accuracy']:.1%} ({int(lme['accuracy'] * lme['total'])}/{lme['total']})")
            for cat, acc in lme.get("by_category", {}).items():
                print(f"    {cat:30s} {acc:.0%}")

        if "observation_types" in section:
            print(f"\n  Observation types:")
            for t, c in sorted(section["observation_types"].items(), key=lambda x: -x[1]):
                bar = "#" * c
                print(f"    {t:25s} {c:3d}  {bar}")

        if "top_observations" in section:
            print(f"\n  Top observations:")
            for obs in section["top_observations"]:
                print(f"    x{obs['reinforcement_count']:3d} [{obs['stage'][:5]}] {obs['title']}")

    print(f"\n{'=' * 65}")


def main():
    synthetic, live = True, True
    for arg in sys.argv[1:]:
        if arg == "--live-only":
            synthetic = False
        elif arg == "--synthetic-only":
            live = False

    report = build_report(synthetic=synthetic, live=live)

    # Write timestamped JSON
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"eval-{ts}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Also write latest symlink
    latest_path = REPORT_DIR / "latest.json"
    latest_path.unlink(missing_ok=True)
    latest_path.symlink_to(report_path.name)

    print_report(report)
    print(f"\n  Report: {report_path}")
    print(f"  Latest: {latest_path}")


if __name__ == "__main__":
    main()
