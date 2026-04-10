#!/usr/bin/env python3
"""
Validate the Observation Quality judge against hand-labeled ground truth.

Builds a labeled dataset from:
  1. Gold set correct observations (expected Pass)
  2. Gold set missing_global patterns, synthesized as observations (expected Pass)
  3. Fabricated garbage observations (expected Fail)

Runs each through judge_observation_quality and computes TPR, TNR, and
a confusion matrix.

Usage:
    python3 eval/validate_judge.py
    python3 eval/validate_judge.py --dry-run    # Show dataset without calling judge
"""

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.judges import judge_observation_quality

EVAL_DB = Path(__file__).parent / "eval-observations.db"
GOLD_SET = Path(__file__).parent / "gold" / "sessions.json"
REPORT_DIR = Path(__file__).parent / "reports"

# ---------------------------------------------------------------------------
# Garbage observations — things the judge SHOULD fail
# ---------------------------------------------------------------------------

GARBAGE_OBSERVATIONS = [
    {
        "title": "Deleting tmp/fixtury.yml forces full fixture rebuild",
        "content": "When the fixtury cache is stale after db:reset, rake fixtury:reset reports 'no changes.' Delete tmp/fixtury.yml to force rebuild.",
        "observation_type": "correction",
        "count": 3,
        "expected": "Fail",
        "reason": "Codebase-derivable technical fact about a specific tool",
    },
    {
        "title": "The app uses Rails 7.1 with Sidekiq for background jobs",
        "content": "The application runs on Rails 7.1 and processes background work through Sidekiq with Redis as the broker.",
        "observation_type": "workflow_pattern",
        "count": 5,
        "expected": "Fail",
        "reason": "Derivable from Gemfile/code — no personal signal",
    },
    {
        "title": "User prefers clean code",
        "content": "The user likes code to be clean and well-organized.",
        "observation_type": "preference_signal",
        "count": 2,
        "expected": "Fail",
        "reason": "Too vague — applies to any engineer",
    },
    {
        "title": "Shutdown protocol: respond to shutdown_request with shutdown_response",
        "content": "Team-lead sends structured JSON shutdown_request messages with a requestId field. The correct response is a shutdown_response echoing the requestId.",
        "observation_type": "workflow_pattern",
        "count": 4,
        "expected": "Fail",
        "reason": "Technical protocol derivable from codebase",
    },
    {
        "title": "The deploy pipeline runs on GitHub Actions",
        "content": "CI/CD is handled by GitHub Actions workflows defined in .github/workflows/. PRs trigger test suites and deploys go through staging first.",
        "observation_type": "workflow_pattern",
        "count": 2,
        "expected": "Fail",
        "reason": "Infrastructure fact derivable from repo",
    },
    {
        "title": "User ran git rebase yesterday",
        "content": "During the March 28 session, the user rebased a feature branch onto main to resolve a conflict.",
        "observation_type": "workflow_pattern",
        "count": 1,
        "expected": "Fail",
        "reason": "Ephemeral one-time event, not a durable pattern",
    },
    {
        "title": "The database uses PostgreSQL 16",
        "content": "Production database is PostgreSQL 16 with pgbouncer for connection pooling. Migrations use ActiveRecord.",
        "observation_type": "decision_context",
        "count": 3,
        "expected": "Fail",
        "reason": "Infrastructure fact derivable from config files",
    },
    {
        "title": "User fixed a bug in the login page",
        "content": "The user identified and fixed a CSS z-index issue on the login modal that was causing it to render behind the header on mobile.",
        "observation_type": "correction",
        "count": 1,
        "expected": "Fail",
        "reason": "Ephemeral task detail — the fix is in git history",
    },
    {
        "title": "Tests should pass before merging",
        "content": "The user expects all tests to pass before a PR is merged. This is a standard quality gate.",
        "observation_type": "workflow_pattern",
        "count": 4,
        "expected": "Fail",
        "reason": "Generic engineering truth — not specific to this person",
    },
    {
        "title": "The staging environment is on Kubernetes",
        "content": "Staging runs on a Kubernetes cluster with Helm charts for deployment. The namespace is staging-apps.",
        "observation_type": "decision_context",
        "count": 2,
        "expected": "Fail",
        "reason": "Infrastructure fact from k8s manifests",
    },
    {
        "title": "Read the file and follow all the instructions in it",
        "content": "When dispatched as a minion agent, the first instruction is to read the instructions.md file in the .minions directory and follow all instructions.",
        "observation_type": "workflow_pattern",
        "count": 55,
        "expected": "Fail",
        "reason": "Dispatch stub — routing boilerplate, not behavioral signal",
    },
    {
        "title": "User uses VS Code as their editor",
        "content": "The user develops primarily in VS Code with various extensions for Ruby, TypeScript, and Python.",
        "observation_type": "preference_signal",
        "count": 3,
        "expected": "Fail",
        "reason": "Tool choice fact — no insight into how or why",
    },
    {
        "title": "Follows instructions embedded in arbitrary files without hesitation",
        "content": "When directed to read a file for instructions, the user treats it as an authoritative routing mechanism and executes whatever is specified immediately.",
        "observation_type": "collaboration_dynamic",
        "count": 3,
        "expected": "Fail",
        "reason": "Misattributes dispatch stub behavior as a personal pattern",
    },
    {
        "title": "User asked Claude to search for files",
        "content": "During the session, the user asked Claude to search for configuration files related to the deployment pipeline.",
        "observation_type": "workflow_pattern",
        "count": 1,
        "expected": "Fail",
        "reason": "Ephemeral task mechanic — no durable pattern",
    },
    {
        "title": "Application has a REST API",
        "content": "The app exposes a RESTful API with JSON responses, authenticated via OAuth2 bearer tokens.",
        "observation_type": "decision_context",
        "count": 2,
        "expected": "Fail",
        "reason": "Architecture fact derivable from code",
    },
]


# ---------------------------------------------------------------------------
# Build labeled dataset
# ---------------------------------------------------------------------------

def build_dataset() -> list[dict]:
    """Build the labeled dataset from gold set + garbage."""
    dataset = []

    # 1. Gold set correct observations → expected Pass
    gold = json.loads(GOLD_SET.read_text())
    correct_ids = set()
    for session in gold["sessions"]:
        for obs in session["correct"]:
            correct_ids.add(obs["id"])

    # Look up actual content from the DB
    conn = sqlite3.connect(str(EVAL_DB))
    for oid in correct_ids:
        row = conn.execute(
            "SELECT title, content, observation_type, count FROM observations WHERE id = ?",
            (oid,),
        ).fetchone()
        if row:
            dataset.append({
                "title": row[0],
                "content": row[1],
                "observation_type": row[2],
                "count": row[3],
                "expected": "Pass",
                "reason": f"Gold set correct extraction (obs #{oid})",
                "source": "gold_correct",
            })
    conn.close()

    # 2. Missing global patterns → synthesize as observations, expected Pass
    for category, patterns in gold.get("missing_global", {}).items():
        for pattern in patterns:
            obs_type = {
                "frustration_signals": "communication_style",
                "collaboration_patterns": "collaboration_dynamic",
                "observation_quality": "self_observation",
            }.get(category, "workflow_pattern")
            dataset.append({
                "title": pattern[:80],
                "content": pattern,
                "observation_type": obs_type,
                "count": 3,
                "expected": "Pass",
                "reason": f"Gold set missing_global ({category}) — system should capture this",
                "source": "gold_missing",
            })

    # 3. Garbage observations → expected Fail
    for g in GARBAGE_OBSERVATIONS:
        dataset.append({
            **g,
            "source": "fabricated_garbage",
        })

    return dataset


# ---------------------------------------------------------------------------
# Run validation
# ---------------------------------------------------------------------------

def validate(dataset: list[dict], dry_run: bool = False) -> dict:
    """Run the quality judge on each item and compute TPR/TNR."""
    results = []

    for i, item in enumerate(dataset):
        if dry_run:
            print(f"  [{i+1}/{len(dataset)}] [{item['expected']:4s}] {item['title'][:60]}")
            print(f"           {item['reason']}")
            continue

        t0 = time.time()
        verdict = judge_observation_quality(
            item["title"], item["content"],
            item["observation_type"], item["count"],
        )
        elapsed = time.time() - t0

        actual = verdict["result"]
        correct = actual == item["expected"]
        mark = "\u2713" if correct else "\u2717"

        results.append({
            "title": item["title"][:60],
            "expected": item["expected"],
            "actual": actual,
            "correct": correct,
            "source": item["source"],
            "reason": item["reason"],
            "critique": verdict.get("critique", ""),
            "time_s": round(elapsed, 1),
        })

        status = f"{mark} expected={item['expected']} got={actual}"
        print(f"  [{i+1}/{len(dataset)}] {status:30s} {item['title'][:50]} ({elapsed:.1f}s)",
              file=sys.stderr)

    if dry_run:
        print(f"\n  {len(dataset)} items ({sum(1 for d in dataset if d['expected'] == 'Pass')} Pass, "
              f"{sum(1 for d in dataset if d['expected'] == 'Fail')} Fail)")
        return {}

    # Compute confusion matrix
    tp = sum(1 for r in results if r["expected"] == "Pass" and r["actual"] == "Pass")
    fn = sum(1 for r in results if r["expected"] == "Pass" and r["actual"] == "Fail")
    tn = sum(1 for r in results if r["expected"] == "Fail" and r["actual"] == "Fail")
    fp = sum(1 for r in results if r["expected"] == "Fail" and r["actual"] == "Pass")

    tpr = tp / max(tp + fn, 1)
    tnr = tn / max(tn + fp, 1)
    accuracy = (tp + tn) / max(len(results), 1)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "dataset_size": len(results),
        "confusion_matrix": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "tpr": round(tpr, 3),
        "tnr": round(tnr, 3),
        "accuracy": round(accuracy, 3),
        "by_source": {},
        "details": results,
    }

    # Per-source breakdown
    for source in ("gold_correct", "gold_missing", "fabricated_garbage"):
        subset = [r for r in results if r["source"] == source]
        if subset:
            correct_n = sum(1 for r in subset if r["correct"])
            summary["by_source"][source] = {
                "total": len(subset),
                "correct": correct_n,
                "accuracy": round(correct_n / len(subset), 3),
            }

    return summary


def print_report(summary: dict):
    cm = summary["confusion_matrix"]
    print("\n" + "=" * 60)
    print("  OBSERVATION QUALITY JUDGE VALIDATION")
    print(f"  {summary['timestamp']}")
    print("=" * 60)

    print(f"\n  Dataset: {summary['dataset_size']} items")
    print(f"\n  Confusion Matrix:")
    print(f"                  Judge Pass  Judge Fail")
    print(f"    Human Pass      {cm['tp']:3d}         {cm['fn']:3d}")
    print(f"    Human Fail      {cm['fp']:3d}         {cm['tn']:3d}")

    print(f"\n  TPR (sensitivity): {summary['tpr']:.1%}  — catches real quality observations")
    print(f"  TNR (specificity): {summary['tnr']:.1%}  — rejects garbage")
    print(f"  Accuracy:          {summary['accuracy']:.1%}")

    print(f"\n  By source:")
    for source, stats in summary.get("by_source", {}).items():
        print(f"    {source:25s} {stats['correct']}/{stats['total']} ({stats['accuracy']:.0%})")

    # Show mismatches
    mismatches = [r for r in summary["details"] if not r["correct"]]
    if mismatches:
        print(f"\n  Mismatches ({len(mismatches)}):")
        for r in mismatches:
            print(f"    {'\u2717'} expected={r['expected']} got={r['actual']}: {r['title']}")
            print(f"      reason: {r['reason']}")
            print(f"      judge:  {r['critique'][:120]}")
    else:
        print("\n  No mismatches — perfect alignment!")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv

    if not EVAL_DB.exists():
        print(f"DB not found: {EVAL_DB}", file=sys.stderr)
        sys.exit(1)
    if not GOLD_SET.exists():
        print(f"Gold set not found: {GOLD_SET}", file=sys.stderr)
        sys.exit(1)

    dataset = build_dataset()
    print(f"Built dataset: {len(dataset)} items "
          f"({sum(1 for d in dataset if d['expected'] == 'Pass')} Pass, "
          f"{sum(1 for d in dataset if d['expected'] == 'Fail')} Fail)",
          file=sys.stderr)

    summary = validate(dataset, dry_run=dry_run)

    if dry_run:
        return

    print_report(summary)

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"judge-validation-{ts}.json"
    with open(report_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    main()
