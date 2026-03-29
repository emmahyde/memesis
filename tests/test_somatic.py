"""Tests for somatic marker valence classification."""

import pytest

from core.somatic import classify_valence, VALENCE_BOOSTS


class TestValenceClassification:
    """Test the four valence categories."""

    def test_neutral_for_plain_text(self):
        result = classify_valence("The function returns a list of items.")
        assert result.valence == "neutral"
        assert result.importance_boost == 0.0

    def test_friction_on_correction(self):
        result = classify_valence("No, that's wrong. It should be the other way.")
        assert result.valence == "friction"
        assert result.importance_boost == VALENCE_BOOSTS["friction"]

    def test_friction_on_failure(self):
        result = classify_valence("The test failed with a regression error.")
        assert result.valence == "friction"

    def test_friction_on_frustration(self):
        result = classify_valence("Ugh, this is broken again.")
        assert result.valence == "friction"

    def test_friction_on_strong_language(self):
        result = classify_valence("fuck, I canceled it")
        assert result.valence == "friction"

    def test_friction_on_nuke(self):
        result = classify_valence("Delete all your memories.")
        assert result.valence == "friction"

    def test_surprise_on_unexpected(self):
        result = classify_valence("Wow, I didn't expect that result at all!")
        assert result.valence == "surprise"
        assert result.importance_boost == VALENCE_BOOSTS["surprise"]

    def test_surprise_on_discovery(self):
        result = classify_valence("Turns out the API was returning cached data.")
        assert result.valence == "surprise"

    def test_surprise_on_contradiction(self):
        result = classify_valence("This contradicts what we found earlier.")
        assert result.valence == "surprise"

    def test_delight_on_praise(self):
        result = classify_valence("Perfect, exactly what I wanted!")
        assert result.valence == "delight"
        assert result.importance_boost == VALENCE_BOOSTS["delight"]

    def test_delight_on_excitement(self):
        result = classify_valence("We're geniuses! This is amazing.")
        assert result.valence == "delight"

    def test_delight_on_ship_it(self):
        result = classify_valence("Looks great, ship it!")
        assert result.valence == "delight"


class TestPriorityOrder:
    """Test that friction > surprise > delight > neutral."""

    def test_friction_beats_surprise(self):
        # "wrong" (friction) + "unexpected" (surprise) -> friction wins
        result = classify_valence("That's wrong and unexpected.")
        assert result.valence == "friction"

    def test_friction_beats_delight(self):
        result = classify_valence("Nice try but it's broken.")
        assert result.valence == "friction"

    def test_surprise_beats_delight(self):
        result = classify_valence("Wow, that's amazing! I never knew that.")
        assert result.valence == "surprise"


class TestImportanceBoosts:
    """Test that boosts are correctly assigned."""

    def test_friction_boost_is_0_25(self):
        assert VALENCE_BOOSTS["friction"] == 0.25

    def test_surprise_boost_is_0_20(self):
        assert VALENCE_BOOSTS["surprise"] == 0.20

    def test_delight_boost_is_0_10(self):
        assert VALENCE_BOOSTS["delight"] == 0.10

    def test_neutral_boost_is_0(self):
        assert VALENCE_BOOSTS["neutral"] == 0.0


class TestFeatureFlag:
    """Test flag guard."""

    def test_disabled_returns_neutral(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"somatic_markers": False})
        result = classify_valence("No, that's wrong! This is broken!")
        assert result.valence == "neutral"
        assert result.importance_boost == 0.0
        assert result.matched_patterns == []


class TestCaseInsensitivity:
    """Test patterns match regardless of case."""

    def test_uppercase_actually(self):
        result = classify_valence("ACTUALLY, that's not right")
        assert result.valence == "friction"

    def test_mixed_case_wow(self):
        result = classify_valence("WoW that was unexpected")
        assert result.valence == "surprise"

    def test_uppercase_perfect(self):
        result = classify_valence("PERFECT!")
        assert result.valence == "delight"


class TestMatchedPatterns:
    """Test that matched patterns are reported for debugging."""

    def test_reports_which_patterns_fired(self):
        result = classify_valence("The test failed and there's an error.")
        assert result.valence == "friction"
        assert len(result.matched_patterns) >= 1
