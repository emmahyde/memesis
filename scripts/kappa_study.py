#!/usr/bin/env python3
"""Inter-rater agreement study for Memory.knowledge_type.

Per Sim & Wright 2005, Fleiss-kappa wants N >= 30 items for stable
estimates; the script refuses smaller corpora unless --force is set.

Workflow:
  1. Sample N memories with non-null content from the index DB
  2. Run K independent LLM raters (different models or different prompts)
  3. Compute Cohen's kappa (K=2) or Fleiss-kappa (K>=3)
  4. Gate decision: kappa >= 0.6 -> safe to use as retrieval filter

Usage:
    uv run python3 scripts/kappa_study.py --n 50
    uv run python3 scripts/kappa_study.py --n 60 \
        --raters claude-sonnet-4-6,claude-haiku-4-5-20251001 \
        --out backfill-output/kappa/run-2026-05-15.json

Notes:
- All LLM calls go through core.llm.call_llm (per CLAUDE.md rule).
- Reads memories via Peewee Memory.select() — no raw sqlite3 connections.
- Run nothing if corpus too small; report and exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.llm import call_llm
from core.models import Memory
from core.validators import KNOWLEDGE_TYPE_VALUES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kappa] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

MIN_N_FOR_STABLE_KAPPA = 30  # Sim & Wright 2005
KAPPA_GATE = 0.6             # Landis & Koch 1977 "substantial agreement"
DEFAULT_RATERS = "claude-sonnet-4-6,claude-haiku-4-5-20251001"

CLASSIFY_PROMPT = """\
Classify the following memory's knowledge_type as exactly ONE of:
- factual: a discrete fact about a specific entity, version, value, or state
- conceptual: a relationship, principle, or model that organizes facts
- procedural: how to do something — a method, sequence, or rule of action
- metacognitive: knowledge about cognition itself (own behavior, preferences,
  workflow patterns, self-corrections)

Respond with ONLY a JSON object: {"knowledge_type": "<value>"}
No prose, no explanation, no fences.

MEMORY:
title: %(title)s
subtitle: %(subtitle)s
content: %(content)s
"""


def _sample_memories(n: int, seed: int) -> list[Memory]:
    pool = list(
        Memory.select()
        .where(Memory.archived_at.is_null(), Memory.content.is_null(False))
    )
    if len(pool) < n:
        return pool
    rng = random.Random(seed)
    return rng.sample(pool, n)


def _classify(model: str, mem: Memory) -> Optional[str]:
    prompt = CLASSIFY_PROMPT % {
        "title": (mem.title or "")[:200],
        "subtitle": (mem.subtitle or "")[:200],
        "content": (mem.content or "")[:1500],
    }
    raw = call_llm(prompt, model=model, max_tokens=64, temperature=0)
    if raw.startswith("[ERROR]"):
        logger.warning("rater %s failed for memory %s: %s", model, mem.id, raw[:120])
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("rater %s returned non-JSON for %s: %s", model, mem.id, raw[:120])
        return None
    kt = parsed.get("knowledge_type")
    if kt not in KNOWLEDGE_TYPE_VALUES:
        logger.warning("rater %s returned invalid kt %r for %s", model, kt, mem.id)
        return None
    return kt


def _cohen_kappa(rater_a: list[str], rater_b: list[str]) -> float:
    from sklearn.metrics import cohen_kappa_score
    return float(cohen_kappa_score(rater_a, rater_b))


def _fleiss_kappa(matrix: list[list[int]]) -> float:
    """Fleiss-kappa for K raters across N items and C categories.

    matrix[i][c] = number of raters that assigned item i to category c.
    Sum over each row equals K (constant). See Fleiss 1971.
    """
    if not matrix:
        return 0.0
    n_items = len(matrix)
    k_raters = sum(matrix[0])
    if k_raters < 2:
        return 0.0
    n_cats = len(matrix[0])

    # P_i: agreement on item i
    p_i = [
        (sum(c * c for c in row) - k_raters) / (k_raters * (k_raters - 1))
        for row in matrix
    ]
    p_bar = sum(p_i) / n_items

    # P_e: expected agreement under chance
    cat_totals = [sum(matrix[i][c] for i in range(n_items)) for c in range(n_cats)]
    total_assignments = n_items * k_raters
    p_e = sum((t / total_assignments) ** 2 for t in cat_totals)

    if p_e >= 1.0:
        return 1.0
    return (p_bar - p_e) / (1.0 - p_e)


def _build_fleiss_matrix(rater_labels: list[list[str]]) -> list[list[int]]:
    """Convert per-rater label lists into a Fleiss count matrix."""
    cats = sorted(KNOWLEDGE_TYPE_VALUES)
    cat_idx = {c: i for i, c in enumerate(cats)}
    n_items = len(rater_labels[0])
    matrix = [[0] * len(cats) for _ in range(n_items)]
    for rater in rater_labels:
        for i, label in enumerate(rater):
            if label in cat_idx:
                matrix[i][cat_idx[label]] += 1
    return matrix


def _kappa_label(kappa: float) -> str:
    """Landis & Koch 1977 buckets."""
    if kappa < 0:
        return "poor (worse than chance)"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost perfect"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=50, help="Sample size")
    p.add_argument(
        "--raters",
        default=DEFAULT_RATERS,
        help="Comma-separated model IDs (>=2)",
    )
    p.add_argument(
        "--base-dir",
        default=os.path.expanduser(
            "~/.claude/projects/-Users-emmahyde-projects-memesis/memory"
        ),
        help="Memesis base_dir for init_db",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=None, help="JSON report path")
    p.add_argument(
        "--force",
        action="store_true",
        help=f"Run even when N < {MIN_N_FOR_STABLE_KAPPA}",
    )
    args = p.parse_args()

    raters = [r.strip() for r in args.raters.split(",") if r.strip()]
    if len(raters) < 2:
        sys.exit("need at least 2 raters")

    init_db(base_dir=args.base_dir)
    sample = _sample_memories(args.n, args.seed)
    n_actual = len(sample)
    logger.info("sampled %d memories (requested %d)", n_actual, args.n)

    if n_actual < MIN_N_FOR_STABLE_KAPPA and not args.force:
        close_db()
        sys.exit(
            f"corpus too small for stable kappa (n={n_actual}, "
            f"need >={MIN_N_FOR_STABLE_KAPPA}); use --force to override"
        )

    rater_labels: list[list[str]] = []
    for rater in raters:
        logger.info("rater %s classifying %d items", rater, n_actual)
        labels: list[str] = []
        for mem in sample:
            label = _classify(rater, mem)
            labels.append(label or "__MISS__")
        rater_labels.append(labels)
        miss_count = sum(1 for x in labels if x == "__MISS__")
        if miss_count:
            logger.warning("rater %s missed %d/%d items", rater, miss_count, n_actual)

    close_db()

    # Filter items where any rater missed (kappa undefined on missing labels)
    keep_idx = [
        i for i in range(n_actual)
        if all(rater_labels[r][i] != "__MISS__" for r in range(len(raters)))
    ]
    logger.info(
        "keeping %d/%d items where all %d raters returned valid labels",
        len(keep_idx), n_actual, len(raters),
    )
    rater_labels = [[rater_labels[r][i] for i in keep_idx] for r in range(len(raters))]
    n_clean = len(keep_idx)

    if n_clean < 2:
        sys.exit("not enough valid-label items to compute kappa")

    if len(raters) == 2:
        kappa = _cohen_kappa(rater_labels[0], rater_labels[1])
        kappa_kind = "cohen"
    else:
        matrix = _build_fleiss_matrix(rater_labels)
        kappa = _fleiss_kappa(matrix)
        kappa_kind = "fleiss"

    # Per-class distribution per rater
    per_rater_dist = {
        raters[r]: dict(Counter(rater_labels[r])) for r in range(len(raters))
    }

    report = {
        "n_sampled": n_actual,
        "n_clean": n_clean,
        "raters": raters,
        "kappa_kind": kappa_kind,
        "kappa": kappa,
        "kappa_label": _kappa_label(kappa),
        "gate_threshold": KAPPA_GATE,
        "gate_pass": kappa >= KAPPA_GATE,
        "per_rater_distribution": per_rater_dist,
        "min_n_threshold": MIN_N_FOR_STABLE_KAPPA,
    }

    print(json.dumps(report, indent=2, default=str))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=str))
        logger.info("report written to %s", args.out)

    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
