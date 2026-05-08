#!/usr/bin/env python3
"""
Compute baseline metrics from observability JSONL logs.

Usage:
    python scripts/compute_baseline.py --metric precision_at_k --k 5
    python scripts/compute_baseline.py --metric acceptance_rate
    python scripts/compute_baseline.py --metric kappa --field kind
    python scripts/compute_baseline.py --metric shadow_prune_summary

Outputs:
    - Human-readable summary to stdout
    - Machine-readable JSON to backfill-output/observability/baseline-{metric}-{ts}.json

Logs read from backfill-output/observability/ (relative to repo root, or
MEMESIS_REPO_ROOT env var).
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(os.environ.get("MEMESIS_REPO_ROOT", Path(__file__).parent.parent))
_OBS_DIR = _REPO_ROOT / "backfill-output" / "observability"


def _load_jsonl(path: Path) -> list[dict]:
    """Load all records from a JSONL file. Returns [] if file missing or empty."""
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _write_output(metric: str, payload: dict) -> Path:
    """Write machine-readable JSON to the observability dir."""
    _OBS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OBS_DIR / f"baseline-{metric}-{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


# ---------------------------------------------------------------------------
# precision@k
# ---------------------------------------------------------------------------


def compute_precision_at_k(k: int) -> dict:
    """
    Compute precision@k from retrieval-trace and acceptance-trace logs.

    precision@k = fraction of top-k returned memories that were accepted
    (i.e., appeared in accepted_ids of the matching acceptance record).

    Returns a dict with summary stats.
    """
    retrievals = _load_jsonl(_OBS_DIR / "retrieval-trace.jsonl")
    acceptances = _load_jsonl(_OBS_DIR / "acceptance-trace.jsonl")

    if not retrievals:
        return {"status": "no_data", "message": "No retrieval-trace.jsonl records yet."}

    # Build lookup: retrieval_id → set of accepted_ids
    accepted_map: dict[str, set[str]] = {}
    for rec in acceptances:
        rid = rec.get("retrieval_id")
        if rid:
            accepted_map[rid] = set(rec.get("accepted_ids", []))

    precisions = []
    no_acceptance_signal = 0
    for ret in retrievals:
        rid = ret.get("retrieval_id", "")
        top_k = (ret.get("returned_ids") or [])[:k]
        if not top_k:
            continue
        if rid not in accepted_map:
            no_acceptance_signal += 1
            continue
        accepted = accepted_map[rid]
        hits = sum(1 for mid in top_k if mid in accepted)
        precisions.append(hits / len(top_k))

    if not precisions:
        return {
            "status": "no_data",
            "message": (
                f"No retrieval events with acceptance signals yet "
                f"({no_acceptance_signal} retrievals missing acceptance records). "
                "Wire log_acceptance() at downstream consolidator/feedback call sites."
            ),
            "retrieval_count": len(retrievals),
            "no_acceptance_signal": no_acceptance_signal,
        }

    mean_p = sum(precisions) / len(precisions)
    return {
        "status": "ok",
        "metric": f"precision@{k}",
        "k": k,
        "n_queries": len(precisions),
        "mean_precision": round(mean_p, 4),
        "min": round(min(precisions), 4),
        "max": round(max(precisions), 4),
        "no_acceptance_signal": no_acceptance_signal,
    }


# ---------------------------------------------------------------------------
# acceptance_rate
# ---------------------------------------------------------------------------


def compute_acceptance_rate() -> dict:
    """
    Compute overall acceptance rate from acceptance-trace logs.

    acceptance_rate = accepted_count / (accepted_count + rejected_count)
    across all logged acceptance events.
    """
    records = _load_jsonl(_OBS_DIR / "acceptance-trace.jsonl")
    if not records:
        return {"status": "no_data", "message": "No acceptance-trace.jsonl records yet."}

    total_accepted = sum(r.get("accepted_count", 0) for r in records)
    total_rejected = sum(r.get("rejected_count", 0) for r in records)
    total = total_accepted + total_rejected

    if total == 0:
        return {"status": "no_data", "message": "All acceptance records have zero counts."}

    rate = total_accepted / total
    return {
        "status": "ok",
        "metric": "acceptance_rate",
        "n_events": len(records),
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "acceptance_rate": round(rate, 4),
    }


# ---------------------------------------------------------------------------
# fleiss-kappa (multi-run inter-annotator agreement)
# ---------------------------------------------------------------------------


def _fleiss_kappa(data: list[list[int]]) -> float:
    """
    Compute Fleiss' kappa for a matrix of ratings.

    data: list of rows, each row is a list of counts per category
          (how many raters assigned each category to this item).
    Returns kappa in [-1, 1]. Requires >= 2 raters and >= 2 items.
    """
    n = len(data)
    if n < 2:
        return float("nan")
    k = len(data[0])
    N = sum(sum(row) for row in data)
    raters_per_item = sum(data[0])
    if raters_per_item < 2:
        return float("nan")

    p_j = [sum(row[j] for row in data) / N for j in range(k)]
    P_i = [
        (sum(c * c for c in row) - raters_per_item)
        / (raters_per_item * (raters_per_item - 1))
        for row in data
    ]
    P_bar = sum(P_i) / n
    P_e_bar = sum(pj * pj for pj in p_j)

    if abs(1.0 - P_e_bar) < 1e-12:
        return float("nan")
    return (P_bar - P_e_bar) / (1.0 - P_e_bar)


def compute_kappa(field: str) -> dict:
    """
    Compute Fleiss' kappa across multi-run consolidation decisions.

    Groups decisions by observation_id and computes inter-run agreement on
    the specified field (typically 'kind' or 'knowledge_type').

    Requires multiple log records for the same observation_id (produced by
    running consolidation multiple times, e.g. kappa calibration runs).
    """
    records = _load_jsonl(_OBS_DIR / "consolidation-decisions.jsonl")
    if not records:
        return {"status": "no_data", "message": "No consolidation-decisions.jsonl records yet."}

    # Group by observation_id
    by_obs: dict[str, list[str]] = defaultdict(list)
    for r in records:
        oid = r.get("observation_id", "")
        val = r.get(field)
        if oid and val is not None:
            by_obs[oid].append(str(val))

    # Only consider observations with multiple ratings
    multi = {oid: vals for oid, vals in by_obs.items() if len(vals) >= 2}
    if not multi:
        return {
            "status": "no_data",
            "message": (
                f"No observations with multiple '{field}' ratings found. "
                "Run consolidation multiple times on the same observations to generate kappa data."
            ),
            "total_observations": len(by_obs),
        }

    # Build category index
    all_cats = sorted(set(v for vals in multi.values() for v in vals))
    cat_idx = {c: i for i, c in enumerate(all_cats)}
    k = len(all_cats)

    matrix = []
    for vals in multi.values():
        row = [0] * k
        for v in vals:
            row[cat_idx[v]] += 1
        matrix.append(row)

    kappa = _fleiss_kappa(matrix)
    return {
        "status": "ok",
        "metric": "kappa",
        "field": field,
        "n_observations": len(multi),
        "categories": all_cats,
        "kappa": round(kappa, 4) if not math.isnan(kappa) else None,
        "interpretation": (
            "≥0.7 reliable; 0.4–0.7 moderate; <0.4 unreliable"
        ),
    }


# ---------------------------------------------------------------------------
# shadow_prune_summary
# ---------------------------------------------------------------------------


def compute_shadow_prune_summary() -> dict:
    """
    Summarize shadow-prune log: survival rates by tier and kind.

    Reports what fraction of memories WOULD survive vs. be pruned
    under the §9 activation threshold, broken down by salience tier.
    """
    records = _load_jsonl(_OBS_DIR / "shadow-prune.jsonl")
    if not records:
        return {"status": "no_data", "message": "No shadow-prune.jsonl records yet."}

    by_tier: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "would_prune": 0})
    for r in records:
        tier = r.get("tier", "unknown")
        by_tier[tier]["total"] += 1
        if r.get("would_prune"):
            by_tier[tier]["would_prune"] += 1

    total = len(records)
    total_pruned = sum(v["would_prune"] for v in by_tier.values())

    tier_summary = {}
    for tier, counts in sorted(by_tier.items()):
        t = counts["total"]
        p = counts["would_prune"]
        tier_summary[tier] = {
            "total": t,
            "would_prune": p,
            "survival_rate": round((t - p) / t, 4) if t > 0 else None,
            "prune_rate": round(p / t, 4) if t > 0 else None,
        }

    return {
        "status": "ok",
        "metric": "shadow_prune_summary",
        "total_evaluated": total,
        "total_would_prune": total_pruned,
        "overall_survival_rate": round((total - total_pruned) / total, 4) if total > 0 else None,
        "by_tier": tier_summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute baseline metrics from observability JSONL logs."
    )
    parser.add_argument(
        "--metric",
        required=True,
        choices=["precision_at_k", "acceptance_rate", "kappa", "shadow_prune_summary"],
        help="Metric to compute.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="k for precision@k (default: 5).",
    )
    parser.add_argument(
        "--field",
        default="kind",
        help="Field for kappa computation (default: kind).",
    )
    args = parser.parse_args(argv)

    if args.metric == "precision_at_k":
        result = compute_precision_at_k(args.k)
    elif args.metric == "acceptance_rate":
        result = compute_acceptance_rate()
    elif args.metric == "kappa":
        result = compute_kappa(args.field)
    elif args.metric == "shadow_prune_summary":
        result = compute_shadow_prune_summary()
    else:
        print(f"Unknown metric: {args.metric}", file=sys.stderr)
        return 1

    # Human-readable output
    status = result.get("status", "unknown")
    if status == "no_data":
        print(f"\n[{args.metric}] No data yet.")
        print(f"  {result.get('message', '')}")
    else:
        print(f"\n[{args.metric}] Results:")
        for k, v in result.items():
            if k not in ("status", "metric"):
                print(f"  {k}: {v}")

    # Machine-readable JSON output
    out_path = _write_output(args.metric, result)
    print(f"\nJSON written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
