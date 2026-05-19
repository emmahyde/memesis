#!/usr/bin/env python3
"""Audit the importance distribution of stored memories.

Histograms `importance` across all non-archived memories and flags the
collapsed-distribution problem from the 2026-05-15 canvas review §3 — too many
scores clustered in the 0.45–0.65 mid-band carry no ranking signal.

Usage:
    uv run python scripts/importance_histogram.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import close_db, init_db  # noqa: E402
from core.importance import COLLAPSE_BAND, distribution_is_collapsed  # noqa: E402
from core.models import Memory  # noqa: E402


def main() -> int:
    init_db()
    try:
        scores = [
            m.importance
            for m in Memory.select(Memory.importance).where(Memory.archived_at.is_null())
            if m.importance is not None
        ]
    finally:
        close_db()

    if not scores:
        print("No memories with an importance score.")
        return 0

    # Ten 0.1-wide buckets.
    buckets = [0] * 10
    for s in scores:
        idx = min(int(s * 10), 9)
        buckets[idx] += 1

    peak = max(buckets) or 1
    print(f"Importance distribution — {len(scores)} memories\n")
    for i, count in enumerate(buckets):
        lo, hi = i / 10, (i + 1) / 10
        bar = "#" * round(40 * count / peak)
        print(f"  {lo:.1f}–{hi:.1f} | {bar:<40} {count}")

    collapsed, fraction = distribution_is_collapsed(scores)
    lo, hi = COLLAPSE_BAND
    print(f"\n  {fraction:.0%} of scores fall in the {lo:.2f}–{hi:.2f} mid-band.")
    if collapsed:
        print("  COLLAPSED — distribution lacks ranking variance; recalibration needed.")
        return 1
    print("  OK — distribution carries ranking signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
