"""Tests for replay priority — salience-based observation sorting."""

import pytest

from core.replay import score_observations, sort_by_salience, ScoredBlock


def _make_content(*obs_types_and_texts):
    """Build ephemeral content with observation blocks.

    Each arg is (type, text) tuple.
    """
    lines = ["# Session Observations — 2026-03-29\n\n"]
    for i, (otype, text) in enumerate(obs_types_and_texts):
        lines.append(f"## [2026-03-29T12:{i:02d}:00] {otype}\n{text}\n\n")
    return "".join(lines)


class TestScoreObservations:
    """Test salience scoring of individual blocks."""

    def test_correction_scores_higher_than_neutral(self):
        content = _make_content(
            ("correction", "No, that's wrong. It should use snake_case."),
            ("domain_knowledge", "The API returns JSON by default."),
        )
        scored = score_observations(content)
        obs = [b for b in scored if b.is_observation]
        assert len(obs) == 2
        # Correction has orienting boost (0.3) + friction somatic (0.25)
        # Domain knowledge has neither
        assert obs[0].salience > obs[1].salience or obs[1].salience > obs[0].salience
        correction = [b for b in obs if "wrong" in b.text][0]
        neutral = [b for b in obs if "JSON" in b.text][0]
        assert correction.salience > neutral.salience

    def test_session_header_preserved(self):
        content = _make_content(("correction", "Fix this"))
        scored = score_observations(content)
        headers = [b for b in scored if not b.is_observation]
        assert len(headers) >= 1
        assert "Session Observations" in headers[0].text

    def test_header_has_inf_salience(self):
        content = _make_content(("correction", "Fix this"))
        scored = score_observations(content)
        headers = [b for b in scored if not b.is_observation]
        assert headers[0].salience == float('inf')

    def test_every_observation_gets_base_salience(self):
        content = _make_content(("domain_knowledge", "Plain fact."))
        scored = score_observations(content)
        obs = [b for b in scored if b.is_observation]
        assert obs[0].salience >= 0.1  # base novelty bonus


class TestSortBySalience:
    """Test full sorting pipeline."""

    def test_correction_sorted_before_neutral(self):
        content = _make_content(
            ("domain_knowledge", "The API returns JSON."),
            ("correction", "No, that's wrong. Use snake_case."),
        )
        sorted_content = sort_by_salience(content)
        # Correction should appear before domain_knowledge in output
        correction_pos = sorted_content.index("wrong")
        neutral_pos = sorted_content.index("JSON")
        assert correction_pos < neutral_pos

    def test_friction_sorted_before_delight(self):
        content = _make_content(
            ("workflow_pattern", "Ship it! This is perfect."),
            ("correction", "No, that's broken. The test failed."),
        )
        sorted_content = sort_by_salience(content)
        friction_pos = sorted_content.index("broken")
        delight_pos = sorted_content.index("perfect")
        assert friction_pos < delight_pos

    def test_session_header_stays_at_top(self):
        content = _make_content(
            ("correction", "Fix this now."),
            ("domain_knowledge", "Plain info."),
        )
        sorted_content = sort_by_salience(content)
        assert sorted_content.startswith("# Session Observations")

    def test_preserves_all_content(self):
        content = _make_content(
            ("correction", "Fix alpha"),
            ("domain_knowledge", "Info beta"),
            ("preference_signal", "Likes gamma"),
        )
        sorted_content = sort_by_salience(content)
        assert "alpha" in sorted_content
        assert "beta" in sorted_content
        assert "gamma" in sorted_content

    def test_stable_sort_for_equal_salience(self):
        # Two blocks with same salience should preserve insertion order
        content = _make_content(
            ("domain_knowledge", "First plain fact."),
            ("domain_knowledge", "Second plain fact."),
        )
        sorted_content = sort_by_salience(content)
        first_pos = sorted_content.index("First plain")
        second_pos = sorted_content.index("Second plain")
        assert first_pos < second_pos


class TestFeatureFlag:
    """Test flag guard."""

    def test_disabled_returns_content_unchanged(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"replay_priority": False})
        content = _make_content(
            ("domain_knowledge", "Info"),
            ("correction", "Fix"),
        )
        result = sort_by_salience(content)
        assert result == content

    def test_disabled_score_returns_single_block(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"replay_priority": False})
        content = _make_content(("correction", "Fix"))
        scored = score_observations(content)
        assert len(scored) == 1
        assert not scored[0].is_observation


class TestNoLLMCalls:
    """Verify salience computation is pure rule-based."""

    def test_scoring_is_fast(self):
        """Score 100 observations in well under 1 second."""
        import time
        obs = [("correction", f"Fix item {i}") for i in range(50)]
        obs += [("domain_knowledge", f"Fact {i}") for i in range(50)]
        content = _make_content(*obs)
        start = time.monotonic()
        sort_by_salience(content)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
