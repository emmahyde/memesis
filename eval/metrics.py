"""
Evaluation metrics for the memesis memory lifecycle.

Four metric functions + MetricsResult dataclass. No external dependencies.
Importable without any core.* modules present.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MetricsResult:
    """Aggregate metrics snapshot for a single evaluation run."""

    precision_at_1: float = 0.0
    precision_at_5: float = 0.0
    precision_at_10: float = 0.0
    mrr: float = 0.0
    prune_precision: float = 0.0
    prune_recall: float = 0.0
    prune_f1: float = 0.0
    injection_utility_rate: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k retrieved items that are relevant.

    Args:
        retrieved: Ordered list of retrieved item IDs.
        relevant:  Set of ground-truth relevant item IDs.
        k:         Cutoff rank.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 when retrieved is empty or k == 0.
    """
    if not retrieved or k == 0:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / k


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    """Mean Reciprocal Rank — reciprocal rank of the first relevant item.

    Args:
        retrieved: Ordered list of retrieved item IDs.
        relevant:  Set of ground-truth relevant item IDs.

    Returns:
        1/rank of the first relevant hit, or 0.0 if none found.
    """
    for i, item in enumerate(retrieved):
        if item in relevant:
            return 1.0 / (i + 1)
    return 0.0


def prune_accuracy(
    kept: list[str],
    true_keep: set[str],
    pruned: list[str],
    true_prune: set[str],
) -> dict[str, float]:
    """Precision, recall, and F1 for a pruning decision.

    Measures how well the pruner decided what to keep:
      - precision = |kept ∩ true_keep| / |kept|  (kept items that should be kept)
      - recall    = |kept ∩ true_keep| / |true_keep|  (true_keep items that were kept)
      - f1        = harmonic mean of precision and recall

    Args:
        kept:       Items the pruner decided to keep.
        true_keep:  Ground-truth items that should be kept.
        pruned:     Items the pruner decided to prune.
        true_prune: Ground-truth items that should be pruned.

    Returns:
        Dict with keys "precision", "recall", "f1", each a float in [0.0, 1.0].
        Returns 0.0 for any undefined term (division by zero).
    """
    kept_set = set(kept)
    true_positives = len(kept_set & true_keep)

    precision = true_positives / len(kept_set) if kept_set else 0.0
    recall = true_positives / len(true_keep) if true_keep else 0.0

    if precision + recall == 0.0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    return {"precision": precision, "recall": recall, "f1": f1}


def injection_utility_rate(injected_ids: list[str], used_ids: set[str]) -> float:
    """Fraction of injected memories that were actually used.

    Args:
        injected_ids: List of memory IDs that were injected into context.
        used_ids:     Set of memory IDs that the model referenced/used.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 when injected_ids is empty.
    """
    if not injected_ids:
        return 0.0
    return len(set(injected_ids) & used_ids) / len(injected_ids)
