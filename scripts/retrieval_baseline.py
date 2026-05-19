"""
Retrieval baseline measurement script (panel C4).

Prints a baseline report of retrieval / injection statistics to stdout.
Run before any schema changes that affect retrieval filtering.

Usage:
    uv run python scripts/retrieval_baseline.py

Output sections:
  1. Injection distribution by memory stage and kind
  2. Stage 2 vs Stage 1 importance delta (raw_importance vs importance)
  3. kind + knowledge_type co-occurrence matrix (C5 orthogonality check)
  4. Memories never injected despite high importance (retrieval gaps)
"""

import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.database import init_db
from core.models import Memory

init_db(project_context=os.getcwd())


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "n/a"


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Injection distribution
# ---------------------------------------------------------------------------
section("1. Injection distribution by stage and kind")

memories = list(Memory.select().where(Memory.archived_at.is_null()))
total = len(memories)
print(f"Total active memories: {total}")

stage_counts: Counter = Counter(m.stage for m in memories)
for stage, count in sorted(stage_counts.items()):
    print(f"  {stage:<20} {count:>4}  ({_pct(count, total)})")

kind_counts: Counter = Counter(m.kind or "null" for m in memories)
print(f"\nBy kind:")
for kind, count in kind_counts.most_common():
    print(f"  {kind:<25} {count:>4}  ({_pct(count, total)})")

injected = [m for m in memories if (m.injection_count or 0) > 0]
print(f"\nInjected at least once: {len(injected)} / {total}  ({_pct(len(injected), total)})")

# ---------------------------------------------------------------------------
# 2. Stage 2 vs Stage 1 importance delta
# ---------------------------------------------------------------------------
section("2. Importance delta: Stage 2 (importance) vs Stage 1 (raw_importance)")

with_raw = [m for m in memories if m.raw_importance is not None]
print(f"Memories with raw_importance recorded: {len(with_raw)} / {total}")

if with_raw:
    deltas = [m.importance - m.raw_importance for m in with_raw if m.importance is not None]
    if deltas:
        avg_delta = sum(deltas) / len(deltas)
        upscored = sum(1 for d in deltas if d > 0.05)
        downscored = sum(1 for d in deltas if d < -0.05)
        neutral = len(deltas) - upscored - downscored
        print(f"  Mean Stage2 - Stage1 delta: {avg_delta:+.3f}")
        print(f"  Up-scored (>0.05):          {upscored}  ({_pct(upscored, len(deltas))})")
        print(f"  Down-scored (<-0.05):        {downscored}  ({_pct(downscored, len(deltas))})")
        print(f"  Neutral (±0.05):             {neutral}  ({_pct(neutral, len(deltas))})")
        s2_median = sorted(m.importance for m in with_raw if m.importance is not None)
        mid = len(s2_median) // 2
        print(f"  Median Stage 2 importance:  {s2_median[mid]:.3f}  (target < 0.65)")

# ---------------------------------------------------------------------------
# 3. kind × knowledge_type co-occurrence matrix
# ---------------------------------------------------------------------------
section("3. kind × knowledge_type co-occurrence (C5 orthogonality check)")

matrix: dict = defaultdict(Counter)
for m in memories:
    k = m.kind or "null"
    kt = m.knowledge_type or "null"
    matrix[k][kt] += 1

all_kts = sorted({kt for row in matrix.values() for kt in row})
header = f"{'kind':<22}" + "".join(f"{kt:<15}" for kt in all_kts)
print(header)
print("-" * len(header))
for kind in sorted(matrix):
    row_str = f"{kind:<22}" + "".join(f"{matrix[kind].get(kt, 0):<15}" for kt in all_kts)
    print(row_str)

# ---------------------------------------------------------------------------
# 4. High-importance memories never injected
# ---------------------------------------------------------------------------
section("4. High-importance memories never injected (retrieval gaps)")

gaps = [
    m for m in memories
    if (m.importance or 0) >= 0.7 and (m.injection_count or 0) == 0
]
print(f"Memories with importance >= 0.7 and zero injections: {len(gaps)}")
for m in sorted(gaps, key=lambda x: -(x.importance or 0))[:10]:
    print(f"  [{m.stage:<12}] imp={m.importance:.2f}  {(m.title or '')[:60]}")

print()
