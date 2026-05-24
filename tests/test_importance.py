"""Tests for core/importance.py — deterministic importance calibration."""

from __future__ import annotations

import pytest

from core.importance import (
    KIND_IMPORTANCE_FLOOR,
    calibrate_importance,
    distribution_is_collapsed,
    has_action_item,
    has_numeric_evidence,
)


# --- per-kind floor ---------------------------------------------------------


def test_floor_raises_a_low_score():
    # A goal scored 0.4 by the LLM is floored up to 0.85.
    assert calibrate_importance(0.4, "goal", "") == pytest.approx(0.85)


def test_floor_does_not_lower_a_high_score():
    # A fact scored 0.9 keeps 0.9 — the floor only raises.
    assert calibrate_importance(0.9, "fact", "") == pytest.approx(0.9)


def test_every_content_kind_has_a_floor():
    """Every content kind (KIND_VALUES minus lifecycle states) carries a floor."""
    from core.validators import KIND_VALUES, is_lifecycle_kind
    content_kinds = {k for k in KIND_VALUES if not is_lifecycle_kind(k)}
    assert set(KIND_IMPORTANCE_FLOOR) == content_kinds


def test_unknown_kind_applies_no_floor():
    assert calibrate_importance(0.3, "open_question", "") == pytest.approx(0.3)
    assert calibrate_importance(0.3, None, "") == pytest.approx(0.3)


# --- additive axes ----------------------------------------------------------


def test_action_item_adds_bonus():
    assert calibrate_importance(0.3, None, "we should fix the cron") == pytest.approx(0.4)


def test_numeric_evidence_adds_bonus():
    assert calibrate_importance(0.3, None, "recall improved 10x") == pytest.approx(0.4)


def test_both_axes_stack():
    score = calibrate_importance(0.3, None, "should cut latency, saw 40% drop")
    assert score == pytest.approx(0.5)


def test_score_is_clamped_to_one():
    assert calibrate_importance(0.95, "goal", "must do this, 99% of runs") == 1.0


def test_none_base_defaults_to_half():
    assert calibrate_importance(None, None, "") == pytest.approx(0.5)


def test_has_action_item_detects_phrases():
    assert has_action_item("this needs to be addressed")
    assert has_action_item("TODO: rewrite")
    assert not has_action_item("the cron ran cleanly")


def test_has_numeric_evidence_detects_measurements():
    assert has_numeric_evidence("87/48/3 split")
    assert has_numeric_evidence("94% cache hit rate")
    assert has_numeric_evidence("10x faster")
    assert not has_numeric_evidence("a few memories were kept")


# --- distribution collapse --------------------------------------------------


def test_collapsed_distribution_flagged():
    scores = [0.5, 0.55, 0.6, 0.52, 0.58, 0.9]  # 5/6 in mid-band
    collapsed, fraction = distribution_is_collapsed(scores)
    assert collapsed
    assert fraction == pytest.approx(5 / 6)


def test_healthy_distribution_not_flagged():
    scores = [0.2, 0.3, 0.5, 0.85, 0.9, 0.95]  # 1/6 in mid-band
    collapsed, fraction = distribution_is_collapsed(scores)
    assert not collapsed


def test_empty_distribution_not_collapsed():
    assert distribution_is_collapsed([]) == (False, 0.0)
