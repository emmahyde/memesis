"""Tests for InteractionAnalyzer — stateful session-level affect tracking.

Pure unit tests: no database, no network, no LLM calls.
Coherence probe tests mock the LLM transport.
"""

import pytest
from unittest.mock import patch

from core.affect import (
    AffectState,
    CoherenceResult,
    InteractionAnalyzer,
    REPETITION_THRESHOLD,
    WINDOW_SIZE,
    _REPAIR_PATTERNS,
    _LOW_EFFORT_PATTERNS,
    _jaccard,
    _tokenize,
    coherence_probe,
    format_guidance,
    load_analyzer,
    save_analyzer,
)


class TestAffectStateDefaults:
    """AffectState dataclass defaults."""

    def test_default_frustration_zero(self):
        state = AffectState()
        assert state.frustration == 0.0

    def test_default_satisfaction_zero(self):
        state = AffectState()
        assert state.satisfaction == 0.0

    def test_default_momentum_zero(self):
        state = AffectState()
        assert state.momentum == 0.0

    def test_default_repair_count_zero(self):
        state = AffectState()
        assert state.repair_count == 0

    def test_default_expectation_gap_one(self):
        state = AffectState()
        assert state.expectation_gap == 1.0

    def test_default_repetition_zero(self):
        state = AffectState()
        assert state.repetition == 0.0

    def test_default_degradation_zero(self):
        state = AffectState()
        assert state.degradation == 0.0

    def test_needs_guidance_false_by_default(self):
        state = AffectState()
        assert not state.needs_guidance

    def test_needs_guidance_true_above_threshold(self):
        state = AffectState(frustration=0.7)
        assert state.needs_guidance

    def test_likely_degraded_false_by_default(self):
        state = AffectState()
        assert not state.likely_degraded

    def test_likely_degraded_true_above_threshold(self):
        state = AffectState(degradation=0.6)
        assert state.likely_degraded


class TestTokenizeAndJaccard:
    """Test the text similarity primitives."""

    def test_tokenize_extracts_words(self):
        tokens = _tokenize("Hello world, this is a test!")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_tokenize_ignores_short_words(self):
        tokens = _tokenize("I am ok no")
        assert len(tokens) == 0  # all < 3 chars

    def test_tokenize_lowercase(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens

    def test_jaccard_identical_sets(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_jaccard_disjoint_sets(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_partial_overlap(self):
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert 0.0 < result < 1.0

    def test_jaccard_empty_sets(self):
        assert _jaccard(set(), set()) == 0.0
        assert _jaccard({"a"}, set()) == 0.0


class TestRepairPatterns:
    """Verify repair move regex patterns match expected inputs."""

    @pytest.mark.parametrize("text", [
        "no, I meant the other file",
        "like I said, use pytest",
        "I already told you to use ruff",
        "forget it, let's move on",
        "never mind",
        "nevermind",
        "let's try something else",
        "just do it",
        "stop, that's not right",
        "why can't you understand this",
        "please just fix the test",
        "I said this again",
    ])
    def test_repair_pattern_matches(self, text):
        matches = [p for p in _REPAIR_PATTERNS if p.search(text)]
        assert len(matches) >= 1, f"Expected repair match for: {text}"

    @pytest.mark.parametrize("text", [
        "that looks great, thanks",
        "perfect, exactly what I wanted",
        "the function returns a list",
        "can you add a docstring",
    ])
    def test_non_repair_text_no_match(self, text):
        matches = [p for p in _REPAIR_PATTERNS if p.search(text)]
        assert len(matches) == 0, f"Unexpected repair match for: {text}"


class TestLowEffortPatterns:
    """Verify expectation gap markers."""

    @pytest.mark.parametrize("text", [
        "just change the color",
        "simply rename the variable",
        "can you quickly fix this",
        "real quick — swap the order",
        "can you update the test",
        "should be easy to fix",
    ])
    def test_low_effort_matches(self, text):
        matches = [p for p in _LOW_EFFORT_PATTERNS if p.search(text)]
        assert len(matches) >= 1, f"Expected low-effort match for: {text}"


class TestInteractionAnalyzer:
    """Core analyzer behavior."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_first_message_neutral(self, analyzer):
        state = analyzer.update("The function returns a list.")
        assert state.frustration < 0.3
        assert state.repair_count == 0

    def test_friction_message_raises_frustration(self, analyzer):
        analyzer.update("Add a helper function.")
        state = analyzer.update("No, that's wrong. I said use a list comprehension.")
        assert state.frustration > 0.0
        assert state.momentum < 0.0

    def test_delight_message_raises_satisfaction(self, analyzer):
        analyzer.update("Add a helper function.")
        state = analyzer.update("Perfect, exactly what I wanted!")
        assert state.satisfaction > 0.0
        assert state.momentum > 0.0

    def test_repair_moves_accumulate(self, analyzer):
        analyzer.update("Fix the test.")
        analyzer.update("No, I meant the other file.")
        state = analyzer.update("Like I said, use pytest.")
        assert state.repair_count >= 2

    def test_repair_decays_on_delight(self, analyzer):
        analyzer.update("No, I meant the other file.")
        assert analyzer._repair_count >= 1
        analyzer.update("Perfect, exactly right!")
        assert analyzer._repair_count < 2

    def test_repair_count_capped_per_message(self, analyzer):
        state = analyzer.update("No, I meant it. Like I said, just do it. Stop. Again.")
        assert state.repair_count <= 2

    def test_expectation_gap_rises_with_exchanges(self, analyzer):
        analyzer.update("Just change the color.", exchange_count=1)
        state = analyzer.update("Still not right.", exchange_count=5)
        assert state.expectation_gap > 1.0

    def test_expectation_gap_stays_low_when_resolved_quickly(self, analyzer):
        state = analyzer.update("Just change the color.", exchange_count=1)
        assert state.expectation_gap <= 2.0

    def test_sustained_friction_builds_frustration(self, analyzer):
        messages = [
            "That's wrong.",
            "No, I said the other way.",
            "This is broken.",
            "Ugh, still wrong.",
            "I already told you.",
        ]
        state = None
        for i, msg in enumerate(messages):
            state = analyzer.update(msg, exchange_count=i + 1)
        assert state.frustration > 0.5
        assert state.needs_guidance

    def test_sustained_delight_builds_satisfaction(self, analyzer):
        messages = [
            "Perfect!",
            "Exactly right.",
            "Love it, great job!",
            "Brilliant, ship it!",
        ]
        state = None
        for i, msg in enumerate(messages):
            state = analyzer.update(msg, exchange_count=i + 1)
        assert state.satisfaction > 0.4
        assert state.momentum > 0.0

    def test_momentum_resists_single_flip(self, analyzer):
        for i in range(4):
            analyzer.update("That's wrong.", exchange_count=i + 1)
        state = analyzer.update("Nice!", exchange_count=5)
        assert state.momentum < 0.0

    def test_valence_window_bounded(self, analyzer):
        for i in range(WINDOW_SIZE + 5):
            analyzer.update(f"Message {i}", exchange_count=i + 1)
        assert len(analyzer._valence_window) == WINDOW_SIZE

    def test_reset_clears_state(self, analyzer):
        analyzer.update("That's wrong.")
        analyzer.update("Ugh, still broken.")
        analyzer.reset()
        state = analyzer.update("Hello.")
        assert state.repair_count == 0
        assert state.frustration < 0.2
        assert len(state.valence_history) == 1


class TestRepetitionDetection:
    """Test message repetition tracking."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_first_message_zero_repetition(self, analyzer):
        state = analyzer.update("Fix the authentication bug in login flow.")
        assert state.repetition == 0.0

    def test_identical_message_high_repetition(self, analyzer):
        analyzer.update("Fix the authentication bug in login flow.")
        state = analyzer.update("Fix the authentication bug in login flow.")
        assert state.repetition > 0.8

    def test_similar_message_moderate_repetition(self, analyzer):
        analyzer.update("Fix the authentication bug in the login flow.")
        state = analyzer.update("The authentication bug in login is still broken.")
        assert state.repetition > 0.3

    def test_different_message_low_repetition(self, analyzer):
        analyzer.update("Fix the authentication bug in login flow.")
        state = analyzer.update("Add a new endpoint for user preferences.")
        assert state.repetition < 0.3

    def test_repetition_window_bounded(self, analyzer):
        for i in range(10):
            analyzer.update(f"Completely unique message number {i} about topic {i * 7}.")
        from core.affect import REPETITION_WINDOW
        assert len(analyzer._recent_messages) == REPETITION_WINDOW


class TestDegradationDetection:
    """Test degradation likelihood scoring."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_no_degradation_on_fresh_session(self, analyzer):
        state = analyzer.update("Add a helper function.")
        assert state.degradation < 0.2

    def test_repetition_drives_degradation(self, analyzer):
        # Same message repeated = agent isn't responding to corrections
        msg = "Fix the broken authentication middleware please."
        for i in range(4):
            state = analyzer.update(msg, exchange_count=i + 1)
        assert state.degradation > 0.3

    def test_repetition_plus_repair_high_degradation(self, analyzer):
        # User repeating AND repairing = strong degradation signal
        for i in range(4):
            analyzer.update(
                "No, I said fix the auth middleware. Like I said, the login flow.",
                exchange_count=i + 1,
            )
        state = analyzer.update(
            "I already told you — fix the auth middleware. Again.",
            exchange_count=5,
        )
        assert state.likely_degraded

    def test_varied_friction_not_degradation(self, analyzer):
        # Different frustrated messages = hard task, not degradation
        messages = [
            "That approach won't work, the API doesn't support it.",
            "No, the schema is different from what you assumed.",
            "The migration failed because of the foreign key constraint.",
            "Wrong — that column is nullable, not required.",
        ]
        state = None
        for i, msg in enumerate(messages):
            state = analyzer.update(msg, exchange_count=i + 1)
        # Frustrated but not degraded — messages are diverse
        assert state.frustration > 0.3
        assert state.degradation < 0.5


class TestFeatureFlag:
    """Test affect_awareness flag guard."""

    def test_disabled_returns_default_state(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"affect_awareness": False, "somatic_markers": True})

        analyzer = InteractionAnalyzer()
        state = analyzer.update("No, that's wrong! Ugh, this is broken!")
        assert state.frustration == 0.0
        assert state.repair_count == 0
        assert state.momentum == 0.0

    def test_enabled_detects_signals(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"affect_awareness": True, "somatic_markers": True})

        analyzer = InteractionAnalyzer()
        state = analyzer.update("No, that's wrong! Ugh, this is broken!")
        assert state.frustration > 0.0


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_empty_string(self, analyzer):
        state = analyzer.update("")
        assert state.frustration < 0.2

    def test_very_long_message(self, analyzer):
        state = analyzer.update("word " * 10000)
        assert isinstance(state.frustration, float)

    def test_scores_bounded_zero_to_one(self, analyzer):
        for i in range(20):
            state = analyzer.update("That's wrong! Broken! Ugh! Again!", exchange_count=i + 1)
        assert 0.0 <= state.frustration <= 1.0
        assert 0.0 <= state.satisfaction <= 1.0
        assert -1.0 <= state.momentum <= 1.0
        assert 0.0 <= state.degradation <= 1.0
        assert 0.0 <= state.repetition <= 1.0


class TestSerialization:
    """Test to_dict/from_dict round-trip and disk persistence."""

    def test_round_trip(self):
        analyzer = InteractionAnalyzer()
        analyzer.update("That's wrong.", exchange_count=1)
        analyzer.update("No, I meant the other file.", exchange_count=2)

        data = analyzer.to_dict()
        restored = InteractionAnalyzer.from_dict(data)

        assert restored._valence_window == analyzer._valence_window
        assert restored._repair_count == analyzer._repair_count
        assert restored._exchange_count == analyzer._exchange_count
        assert restored._momentum == analyzer._momentum
        assert restored._recent_messages == analyzer._recent_messages

    def test_from_dict_empty(self):
        restored = InteractionAnalyzer.from_dict({})
        assert restored._valence_window == []
        assert restored._repair_count == 0
        assert restored._recent_messages == []

    def test_save_and_load(self, tmp_path):
        base_dir = tmp_path / "memory"
        (base_dir / "ephemeral").mkdir(parents=True)

        analyzer = InteractionAnalyzer()
        analyzer.update("That's wrong.", exchange_count=1)
        save_analyzer(analyzer, base_dir, "sess-test")

        loaded = load_analyzer(base_dir, "sess-test")
        assert loaded._valence_window == analyzer._valence_window
        assert loaded._repair_count == analyzer._repair_count
        assert loaded._momentum == analyzer._momentum
        assert loaded._recent_messages == analyzer._recent_messages

    def test_load_missing_file_returns_fresh(self, tmp_path):
        base_dir = tmp_path / "memory"
        loaded = load_analyzer(base_dir, "nonexistent")
        assert loaded._exchange_count == 0

    def test_load_corrupt_file_returns_fresh(self, tmp_path):
        base_dir = tmp_path / "memory"
        (base_dir / "ephemeral").mkdir(parents=True)
        (base_dir / "ephemeral" / ".affect-sess-bad.json").write_text("not json")

        loaded = load_analyzer(base_dir, "sess-bad")
        assert loaded._exchange_count == 0


class TestCoherenceProbe:
    """Test the coherence probe with mocked LLM calls."""

    def test_similar_responses_low_variance(self):
        with patch("core.llm.call_llm") as mock_llm:
            mock_llm.side_effect = [
                "First read the file, then modify the function signature.",
                "Read the file first, then update the function signature.",
            ]
            result = coherence_probe("Change the function signature")
            assert result.variance < 0.5
            assert not result.likely_degraded

    def test_divergent_responses_high_variance(self):
        with patch("core.llm.call_llm") as mock_llm:
            mock_llm.side_effect = [
                "Rewrite the entire authentication module from scratch using OAuth.",
                "Add a simple password check to the existing login handler.",
            ]
            result = coherence_probe("Fix authentication")
            assert result.variance > 0.4
            assert isinstance(result.response_a, str)
            assert isinstance(result.response_b, str)

    def test_probe_calls_llm_twice(self):
        with patch("core.llm.call_llm") as mock_llm:
            mock_llm.return_value = "Some response about the approach."
            coherence_probe("Fix the test")
            assert mock_llm.call_count == 2

    def test_probe_uses_temperature(self):
        with patch("core.llm.call_llm") as mock_llm:
            mock_llm.return_value = "Some response."
            coherence_probe("Fix the test")
            for call in mock_llm.call_args_list:
                assert call.kwargs.get("temperature", call[1].get("temperature")) == 0.7


class TestCorrectionsTracking:
    """Test that user corrections are captured and persisted."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_repair_message_logged_as_correction(self, analyzer):
        analyzer.update("No, I meant use the other approach.")
        assert len(analyzer._corrections) == 1
        assert "other approach" in analyzer._corrections[0]

    def test_non_repair_message_not_logged(self, analyzer):
        analyzer.update("The function returns a list.")
        assert len(analyzer._corrections) == 0

    def test_corrections_accumulate(self, analyzer):
        analyzer.update("No, I meant the other file.")
        analyzer.update("Like I said, use pytest.")
        assert len(analyzer._corrections) == 2

    def test_corrections_capped(self, analyzer):
        for i in range(15):
            analyzer.update(f"No, I meant approach {i}. Again.")
        assert len(analyzer._corrections) == InteractionAnalyzer.MAX_CORRECTIONS

    def test_corrections_in_affect_state(self, analyzer):
        analyzer.update("No, I meant the other file.")
        state = analyzer.update("Like I said, use pytest.")
        assert len(state.corrections) >= 1

    def test_corrections_serialized(self):
        analyzer = InteractionAnalyzer()
        analyzer.update("No, I meant the other file.")

        data = analyzer.to_dict()
        restored = InteractionAnalyzer.from_dict(data)
        assert restored._corrections == analyzer._corrections

    def test_corrections_persisted_to_disk(self, tmp_path):
        base_dir = tmp_path / "memory"
        (base_dir / "ephemeral").mkdir(parents=True)

        analyzer = InteractionAnalyzer()
        analyzer.update("No, I meant the other file.")
        save_analyzer(analyzer, base_dir, "sess-corr")

        loaded = load_analyzer(base_dir, "sess-corr")
        assert loaded._corrections == analyzer._corrections


class TestCurrentState:
    """Test InteractionAnalyzer.current_state() — snapshot without a new message."""

    @pytest.fixture
    def analyzer(self):
        return InteractionAnalyzer()

    def test_fresh_analyzer_returns_affect_state(self, analyzer):
        state = analyzer.current_state()
        assert isinstance(state, AffectState)

    def test_fresh_analyzer_neutral(self, analyzer):
        state = analyzer.current_state()
        assert state.frustration == 0.0
        assert state.satisfaction == 0.0
        assert state.momentum == 0.0
        assert state.repair_count == 0

    def test_current_state_consistent_with_update(self, analyzer):
        # After several updates, current_state should reflect same direction
        messages = [
            "That's wrong.",
            "No, I said the other way.",
            "Ugh, this is broken.",
        ]
        last_update_state = None
        for i, msg in enumerate(messages):
            last_update_state = analyzer.update(msg, exchange_count=i + 1)

        snapshot = analyzer.current_state()
        # Both should reflect frustration from the same session signals
        assert snapshot.frustration > 0.0
        assert snapshot.frustration == pytest.approx(last_update_state.frustration, abs=0.05)

    def test_current_state_does_not_mutate_valence_window(self, analyzer):
        analyzer.update("That's wrong.", exchange_count=1)
        window_before = list(analyzer._valence_window)
        analyzer.current_state()
        assert analyzer._valence_window == window_before

    def test_current_state_does_not_mutate_repair_count(self, analyzer):
        analyzer.update("No, I meant the other file.")
        count_before = analyzer._repair_count
        analyzer.current_state()
        assert analyzer._repair_count == count_before

    def test_current_state_does_not_mutate_recent_messages(self, analyzer):
        analyzer.update("Fix the auth bug in login flow.")
        messages_before = list(analyzer._recent_messages)
        analyzer.current_state()
        assert analyzer._recent_messages == messages_before

    def test_current_state_valence_history_matches_window(self, analyzer):
        analyzer.update("Perfect!")
        analyzer.update("That's wrong.")
        state = analyzer.current_state()
        assert state.valence_history == analyzer._valence_window

    def test_current_state_corrections_matches_internal(self, analyzer):
        analyzer.update("No, I meant the other file.")
        state = analyzer.current_state()
        assert state.corrections == analyzer._corrections

    def test_current_state_repair_count_matches_internal(self, analyzer):
        analyzer.update("No, I meant use the other approach.")
        analyzer.update("Like I said, use pytest.")
        state = analyzer.current_state()
        assert state.repair_count == analyzer._repair_count

    def test_current_state_scores_bounded(self, analyzer):
        for i in range(10):
            analyzer.update("That's wrong! Broken! Again!", exchange_count=i + 1)
        state = analyzer.current_state()
        assert 0.0 <= state.frustration <= 1.0
        assert 0.0 <= state.satisfaction <= 1.0
        assert -1.0 <= state.momentum <= 1.0
        assert 0.0 <= state.degradation <= 1.0
        assert 0.0 <= state.repetition <= 1.0

    def test_current_state_after_sustained_delight(self, analyzer):
        for i in range(5):
            analyzer.update("Perfect!", exchange_count=i + 1)
        state = analyzer.current_state()
        assert state.satisfaction > state.frustration
        assert state.momentum > 0.0

    def test_current_state_disabled_flag_returns_default(self, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"affect_awareness": False, "somatic_markers": True})

        a = InteractionAnalyzer()
        # Manually set some internal state to prove it's ignored when flag off
        a._repair_count = 5
        a._valence_window = ["friction", "friction", "friction"]
        state = a.current_state()
        assert state.frustration == 0.0
        assert state.repair_count == 0

    def test_current_state_independent_of_call_order(self, analyzer):
        # current_state() called before any update should not crash or bias later updates
        _ = analyzer.current_state()
        state = analyzer.update("Perfect, exactly right!")
        assert state.satisfaction > 0.0

    def test_current_state_from_loaded_analyzer(self, tmp_path):
        """Loaded analyzer should produce same current_state as original."""
        base_dir = tmp_path / "memory"
        (base_dir / "ephemeral").mkdir(parents=True)

        a = InteractionAnalyzer()
        a.update("No, I meant the other file.", exchange_count=1)
        a.update("Like I said, use pytest.", exchange_count=2)
        save_analyzer(a, base_dir, "sess-cs")

        restored = load_analyzer(base_dir, "sess-cs")
        original_state = a.current_state()
        restored_state = restored.current_state()

        assert restored_state.frustration == pytest.approx(original_state.frustration, abs=1e-9)
        assert restored_state.satisfaction == pytest.approx(original_state.satisfaction, abs=1e-9)
        assert restored_state.repair_count == original_state.repair_count
        assert restored_state.momentum == pytest.approx(original_state.momentum, abs=1e-9)


class TestFormatGuidance:
    """Test guidance text formatting."""

    def test_no_guidance_when_not_frustrated(self):
        state = AffectState(frustration=0.3)
        assert format_guidance(state) == ""

    def test_degradation_guidance_when_degraded(self):
        state = AffectState(
            frustration=0.8, degradation=0.7,
            repair_count=4, repetition=0.6, expectation_gap=1.0,
        )
        guidance = format_guidance(state)
        assert "degrading" in guidance
        assert "compacting" in guidance
        assert "repeating" in guidance

    def test_degradation_guidance_includes_repair_count(self):
        state = AffectState(
            frustration=0.8, degradation=0.7,
            repair_count=5, repetition=0.3,
        )
        guidance = format_guidance(state)
        assert "5 repair moves" in guidance
        assert "not learning" in guidance

    def test_task_difficulty_instructs_plan(self):
        state = AffectState(
            frustration=0.7, degradation=0.2,
            repair_count=2, expectation_gap=2.0,
        )
        guidance = format_guidance(state)
        assert "plan" in guidance.lower()
        assert "WORKLOG" in guidance
        assert "degrading" not in guidance

    def test_task_difficulty_instructs_no_retry(self):
        state = AffectState(
            frustration=0.7, degradation=0.2,
            repair_count=2, expectation_gap=2.0,
        )
        guidance = format_guidance(state)
        assert "do NOT retry" in guidance

    def test_task_difficulty_includes_corrections(self):
        state = AffectState(
            frustration=0.8, degradation=0.2,
            repair_count=3, expectation_gap=2.0,
            corrections=[
                "No, I meant the other file",
                "Like I said, use pytest not unittest",
            ],
        )
        guidance = format_guidance(state)
        assert "other file" in guidance
        assert "pytest" in guidance
        assert "corrections" in guidance.lower()

    def test_task_difficulty_limits_corrections_to_five(self):
        corrections = [f"Correction number {i}" for i in range(8)]
        state = AffectState(
            frustration=0.8, degradation=0.2,
            repair_count=3, expectation_gap=2.0,
            corrections=corrections,
        )
        guidance = format_guidance(state)
        # Should only include last 5
        assert "Correction number 7" in guidance
        assert "Correction number 2" not in guidance

    def test_task_difficulty_no_corrections_still_works(self):
        state = AffectState(
            frustration=0.7, degradation=0.2,
            repair_count=2, expectation_gap=2.0,
        )
        guidance = format_guidance(state)
        assert "plan" in guidance.lower()
        assert "User corrections so far" not in guidance


class TestNonTypedInputGating:
    """update() must reject pasted skill bodies / system reminders without
    poisoning the valence window or repair counter."""

    def test_skill_body_does_not_increment_repair_count(self):
        analyzer = InteractionAnalyzer()
        body = (
            "Base directory for this skill: /Users/foo/.claude/skills/x\n\n"
            "# Foo skill\nDocumentation says this never fails, never errors, "
            "and never blocks even when wrong inputs arrive."
        )
        analyzer.update(body)
        assert analyzer._repair_count == 0
        assert analyzer._valence_window == []

    def test_system_reminder_does_not_log_correction(self):
        analyzer = InteractionAnalyzer()
        msg = "<system-reminder>I told you again to stop, please just do this</system-reminder>"
        analyzer.update(msg)
        assert analyzer._corrections == []

    def test_long_paste_skipped(self):
        analyzer = InteractionAnalyzer()
        analyzer.update("x " * 500)
        assert analyzer._exchange_count == 0

    def test_real_typed_friction_still_counts(self):
        analyzer = InteractionAnalyzer()
        analyzer.update("ugh, this is broken again")
        assert "friction" in analyzer._valence_window
