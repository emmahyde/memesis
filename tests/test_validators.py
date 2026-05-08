"""Tests for core/validators.py — Sprint A WS-B."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.validators import (
    KIND_VALUES,
    KNOWLEDGE_TYPE_VALUES,
    Stage1Observation,
    Stage2Observation,
    ValidationError,
    is_pronoun_prefixed,
    validate_stage1,
    validate_stage1_soft,
    validate_stage2,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_STAGE1 = {
    "kind": "finding",
    "knowledge_type": "factual",
    "knowledge_type_confidence": "high",
    "importance": 0.7,
    "facts": ["Emma prefers explicit type annotations in all new C# code"],
    "cwd": "/home/user/projects/sector",
}

VALID_STAGE2 = {
    **VALID_STAGE1,
    "raw_importance": 0.7,
    "subject": "user",
    "work_event": "discovery",
    "subtitle": "Explicit types preferred",
    "linked_observation_ids": [],
}


# ---------------------------------------------------------------------------
# is_pronoun_prefixed
# ---------------------------------------------------------------------------


class TestIsPronounPrefixed:
    def test_clean_fact_returns_false(self):
        assert is_pronoun_prefixed("Emma prefers explicit types") is False

    def test_pronoun_he_returns_true(self):
        assert is_pronoun_prefixed("he tends to over-engineer") is True

    def test_pronoun_she_returns_true(self):
        assert is_pronoun_prefixed("She prefers dark mode") is True

    def test_pronoun_it_returns_true(self):
        assert is_pronoun_prefixed("it stores tokens in Redis") is True

    def test_pronoun_they_returns_true(self):
        assert is_pronoun_prefixed("they decided to use cron") is True

    def test_pronoun_we_returns_true(self):
        assert is_pronoun_prefixed("we chose MonoGame over Godot") is True

    def test_pronoun_i_returns_true(self):
        assert is_pronoun_prefixed("I tend to forget this") is True

    def test_pronoun_this_returns_true(self):
        assert is_pronoun_prefixed("this module handles auth") is True

    def test_pronoun_the_returns_true(self):
        assert is_pronoun_prefixed("the system uses JWT") is True

    def test_case_insensitive(self):
        assert is_pronoun_prefixed("HE tends to over-engineer") is True

    def test_leading_punctuation_stripped(self):
        assert is_pronoun_prefixed("- he said so") is True

    def test_empty_string_returns_false(self):
        assert is_pronoun_prefixed("") is False

    def test_sector_project_named_subject(self):
        assert is_pronoun_prefixed("Sector project uses EventBus for cross-layer events") is False


# ---------------------------------------------------------------------------
# validate_stage1 — hard mode
# ---------------------------------------------------------------------------


class TestValidateStage1Hard:
    def test_valid_record_passes(self):
        obs = validate_stage1(VALID_STAGE1)
        assert isinstance(obs, Stage1Observation)
        assert obs.kind == "finding"
        assert obs.importance == 0.7

    def test_all_kind_values_accepted(self):
        for kind in KIND_VALUES:
            raw = {**VALID_STAGE1, "kind": kind}
            obs = validate_stage1(raw)
            assert obs.kind == kind

    def test_invalid_kind_raises_validation_error(self):
        raw = {**VALID_STAGE1, "kind": "insight"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "kind"
        assert exc_info.value.value == "insight"

    def test_invalid_knowledge_type_raises(self):
        raw = {**VALID_STAGE1, "knowledge_type": "descriptive"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "knowledge_type"

    def test_invalid_knowledge_type_confidence_raises(self):
        raw = {**VALID_STAGE1, "knowledge_type_confidence": "medium"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "knowledge_type_confidence"

    def test_importance_above_1_raises(self):
        raw = {**VALID_STAGE1, "importance": 1.5}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "importance"

    def test_importance_below_0_raises(self):
        raw = {**VALID_STAGE1, "importance": -0.1}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "importance"

    def test_importance_zero_is_valid(self):
        raw = {**VALID_STAGE1, "importance": 0.0}
        obs = validate_stage1(raw)
        assert obs.importance == 0.0

    def test_importance_one_is_valid(self):
        raw = {**VALID_STAGE1, "importance": 1.0}
        obs = validate_stage1(raw)
        assert obs.importance == 1.0

    def test_empty_facts_list_is_allowed(self):
        raw = {**VALID_STAGE1, "facts": []}
        obs = validate_stage1(raw)
        assert obs.facts == []

    def test_facts_with_pronoun_prefix_raises(self):
        raw = {**VALID_STAGE1, "facts": ["he prefers dark mode"]}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert "facts[0]" in exc_info.value.field_name

    def test_facts_more_than_5_raises(self):
        raw = {**VALID_STAGE1, "facts": ["fact one", "fact two", "fact three", "fact four", "fact five", "fact six"]}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert exc_info.value.field_name == "facts"

    def test_missing_required_field_raises(self):
        raw = {k: v for k, v in VALID_STAGE1.items() if k != "kind"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert "kind" in str(exc_info.value)

    def test_missing_importance_raises(self):
        raw = {k: v for k, v in VALID_STAGE1.items() if k != "importance"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage1(raw)
        assert "importance" in str(exc_info.value)

    def test_null_cwd_is_accepted(self):
        raw = {**VALID_STAGE1, "cwd": None}
        obs = validate_stage1(raw)
        assert obs.cwd is None

    def test_cwd_not_present_defaults_to_none(self):
        raw = {k: v for k, v in VALID_STAGE1.items() if k != "cwd"}
        obs = validate_stage1(raw)
        assert obs.cwd is None

    def test_content_optional_legacy_field(self):
        raw = {**VALID_STAGE1, "content": "Some legacy content string"}
        obs = validate_stage1(raw)
        assert obs.content == "Some legacy content string"

    def test_facts_absent_defaults_to_empty(self):
        raw = {k: v for k, v in VALID_STAGE1.items() if k != "facts"}
        obs = validate_stage1(raw)
        assert obs.facts == []


# ---------------------------------------------------------------------------
# validate_stage1_soft — soft mode
# ---------------------------------------------------------------------------


class TestValidateStage1Soft:
    def test_valid_record_returns_obs_and_empty_warnings(self):
        obs, warnings = validate_stage1_soft(VALID_STAGE1)
        assert obs is not None
        assert warnings == []

    def test_invalid_kind_warns_not_raises(self):
        raw = {**VALID_STAGE1, "kind": "observation"}
        obs, warnings = validate_stage1_soft(raw)
        assert obs is not None
        assert any("kind" in w for w in warnings)

    def test_pronoun_fact_soft_warns_and_keeps_fact(self):
        raw = {**VALID_STAGE1, "facts": ["she tends to over-engineer"]}
        obs, warnings = validate_stage1_soft(raw)
        assert obs is not None
        # Fact is kept in soft mode
        assert len(obs.facts) == 1
        assert any("pronoun" in w for w in warnings)

    def test_importance_out_of_range_warns(self):
        raw = {**VALID_STAGE1, "importance": 2.0}
        obs, warnings = validate_stage1_soft(raw)
        # Returns obs with coerced importance (0.0)
        assert obs is not None
        assert any("importance" in w for w in warnings)

    def test_missing_required_field_returns_none_and_warning(self):
        raw = {k: v for k, v in VALID_STAGE1.items() if k != "kind"}
        obs, warnings = validate_stage1_soft(raw)
        assert obs is None
        assert any("kind" in w for w in warnings)

    def test_empty_facts_allowed_in_soft_mode(self):
        raw = {**VALID_STAGE1, "facts": []}
        obs, warnings = validate_stage1_soft(raw)
        assert obs is not None
        assert obs.facts == []

    def test_soft_mode_returns_warnings_list_not_exception(self):
        """Confirm soft mode never raises even on multiple violations."""
        raw = {**VALID_STAGE1, "kind": "bogus", "knowledge_type": "bogus", "importance": 99.0}
        obs, warnings = validate_stage1_soft(raw)
        assert isinstance(warnings, list)
        assert len(warnings) >= 3


# ---------------------------------------------------------------------------
# validate_stage2 — hard mode
# ---------------------------------------------------------------------------


class TestValidateStage2Hard:
    def test_valid_stage2_passes(self):
        obs = validate_stage2(VALID_STAGE2)
        assert isinstance(obs, Stage2Observation)
        assert obs.subject == "user"
        assert obs.work_event == "discovery"

    def test_invalid_subject_raises(self):
        raw = {**VALID_STAGE2, "subject": "team"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert exc_info.value.field_name == "subject"

    def test_null_subject_accepted(self):
        raw = {**VALID_STAGE2, "subject": None}
        obs = validate_stage2(raw)
        assert obs.subject is None

    def test_invalid_work_event_raises(self):
        raw = {**VALID_STAGE2, "work_event": "deploy"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert exc_info.value.field_name == "work_event"

    def test_null_work_event_accepted(self):
        raw = {**VALID_STAGE2, "work_event": None}
        obs = validate_stage2(raw)
        assert obs.work_event is None

    def test_subtitle_over_24_words_raises(self):
        raw = {
            **VALID_STAGE2,
            "subtitle": " ".join(["word"] * 25),
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert exc_info.value.field_name == "subtitle"

    def test_subtitle_exactly_24_words_passes(self):
        raw = {**VALID_STAGE2, "subtitle": " ".join(["word"] * 24)}
        obs = validate_stage2(raw)
        assert obs.subtitle is not None

    def test_raw_importance_out_of_range_raises(self):
        raw = {**VALID_STAGE2, "raw_importance": -0.5}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert exc_info.value.field_name == "raw_importance"

    def test_linked_observation_ids_defaults_to_empty(self):
        raw = {k: v for k, v in VALID_STAGE2.items() if k != "linked_observation_ids"}
        obs = validate_stage2(raw)
        assert obs.linked_observation_ids == []

    # Stage 1 field validations still apply
    def test_invalid_kind_raises_in_stage2(self):
        raw = {**VALID_STAGE2, "kind": "thought"}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert exc_info.value.field_name == "kind"

    def test_facts_pronoun_raises_in_stage2(self):
        raw = {**VALID_STAGE2, "facts": ["it uses JWT internally"]}
        with pytest.raises(ValidationError) as exc_info:
            validate_stage2(raw)
        assert "facts[0]" in exc_info.value.field_name
