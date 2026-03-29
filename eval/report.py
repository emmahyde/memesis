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
GOLD_SET_PATH = Path(__file__).parent / "gold" / "sessions.json"


# ---------------------------------------------------------------------------
# Retrieval scoring helpers
# ---------------------------------------------------------------------------

def make_retrieval_fn(engine: RetrievalEngine, session_id: str):
    """Build a retrieval function that combines FTS + injection context."""
    context = engine.inject_for_session(session_id=session_id)

    def retrieve(query: str) -> list[str]:
        results = []
        # Include injection context as a retrieval source
        if context:
            results.append(context)
        # Also try FTS
        try:
            fts_results = Memory.search_fts(Memory.sanitize_fts_term(query), limit=5)
            results.extend(r.content for r in fts_results)
        except Exception:
            pass
        return results

    return retrieve


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

            # LongMemEval — combined FTS + injection retrieval
            retrieval_fn = make_retrieval_fn(engine, "synthetic_longmemeval")
            adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
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

            # LongMemEval — combined FTS + injection retrieval
            retrieval_fn = make_retrieval_fn(engine, "live_longmemeval")
            adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
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
# Gold set eval — storage quality
# ---------------------------------------------------------------------------

def run_gold_eval() -> dict | None:
    """Score reduce output against hand-labeled gold set.

    Measures:
      - Per-session precision (correct / (correct + false))
      - Per-session recall (correct / (correct + missing))
      - Dedup quality (expected merges that still exist as separate observations)
      - Classification accuracy (misclassified observations)
    """
    if not GOLD_SET_PATH.exists() or not EVAL_OBSERVATIONS_DB.exists():
        return None

    with open(GOLD_SET_PATH) as f:
        gold = json.load(f)

    db = sqlite3.connect(str(EVAL_OBSERVATIONS_DB))
    db.row_factory = sqlite3.Row

    # Build observation lookup: id -> row
    all_obs = {r["id"]: dict(r) for r in db.execute("SELECT * FROM observations")}

    per_session = []
    total_correct = 0
    total_false = 0
    total_missing_per_session = 0

    for sess in gold["sessions"]:
        sid = sess["session_id"]

        # Find observations that source this session
        sourced = []
        for obs in all_obs.values():
            if sid in (obs.get("sources") or ""):
                sourced.append(obs)

        correct_ids = {c["id"] for c in sess["correct"]}
        false_ids = {f["id"] for f in sess["false_reinforcements"]}
        missing_count = len(sess.get("missing", []))

        # Match sourced observations against labels
        sourced_ids = {o["id"] for o in sourced}
        true_positives = len(correct_ids & sourced_ids)
        false_positives = len(false_ids & sourced_ids)
        unlabeled = sourced_ids - correct_ids - false_ids

        n_labeled = true_positives + false_positives
        precision = true_positives / max(n_labeled, 1)
        recall = true_positives / max(true_positives + missing_count, 1)

        total_correct += true_positives
        total_false += false_positives
        total_missing_per_session += missing_count

        per_session.append({
            "session_id": sid,
            "project": sess["project"],
            "messages": sess["messages"],
            "sourced_count": len(sourced),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "unlabeled": len(unlabeled),
            "missing": missing_count,
            "precision": precision,
            "recall": recall,
        })

    # Dedup eval: check if expected-merge pairs still both exist
    merge_results = []
    for merge in gold.get("expected_merges", []):
        ids = merge["ids"]
        both_exist = all(i in all_obs for i in ids)
        titles = [all_obs[i]["title"] if i in all_obs else "MISSING" for i in ids]
        merge_results.append({
            "ids": ids,
            "titles": titles,
            "still_separate": both_exist,
        })
    unmerged_count = sum(1 for m in merge_results if m["still_separate"])

    # Classification eval
    misclass = gold.get("misclassifications", [])
    misclass_results = []
    for mc in misclass:
        checked = []
        for oid in mc["ids"]:
            if oid in all_obs:
                actual = all_obs[oid].get("observation_type", "")
                checked.append({
                    "id": oid,
                    "title": all_obs[oid]["title"],
                    "actual_type": actual,
                    "expected_type": mc["expected_type"],
                    "correct": actual == mc["expected_type"],
                })
        misclass_results.extend(checked)
    misclass_correct = sum(1 for m in misclass_results if m["correct"])
    misclass_total = len(misclass_results)

    # Aggregate
    n_sessions = len(per_session)
    macro_precision = sum(s["precision"] for s in per_session) / max(n_sessions, 1)
    macro_recall = sum(s["recall"] for s in per_session) / max(n_sessions, 1)

    db.close()

    return {
        "sessions_evaluated": n_sessions,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "total_true_positives": total_correct,
        "total_false_positives": total_false,
        "total_missing": total_missing_per_session,
        "per_session": per_session,
        "dedup": {
            "expected_merges": len(merge_results),
            "still_separate": unmerged_count,
            "details": merge_results,
        },
        "classification": {
            "total_checked": misclass_total,
            "correctly_classified": misclass_correct,
            "accuracy": misclass_correct / max(misclass_total, 1),
            "details": misclass_results[:5],  # Sample for readability
        },
        "missing_global": gold.get("missing_global", {}),
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(synthetic: bool = True, live: bool = True, gold: bool = True) -> dict:
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

    if gold:
        gold_result = run_gold_eval()
        if gold_result:
            report["gold"] = gold_result
        else:
            report["gold"] = {"error": "gold set or observations DB not found"}

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

    # Gold set section
    gold_section = report.get("gold")
    if gold_section and "error" not in gold_section:
        print(f"\n  [GOLD SET — Storage Quality]")
        print(f"  Sessions: {gold_section['sessions_evaluated']}")
        print(f"  Macro precision: {gold_section['macro_precision']:.1%}")
        print(f"  Macro recall:    {gold_section['macro_recall']:.1%}")
        print(f"  True positives:  {gold_section['total_true_positives']}")
        print(f"  False positives: {gold_section['total_false_positives']}")
        print(f"  Missing:         {gold_section['total_missing']}")

        print(f"\n  Per session:")
        for s in gold_section["per_session"]:
            print(f"    {s['session_id'][:8]} ({s['project']:20s} {s['messages']:3d}msg)"
                  f"  P={s['precision']:.0%}  R={s['recall']:.0%}"
                  f"  TP={s['true_positives']} FP={s['false_positives']}"
                  f"  miss={s['missing']} unlbl={s['unlabeled']}")

        dedup = gold_section["dedup"]
        print(f"\n  Dedup: {dedup['still_separate']}/{dedup['expected_merges']} pairs still unmerged")
        for d in dedup["details"]:
            status = "UNMERGED" if d["still_separate"] else "merged"
            print(f"    [{status}] #{d['ids'][0]} / #{d['ids'][1]}")

        cls = gold_section["classification"]
        print(f"\n  Classification: {cls['correctly_classified']}/{cls['total_checked']}"
              f" ({cls['accuracy']:.0%} correct)")

        missing_global = gold_section.get("missing_global", {})
        for category, items in missing_global.items():
            print(f"\n  Missing ({category}): {len(items)} patterns undetected")

    elif gold_section and "error" in gold_section:
        print(f"\n  [GOLD SET] {gold_section['error']}")

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
