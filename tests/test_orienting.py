"""Tests for OrientingDetector — rule-based high-signal moment detection.

These are pure unit tests: no database, no network, no LLM calls.
"""

import pytest

from core.orienting import OrientingDetector, OrientingResult, OrientingSignal


class TestOrientingDetector:
    """Full test suite for OrientingDetector."""

    @pytest.fixture
    def detector(self):
        return OrientingDetector()

    # -----------------------------------------------------------------------
    # Correction signal tests
    # -----------------------------------------------------------------------

    def test_correction_no_thats_wrong(self, detector):
        """'no, that's wrong' triggers correction signal."""
        result = detector.detect("no, that's wrong — use a list comprehension")
        assert result.has_signals
        assert any(s.signal_type == "correction" for s in result.signals)

    def test_correction_actually(self, detector):
        """'actually' triggers correction signal."""
        result = detector.detect("actually, I want it the other way")
        assert result.has_signals
        assert any(s.signal_type == "correction" for s in result.signals)

    def test_correction_not_that(self, detector):
        """'not that' triggers correction signal."""
        result = detector.detect("not that, I said use pytest")
        assert result.has_signals
        assert any(s.signal_type == "correction" for s in result.signals)

    def test_correction_i_said(self, detector):
        """'I said' triggers correction signal."""
        result = detector.detect("I said to use the other approach")
        assert result.has_signals
        assert any(s.signal_type == "correction" for s in result.signals)

    def test_no_correction_neutral_text(self, detector):
        """Neutral text does NOT trigger correction signal."""
        result = detector.detect("that looks good, thanks")
        assert not any(s.signal_type == "correction" for s in result.signals)

    # -----------------------------------------------------------------------
    # Emphasis signal tests
    # -----------------------------------------------------------------------

    def test_emphasis_remember_this(self, detector):
        """'remember this' triggers emphasis signal."""
        result = detector.detect("remember this for next time")
        assert result.has_signals
        assert any(s.signal_type == "emphasis" for s in result.signals)

    def test_emphasis_important(self, detector):
        """'important' triggers emphasis signal."""
        result = detector.detect("this is important — always use type hints")
        assert result.has_signals
        assert any(s.signal_type == "emphasis" for s in result.signals)

    def test_emphasis_never_critical(self, detector):
        """'never' and 'critical' trigger emphasis signal."""
        result = detector.detect("never use print for logging, it's critical we use the logger")
        assert result.has_signals
        assert any(s.signal_type == "emphasis" for s in result.signals)

    def test_emphasis_dont_forget(self, detector):
        """'don't forget' triggers emphasis signal."""
        result = detector.detect("don't forget to run the tests")
        assert result.has_signals
        assert any(s.signal_type == "emphasis" for s in result.signals)

    def test_no_emphasis_neutral_text(self, detector):
        """Neutral test passing text does NOT trigger emphasis signal."""
        result = detector.detect("the test passed fine")
        assert not any(s.signal_type == "emphasis" for s in result.signals)

    # -----------------------------------------------------------------------
    # Error spike signal tests
    # -----------------------------------------------------------------------

    def test_error_spike_three_indicators(self, detector):
        """Three error/traceback indicators triggers error_spike signal."""
        text = "Error: ModuleNotFoundError\nTraceback (most recent call last)\nFailed: test_auth"
        result = detector.detect(text)
        assert result.has_signals
        assert any(s.signal_type == "error_spike" for s in result.signals)

    def test_no_error_spike_single_indicator(self, detector):
        """Only 1 error indicator does NOT trigger error_spike."""
        text = "Error: something went wrong"
        result = detector.detect(text)
        assert not any(s.signal_type == "error_spike" for s in result.signals)

    def test_error_spike_five_indicators_higher_confidence(self, detector):
        """Five error indicators triggers with higher confidence than three."""
        text_3 = "Error: foo\nTraceback (most recent call last)\nFailed: test_bar"
        text_5 = "Error: foo\nTraceback (most recent call last)\nFailed: test_bar\nException: boom\nModuleNotFoundError"
        result_3 = detector.detect(text_3)
        result_5 = detector.detect(text_5)

        spike_3 = next((s for s in result_3.signals if s.signal_type == "error_spike"), None)
        spike_5 = next((s for s in result_5.signals if s.signal_type == "error_spike"), None)

        assert spike_3 is not None
        assert spike_5 is not None
        assert spike_5.confidence > spike_3.confidence

    # -----------------------------------------------------------------------
    # Pacing break signal tests
    # -----------------------------------------------------------------------

    def test_pacing_break_short_message(self, detector):
        """Short message vs high average triggers pacing_break."""
        result = detector.detect("ok", message_lengths=[200, 180, 220, 15])
        assert result.has_signals
        assert any(s.signal_type == "pacing_break" for s in result.signals)

    def test_no_pacing_break_consistent_lengths(self, detector):
        """Consistent message lengths do NOT trigger pacing_break."""
        result = detector.detect("that looks good", message_lengths=[200, 180, 220, 190])
        assert not any(s.signal_type == "pacing_break" for s in result.signals)

    def test_no_pacing_break_none_lengths(self, detector):
        """None message_lengths does NOT trigger pacing_break."""
        result = detector.detect("something", message_lengths=None)
        assert not any(s.signal_type == "pacing_break" for s in result.signals)

    def test_no_pacing_break_empty_lengths(self, detector):
        """Empty message_lengths list does NOT trigger pacing_break."""
        result = detector.detect("something", message_lengths=[])
        assert not any(s.signal_type == "pacing_break" for s in result.signals)

    # -----------------------------------------------------------------------
    # Importance boost tests
    # -----------------------------------------------------------------------

    def test_importance_boost_correction(self, detector):
        """Correction signal has importance_boost >= 0.3."""
        result = detector.detect("no, that's wrong — use a list comprehension")
        assert result.importance_boost >= 0.3

    def test_importance_boost_emphasis(self, detector):
        """Emphasis signal has importance_boost >= 0.2."""
        result = detector.detect("remember this for next time")
        assert result.importance_boost >= 0.2

    def test_importance_boost_error_spike(self, detector):
        """Error spike signal has importance_boost >= 0.2."""
        text = "Error: ModuleNotFoundError\nTraceback (most recent call last)\nFailed: test_auth"
        result = detector.detect(text)
        assert result.importance_boost >= 0.2

    def test_importance_boost_pacing_break(self, detector):
        """Pacing break signal has importance_boost >= 0.1."""
        result = detector.detect("ok", message_lengths=[200, 180, 220, 15])
        assert result.importance_boost >= 0.1

    def test_importance_boost_multiple_signals_is_max(self, detector):
        """Multiple signals (correction + emphasis) boost is max, not additive."""
        # "no, that's wrong" (correction=0.3) + "remember this" (emphasis=0.2)
        text = "no, that's wrong — remember this: always use list comprehensions"
        result = detector.detect(text)
        assert result.has_signals
        # Max boost = 0.3 (correction), not sum 0.5
        assert result.importance_boost == 0.3

    def test_importance_boost_zero_for_no_signals(self, detector):
        """No signals results in importance_boost == 0.0."""
        result = detector.detect("the test passed fine, everything looks good")
        assert result.importance_boost == 0.0

    # -----------------------------------------------------------------------
    # Feature flag guard tests
    # -----------------------------------------------------------------------

    def test_flag_disabled_returns_empty(self, detector, monkeypatch):
        """When orienting_detector flag is False, detect() returns empty result."""
        import core.flags as flags_module
        monkeypatch.setattr(flags_module, "_cache", {"orienting_detector": False})

        result = detector.detect("no, that's wrong!")
        assert result.signals == []
        assert result.importance_boost == 0.0

    def test_flag_enabled_works_normally(self, detector, monkeypatch):
        """When flag is True (default), detect() works normally."""
        import core.flags as flags_module
        monkeypatch.setattr(flags_module, "_cache", {"orienting_detector": True})

        result = detector.detect("no, that's wrong!")
        assert result.has_signals

    # -----------------------------------------------------------------------
    # Edge case tests
    # -----------------------------------------------------------------------

    def test_empty_string_returns_no_signals(self, detector):
        """Empty string input returns no signals."""
        result = detector.detect("")
        assert result.signals == []
        assert result.importance_boost == 0.0

    def test_none_input_returns_no_signals(self, detector):
        """None text input returns no signals gracefully."""
        result = detector.detect(None)
        assert result.signals == []
        assert result.importance_boost == 0.0

    def test_case_insensitivity(self, detector):
        """Uppercase 'NO, THAT'S WRONG' triggers correction signal like lowercase."""
        result = detector.detect("NO, THAT'S WRONG — use a list comprehension")
        assert result.has_signals
        assert any(s.signal_type == "correction" for s in result.signals)

    def test_word_boundary_actually_not_factually(self, detector):
        """'actually' triggers but 'factually' does not."""
        result_factually = detector.detect("factually speaking, this is correct")
        result_actually = detector.detect("actually, this should be different")

        assert not any(s.signal_type == "correction" for s in result_factually.signals), \
            "'factually' should not trigger correction"
        assert any(s.signal_type == "correction" for s in result_actually.signals), \
            "'actually' should trigger correction"
