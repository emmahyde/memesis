"""
eval/injection_correlation_test.py — RISK-09 correlation analysis.

Measures the Pearson (and Spearman) correlation between a memory's
injection_count and a confirmed-utility signal ("was this memory
promoted?"), using data from the live DB.

Metric choice
-------------
We use ConsolidationLog.action == "promoted" as the "useful" signal.
This is the closest available proxy to "kept_after_consolidation": a
memory that was promoted survived curation and was judged worthy of a
higher stage. The preferred field `kept_after_consolidation` does not
exist as a queryable column, so `promoted` from ConsolidationLog is the
correct fallback.

Re-coupling threshold
---------------------
If the analysis finds:
    Pearson r > 0.3 AND p-value < 0.05 AND N >= 50 memories

then injection_count is a meaningful predictor of utility and the two
signals may warrant re-coupling. Below r=0.3 or with N<50 the
correlation is too weak or too sparse to justify a feedback loop.

Usage
-----
    python -m pytest eval/injection_correlation_test.py -v
    # or run as a standalone script:
    python eval/injection_correlation_test.py

Both paths require an initialized DB (i.e., run after at least one
consolidation cycle). If fewer than 10 memory rows exist the test is
skipped rather than misleadingly reported as passing.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Allow running as a standalone script from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Pure-Python Pearson and Spearman implementations
# ---------------------------------------------------------------------------


def _pearson(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """
    Compute Pearson r and a two-tailed p-value approximation.

    Returns (r, p_value). Falls back to (0.0, 1.0) if xs or ys are constant.
    Uses a t-distribution approximation for the p-value.
    """
    n = len(xs)
    if n < 3:
        return 0.0, 1.0

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))

    if std_x < 1e-12 or std_y < 1e-12:
        # One variable is constant — correlation is undefined / zero.
        return 0.0, 1.0

    r = cov / (std_x * std_y)
    r = max(-1.0, min(1.0, r))  # clamp floating-point drift

    # t-distribution approximation: t = r * sqrt(n-2) / sqrt(1-r^2)
    if abs(r) >= 1.0:
        return r, 0.0
    t_stat = r * math.sqrt(n - 2) / math.sqrt(1 - r ** 2)

    # Two-tailed p from regularized incomplete beta approximation (rough).
    # For eval purposes we use a simple threshold; exact p is provided for
    # the report log.
    p_approx = _t_pvalue(abs(t_stat), df=n - 2)
    return r, p_approx


def _t_pvalue(t: float, df: int) -> float:
    """
    Rough two-tailed p-value from a t-statistic via a normal approximation.

    For df >= 30 the t-distribution is approximately normal. For smaller df
    the approximation is conservative (over-estimates p), which is acceptable
    for an eval threshold check rather than a publication-grade test.
    """
    if df < 1:
        return 1.0
    # Use erfc for the normal approximation (works well for df >= 30).
    # For df < 30 this underestimates significance — safe for our use case
    # (we want to avoid false "re-couple" signals).
    z = t / math.sqrt(1 + t ** 2 / df)
    p_one_tail = 0.5 * math.erfc(z / math.sqrt(2))
    return 2 * p_one_tail


def _rank(values: list[float]) -> list[float]:
    """Return ranks (1-based, average ties) for a list of values."""
    indexed = sorted(enumerate(values), key=lambda iv: iv[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-based average rank for ties
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Spearman rank correlation and approximate p-value."""
    return _pearson(_rank(xs), _rank(ys))


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


def _load_memory_data() -> list[dict]:
    """
    Load (injection_count, promoted) pairs from the live DB.

    promoted=1 if ConsolidationLog has at least one action="promoted" row
    for this memory_id; 0 otherwise.
    """
    from core.models import ConsolidationLog, Memory

    promoted_ids: set[str] = {
        row.memory_id
        for row in ConsolidationLog.select(ConsolidationLog.memory_id).where(
            ConsolidationLog.action == "promoted",
            ConsolidationLog.memory_id.is_null(False),
        )
    }

    rows = []
    for mem in Memory.select(Memory.id, Memory.injection_count):
        rows.append({
            "memory_id": mem.id,
            "injection_count": mem.injection_count or 0,
            "promoted": 1 if mem.id in promoted_ids else 0,
        })
    return rows


# ---------------------------------------------------------------------------
# Pytest test
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_injection_count_promotion_correlation(memory_store):
    """
    Compute Pearson and Spearman correlation between injection_count and
    the "promoted" utility signal.

    The test passes regardless of the correlation magnitude — its job is to
    MEASURE and report. Assertions only guard:
      - Sufficient data for the analysis to be meaningful (>= 10 rows).
      - Correlation is in [-1, 1] (sanity check on the math).

    See module docstring for the re-coupling threshold.
    """
    from core.database import init_db
    init_db(base_dir=str(memory_store))

    data = _load_memory_data()

    if len(data) < 10:
        pytest.skip(
            f"Insufficient data for correlation analysis: {len(data)} memories "
            "(need >= 10). Run after at least one consolidation cycle."
        )

    xs = [float(d["injection_count"]) for d in data]
    ys = [float(d["promoted"]) for d in data]

    pearson_r, pearson_p = _pearson(xs, ys)
    spearman_r, spearman_p = _spearman(xs, ys)

    n = len(data)
    promoted_count = sum(d["promoted"] for d in data)

    print(f"\n--- RISK-09 Injection Count / Promotion Correlation ---")
    print(f"  N memories:       {n}")
    print(f"  Promoted count:   {promoted_count} ({100 * promoted_count / n:.1f}%)")
    print(f"  Pearson r:        {pearson_r:.4f}  (p≈{pearson_p:.4f})")
    print(f"  Spearman r:       {spearman_r:.4f}  (p≈{spearman_p:.4f})")
    print(f"  Re-couple signal: {'YES — see module docstring' if pearson_r > 0.3 and pearson_p < 0.05 and n >= 50 else 'no'}")
    print(f"------------------------------------------------------")

    # Sanity: correlation is bounded
    assert -1.0 <= pearson_r <= 1.0
    assert -1.0 <= spearman_r <= 1.0


# ---------------------------------------------------------------------------
# Standalone script entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the correlation analysis directly (no pytest required)."""
    from core.database import init_db

    init_db()  # uses default ~/.claude/memory path

    data = _load_memory_data()
    n = len(data)

    if n < 10:
        print(f"Insufficient data: {n} memories (need >= 10).")
        return

    xs = [float(d["injection_count"]) for d in data]
    ys = [float(d["promoted"]) for d in data]

    pearson_r, pearson_p = _pearson(xs, ys)
    spearman_r, spearman_p = _spearman(xs, ys)
    promoted_count = sum(d["promoted"] for d in data)

    print(f"\n--- RISK-09 Injection Count / Promotion Correlation ---")
    print(f"  N memories:       {n}")
    print(f"  Promoted count:   {promoted_count} ({100 * promoted_count / n:.1f}%)")
    print(f"  Pearson r:        {pearson_r:.4f}  (p≈{pearson_p:.4f})")
    print(f"  Spearman r:       {spearman_r:.4f}  (p≈{spearman_p:.4f})")
    re_couple = pearson_r > 0.3 and pearson_p < 0.05 and n >= 50
    print(f"  Re-couple signal: {'YES — consider re-coupling injection_count into scoring' if re_couple else 'no'}")
    print(f"------------------------------------------------------")


if __name__ == "__main__":
    main()
