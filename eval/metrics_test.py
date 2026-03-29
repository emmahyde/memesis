"""
Unit tests for eval/metrics.py — hand-computed fixtures, no network or LLM calls.
"""

import pytest
from eval.metrics import (
    MetricsResult,
    precision_at_k,
    mrr,
    prune_accuracy,
    injection_utility_rate,
)


# ---------------------------------------------------------------------------
# precision_at_k
# ---------------------------------------------------------------------------

class TestPrecisionAtK:
    def test_basic_half(self):
        # top-2: ["a","b"], relevant={"a","c"} → 1 hit / 2 = 0.5
        assert precision_at_k(["a", "b", "c"], {"a", "c"}, 2) == 0.5

    def test_all_relevant(self):
        assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0

    def test_none_relevant(self):
        assert precision_at_k(["x", "y", "z"], {"a", "b"}, 3) == 0.0

    def test_empty_retrieved(self):
        assert precision_at_k([], {"a"}, 3) == 0.0

    def test_k_zero(self):
        assert precision_at_k(["a", "b"], {"a"}, 0) == 0.0

    def test_k_larger_than_retrieved(self):
        # k=5 but only 2 items → denominator should be k=5, not len(retrieved)
        assert precision_at_k(["a", "b"], {"a"}, 5) == pytest.approx(1.0 / 5)

    def test_k_one_hit(self):
        assert precision_at_k(["a", "x", "y"], {"a"}, 1) == 1.0

    def test_k_one_miss(self):
        assert precision_at_k(["x", "a", "y"], {"a"}, 1) == 0.0


# ---------------------------------------------------------------------------
# mrr
# ---------------------------------------------------------------------------

class TestMRR:
    def test_first_relevant_at_position_2(self):
        # "a" is at index 1 → rank 2 → 1/2 = 0.5
        assert mrr(["x", "a", "b"], {"a"}) == 0.5

    def test_first_relevant_at_position_1(self):
        assert mrr(["a", "b", "c"], {"a"}) == 1.0

    def test_no_relevant(self):
        assert mrr(["x", "y"], {"a"}) == 0.0

    def test_empty_retrieved(self):
        assert mrr([], {"a"}) == 0.0

    def test_multiple_relevant_first_wins(self):
        # "a" at index 0 is the first relevant → 1.0
        assert mrr(["a", "b"], {"a", "b"}) == 1.0

    def test_relevant_at_position_3(self):
        assert mrr(["x", "y", "a"], {"a"}) == pytest.approx(1.0 / 3)


# ---------------------------------------------------------------------------
# prune_accuracy
# ---------------------------------------------------------------------------

class TestPruneAccuracy:
    def test_perfect_pruning(self):
        result = prune_accuracy(["a", "b"], {"a", "b"}, ["c", "d"], {"c", "d"})
        assert result == {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    def test_partial_precision(self):
        # kept=["a","b","e"], true_keep={"a","b"} → precision=2/3, recall=1.0
        result = prune_accuracy(["a", "b", "e"], {"a", "b"}, ["c"], {"c"})
        assert result["precision"] == pytest.approx(2.0 / 3)
        assert result["recall"] == 1.0

    def test_partial_recall(self):
        # kept=["a"], true_keep={"a","b"} → precision=1.0, recall=0.5
        result = prune_accuracy(["a"], {"a", "b"}, ["b", "c"], {"c"})
        assert result["precision"] == 1.0
        assert result["recall"] == 0.5

    def test_f1_is_harmonic_mean(self):
        # precision=2/3, recall=1.0 → f1 = 2*(2/3*1.0)/(2/3+1.0) = (4/3)/(5/3) = 4/5
        result = prune_accuracy(["a", "b", "e"], {"a", "b"}, ["c"], {"c"})
        expected_f1 = 2 * (2.0 / 3) * 1.0 / ((2.0 / 3) + 1.0)
        assert result["f1"] == pytest.approx(expected_f1)

    def test_empty_kept(self):
        # No kept items → precision undefined (0.0), recall=0.0, f1=0.0
        result = prune_accuracy([], {"a", "b"}, ["a", "b"], {"a", "b"})
        assert result["precision"] == 0.0
        assert result["recall"] == 0.0
        assert result["f1"] == 0.0

    def test_empty_true_keep(self):
        # true_keep is empty → recall denominator is 0 → recall=0.0
        result = prune_accuracy(["a"], set(), ["b"], {"b"})
        assert result["recall"] == 0.0

    def test_returns_dict_keys(self):
        result = prune_accuracy(["a"], {"a"}, [], set())
        assert set(result.keys()) == {"precision", "recall", "f1"}


# ---------------------------------------------------------------------------
# injection_utility_rate
# ---------------------------------------------------------------------------

class TestInjectionUtilityRate:
    def test_one_third_used(self):
        assert injection_utility_rate(["a", "b", "c"], {"a"}) == pytest.approx(1.0 / 3)

    def test_all_used(self):
        assert injection_utility_rate(["a", "b"], {"a", "b"}) == 1.0

    def test_none_used(self):
        assert injection_utility_rate(["a", "b"], set()) == 0.0

    def test_empty_injected(self):
        assert injection_utility_rate([], {"a"}) == 0.0

    def test_used_superset_of_injected(self):
        # All injected were used, even if used_ids has extras
        assert injection_utility_rate(["a"], {"a", "b", "c"}) == 1.0


# ---------------------------------------------------------------------------
# MetricsResult dataclass
# ---------------------------------------------------------------------------

class TestMetricsResult:
    def test_default_instantiation(self):
        result = MetricsResult()
        assert result.precision_at_1 == 0.0
        assert result.precision_at_5 == 0.0
        assert result.precision_at_10 == 0.0
        assert result.mrr == 0.0
        assert result.prune_precision == 0.0
        assert result.prune_recall == 0.0
        assert result.prune_f1 == 0.0
        assert result.injection_utility_rate == 0.0
        assert result.timestamp is not None

    def test_timestamp_is_string(self):
        result = MetricsResult()
        assert isinstance(result.timestamp, str)

    def test_custom_values(self):
        result = MetricsResult(precision_at_1=0.8, mrr=0.6)
        assert result.precision_at_1 == 0.8
        assert result.mrr == 0.6
