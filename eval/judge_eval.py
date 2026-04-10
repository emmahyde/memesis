#!/usr/bin/env python3
"""
Run LLM-as-Judge evaluators against the observation store and retrieval system.

Usage:
    python3 eval/judge_eval.py                    # Run all three judges
    python3 eval/judge_eval.py --judge retrieval   # Just retrieval relevance
    python3 eval/judge_eval.py --judge quality      # Just observation quality
    python3 eval/judge_eval.py --judge dedup        # Just dedup accuracy
    python3 eval/judge_eval.py --sample 20          # Sample size per judge (default 20)
"""

import json
import random
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.judges import judge_retrieval_relevance, judge_observation_quality, judge_dedup_accuracy

EVAL_DB = Path(__file__).parent / "eval-observations.db"
REPORT_DIR = Path(__file__).parent / "reports"


# ---------------------------------------------------------------------------
# Retrieval relevance
# ---------------------------------------------------------------------------

RETRIEVAL_QUERIES = [
    "how does the user handle code review",
    "file write contention and race conditions",
    "PR formatting and body conventions",
    "autonomous agent delegation",
    "communication style and corrections",
    "context recovery after interruption",
    "worktree directory structure",
    "testing and verification before shipping",
    "scope cutting mid-execution",
    "multi-agent orchestration pipeline",
]


def eval_retrieval(sample: int = 10) -> dict:
    """Run retrieval relevance judge on queries."""
    from core.database import init_db, close_db
    from core.models import Memory
    from core.retrieval import RetrievalEngine
    from eval.conftest import seed_from_observations

    queries = RETRIEVAL_QUERIES[:sample]
    results_list = []

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "judge_eval"
        init_db(base_dir=str(base))
        seed_from_observations()
        engine = RetrievalEngine()

        for query in queries:
            t0 = time.time()
            retrieved = engine.get_crystallized_for_context(query=query)
            top5 = [{"title": m.title or "", "content": m.content or ""} for m in retrieved[:5]]

            verdict = judge_retrieval_relevance(query, top5)
            elapsed = time.time() - t0

            results_list.append({
                "query": query,
                "top5_titles": [r["title"][:60] for r in top5],
                "critique": verdict["critique"],
                "result": verdict["result"],
                "time_s": round(elapsed, 1),
            })
            _print_verdict("retrieval", query[:50], verdict["result"], elapsed)

        close_db()

    pass_count = sum(1 for r in results_list if r["result"] == "Pass")
    return {
        "judge": "retrieval_relevance",
        "total": len(results_list),
        "pass": pass_count,
        "fail": len(results_list) - pass_count,
        "pass_rate": pass_count / max(len(results_list), 1),
        "details": results_list,
    }


# ---------------------------------------------------------------------------
# Observation quality
# ---------------------------------------------------------------------------

def eval_quality(sample: int = 20) -> dict:
    """Run observation quality judge on a sample of observations."""
    conn = sqlite3.connect(str(EVAL_DB))
    rows = conn.execute(
        "SELECT title, content, observation_type, count FROM observations ORDER BY RANDOM()"
    ).fetchall()
    conn.close()

    sampled = rows[:sample]
    results_list = []

    for title, content, obs_type, count in sampled:
        t0 = time.time()
        verdict = judge_observation_quality(title, content, obs_type, count)
        elapsed = time.time() - t0

        results_list.append({
            "title": title[:60],
            "observation_type": obs_type,
            "count": count,
            "critique": verdict["critique"],
            "result": verdict["result"],
            "time_s": round(elapsed, 1),
        })
        _print_verdict("quality", title[:50], verdict["result"], elapsed)

    pass_count = sum(1 for r in results_list if r["result"] == "Pass")
    return {
        "judge": "observation_quality",
        "total": len(results_list),
        "pass": pass_count,
        "fail": len(results_list) - pass_count,
        "pass_rate": pass_count / max(len(results_list), 1),
        "details": results_list,
    }


# ---------------------------------------------------------------------------
# Dedup accuracy
# ---------------------------------------------------------------------------

def _find_candidate_dupes(conn: sqlite3.Connection, top_n: int = 20) -> list:
    """Find observation pairs with high title word overlap."""
    rows = conn.execute("SELECT id, title, content, count FROM observations").fetchall()
    pairs = []
    for a, b in combinations(rows, 2):
        wa = set(a[1].lower().split())
        wb = set(b[1].lower().split())
        if not wa or not wb:
            continue
        overlap = len(wa & wb) / len(wa | wb)
        if overlap > 0.25:
            pairs.append((overlap, a, b))
    pairs.sort(reverse=True)
    return pairs[:top_n]


def eval_dedup(sample: int = 20) -> dict:
    """Run dedup accuracy judge on candidate duplicate pairs."""
    conn = sqlite3.connect(str(EVAL_DB))
    candidates = _find_candidate_dupes(conn, top_n=sample)
    conn.close()

    if not candidates:
        return {"judge": "dedup_accuracy", "total": 0, "pass": 0, "fail": 0,
                "pass_rate": 0, "details": [], "note": "No candidate pairs found"}

    results_list = []
    for overlap, a, b in candidates:
        t0 = time.time()
        verdict = judge_dedup_accuracy(a[1], a[2], b[1], b[2])
        elapsed = time.time() - t0

        results_list.append({
            "pair": f"#{a[0]} vs #{b[0]}",
            "title_a": a[1][:60],
            "title_b": b[1][:60],
            "word_overlap": round(overlap, 2),
            "critique": verdict["critique"],
            "result": verdict["result"],
            "time_s": round(elapsed, 1),
        })
        label = "separate" if verdict["result"] == "Pass" else "SHOULD MERGE"
        _print_verdict("dedup", f"#{a[0]} vs #{b[0]}", f"{verdict['result']} ({label})", elapsed)

    # For dedup: Fail = should have been merged = dedup missed it
    fail_count = sum(1 for r in results_list if r["result"] == "Fail")
    return {
        "judge": "dedup_accuracy",
        "total": len(results_list),
        "pass": len(results_list) - fail_count,  # correctly separate
        "fail": fail_count,  # missed merges
        "miss_rate": fail_count / max(len(results_list), 1),
        "details": results_list,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_verdict(judge: str, label: str, result: str, elapsed: float):
    mark = "✓" if "Pass" in result else "✗"
    print(f"  {mark} [{judge:10s}] {label:50s} {result:20s} {elapsed:.1f}s", file=sys.stderr)


def print_summary(results: list[dict]):
    print("\n" + "=" * 70)
    print("  MEMESIS JUDGE EVALUATION")
    print(f"  {datetime.now().isoformat()}")
    print("=" * 70)

    for r in results:
        judge = r["judge"]
        total = r["total"]
        if judge == "dedup_accuracy":
            miss = r.get("fail", 0)
            print(f"\n  {judge}: {total} pairs checked, {miss} missed merges ({r.get('miss_rate', 0):.0%} miss rate)")
        else:
            passed = r["pass"]
            rate = r.get("pass_rate", 0)
            print(f"\n  {judge}: {passed}/{total} pass ({rate:.0%})")

        # Show failures
        for d in r.get("details", []):
            if d["result"] == "Fail":
                label = d.get("query") or d.get("title") or d.get("pair", "?")
                print(f"    ✗ {label[:65]}")
                print(f"      {d['critique'][:120]}")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    judges_to_run = ["retrieval", "quality", "dedup"]
    sample = 20

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--judge" and i + 1 < len(args):
            judges_to_run = [args[i + 1]]
            i += 2
        elif args[i] == "--sample" and i + 1 < len(args):
            sample = int(args[i + 1])
            i += 2
        else:
            i += 1

    random.seed(42)
    results = []

    for judge in judges_to_run:
        print(f"\n  Running {judge} judge (sample={sample})...\n", file=sys.stderr)
        if judge == "retrieval":
            results.append(eval_retrieval(sample=min(sample, len(RETRIEVAL_QUERIES))))
        elif judge == "quality":
            results.append(eval_quality(sample=sample))
        elif judge == "dedup":
            results.append(eval_dedup(sample=sample))

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"judge-eval-{ts}.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print_summary(results)
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    main()
