"""
Tests for core/prompts.py — Wave 2 WS-D prompt rewrite verification.

Asserts:
- OBSERVATION_EXTRACT_PROMPT (Stage 1) contains required language fragments
- CONSOLIDATION_PROMPT (Stage 2) contains required language fragments
- CONCEPT_TAGS dict is removed from the module
- Both prompts format without KeyError given mock data
- Skip protocol is documented in Stage 1
- SESSION_TYPE_GUIDANCE dict exists with expected keys (Wave B #33)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.prompts as prompts_module
from core.prompts import (
    CONSOLIDATION_PROMPT,
    OBSERVATION_EXTRACT_PROMPT,
    SESSION_TYPE_GUIDANCE,
    format_extract_prompt,
)


# ---------------------------------------------------------------------------
# CONCEPT_TAGS removal
# ---------------------------------------------------------------------------


class TestConceptTagsRemoved:
    def test_concept_tags_not_in_module(self):
        assert not hasattr(prompts_module, "CONCEPT_TAGS"), (
            "CONCEPT_TAGS dict must be removed per panel C2 / TAXONOMY §3"
        )

    def test_concept_tags_not_in_consolidation_prompt(self):
        assert "concept_tags" not in CONSOLIDATION_PROMPT.lower(), (
            "CONSOLIDATION_PROMPT must not reference concept_tags"
        )

    def test_concept_tags_not_in_extract_prompt(self):
        assert "concept_tags" not in OBSERVATION_EXTRACT_PROMPT.lower(), (
            "OBSERVATION_EXTRACT_PROMPT must not reference concept_tags"
        )


# ---------------------------------------------------------------------------
# Stage 1 — OBSERVATION_EXTRACT_PROMPT
# ---------------------------------------------------------------------------


class TestStage1PromptStructure:
    def test_no_quota_language(self):
        # Old "0-3" hard cap must be gone; quality gate replaces it
        assert "0-3" not in OBSERVATION_EXTRACT_PROMPT, (
            "Stage 1 must not contain '0-3' hard quota; use quality-gate language"
        )

    def test_quality_gate_present(self):
        assert "quality gate" in OBSERVATION_EXTRACT_PROMPT.lower()

    def test_knowledge_type_confidence_field(self):
        assert "knowledge_type_confidence" in OBSERVATION_EXTRACT_PROMPT

    def test_kind_field_present(self):
        assert '"kind"' in OBSERVATION_EXTRACT_PROMPT

    def test_knowledge_type_field_present(self):
        assert '"knowledge_type"' in OBSERVATION_EXTRACT_PROMPT

    def test_facts_field_present(self):
        assert '"facts"' in OBSERVATION_EXTRACT_PROMPT

    def test_cwd_field_present(self):
        assert '"cwd"' in OBSERVATION_EXTRACT_PROMPT

    def test_skip_protocol_documented(self):
        assert '"skipped"' in OBSERVATION_EXTRACT_PROMPT
        assert "skipped" in OBSERVATION_EXTRACT_PROMPT.lower()

    def test_skip_protocol_reason_field(self):
        assert '"reason"' in OBSERVATION_EXTRACT_PROMPT

    def test_no_empty_array_for_skip(self):
        # Must instruct to use skip signal, not empty array
        lower = OBSERVATION_EXTRACT_PROMPT.lower()
        assert "do not return an empty array" in lower or "not return an empty array" in lower

    def test_verb_anchor_list(self):
        # At least a subset of the required verbs from claude-mem code.json
        for verb in ("implemented", "fixed", "deployed", "configured", "refactored"):
            assert verb in OBSERVATION_EXTRACT_PROMPT.lower(), (
                f"Verb anchor '{verb}' missing from Stage 1 prompt"
            )

    def test_orthogonality_note(self):
        lower = OBSERVATION_EXTRACT_PROMPT.lower()
        assert "independent" in lower, (
            "Prompt must note that kind and knowledge_type are independent dimensions"
        )

    def test_kind_enum_values_present(self):
        for value in ("decision", "finding", "preference", "constraint", "correction", "open_question"):
            assert value in OBSERVATION_EXTRACT_PROMPT

    def test_knowledge_type_enum_values_present(self):
        for value in ("factual", "conceptual", "procedural", "metacognitive"):
            assert value in OBSERVATION_EXTRACT_PROMPT

    def test_importance_anchors_present(self):
        assert "0.2" in OBSERVATION_EXTRACT_PROMPT
        assert "0.5" in OBSERVATION_EXTRACT_PROMPT
        assert "0.8" in OBSERVATION_EXTRACT_PROMPT
        assert "0.95" in OBSERVATION_EXTRACT_PROMPT

    def test_no_pronoun_instruction(self):
        lower = OBSERVATION_EXTRACT_PROMPT.lower()
        assert "no pronouns" in lower or "named subject" in lower

    def test_footer_no_markdown(self):
        lower = OBSERVATION_EXTRACT_PROMPT.lower()
        assert "no markdown" in lower or "no markdown fences" in lower

    def test_mode_field_not_in_output_schema(self):
        # Old field name must be gone from output schema block
        # Check it's not used as a JSON key in the output schema
        assert '"mode"' not in OBSERVATION_EXTRACT_PROMPT

    def test_no_subject_in_stage1_output_schema(self):
        # subject is Stage 2 only per panel C5
        assert '"subject"' not in OBSERVATION_EXTRACT_PROMPT

    def test_format_with_mock_transcript(self):
        # #33: smoke test via format_extract_prompt() to exercise per-session-type guidance injection
        formatted = format_extract_prompt(
            transcript="mock session content",
            session_type="code",
            affect_hint="",
        )
        assert "mock session content" in formatted
        # Verify code-session guidance was injected (not the "unknown" fallback)
        from core.prompts import SESSION_TYPE_GUIDANCE
        assert SESSION_TYPE_GUIDANCE["code"] in formatted


# ---------------------------------------------------------------------------
# Stage 2 — CONSOLIDATION_PROMPT
# ---------------------------------------------------------------------------


class TestStage2PromptStructure:
    def test_re_score_independently(self):
        lower = CONSOLIDATION_PROMPT.lower()
        assert "re-score" in lower or "re score" in lower or "independently" in lower

    def test_raw_importance_field(self):
        assert "raw_importance" in CONSOLIDATION_PROMPT

    def test_importance_field(self):
        assert '"importance"' in CONSOLIDATION_PROMPT

    def test_subject_field_present(self):
        assert '"subject"' in CONSOLIDATION_PROMPT

    def test_subject_enum_values(self):
        for value in ("self", "user", "system", "collaboration", "workflow", "aesthetic", "domain"):
            assert value in CONSOLIDATION_PROMPT

    def test_work_event_field_present(self):
        assert '"work_event"' in CONSOLIDATION_PROMPT

    def test_work_event_null_default_guidance(self):
        lower = CONSOLIDATION_PROMPT.lower()
        assert "null" in lower

    def test_subtitle_field_present(self):
        assert '"subtitle"' in CONSOLIDATION_PROMPT

    def test_subtitle_word_limit(self):
        assert "24" in CONSOLIDATION_PROMPT

    def test_kind_field_preserved(self):
        assert '"kind"' in CONSOLIDATION_PROMPT

    def test_knowledge_type_field_present(self):
        assert '"knowledge_type"' in CONSOLIDATION_PROMPT

    def test_knowledge_type_confidence_field(self):
        assert '"knowledge_type_confidence"' in CONSOLIDATION_PROMPT

    def test_facts_field_present(self):
        assert '"facts"' in CONSOLIDATION_PROMPT

    def test_behavioral_gate_language(self):
        lower = CONSOLIDATION_PROMPT.lower()
        assert "would i do something wrong" in lower or "do something wrong" in lower

    def test_no_most_should_die_language(self):
        # Panel finding: "MOST SHOULD DIE" primes LLM toward stinginess
        assert "MOST SHOULD DIE" not in CONSOLIDATION_PROMPT

    def test_action_field_present(self):
        assert '"action"' in CONSOLIDATION_PROMPT

    def test_reinforces_contradicts_fields(self):
        assert '"reinforces"' in CONSOLIDATION_PROMPT
        assert '"contradicts"' in CONSOLIDATION_PROMPT

    def test_empty_buffer_returns_decisions_array(self):
        assert '"decisions": []' in CONSOLIDATION_PROMPT or '{"decisions": []}' in CONSOLIDATION_PROMPT

    def test_no_concept_tags_in_output_schema(self):
        assert "concept_tags" not in CONSOLIDATION_PROMPT

    def test_no_observation_type_legacy_field_promoted(self):
        # observation_type was the old Stage 2 field — should not appear in new output schema
        assert '"observation_type"' not in CONSOLIDATION_PROMPT

    def test_format_with_mock_data(self):
        # Smoke test: no KeyError (open_questions_block added by WS-H)
        formatted = CONSOLIDATION_PROMPT.format(
            ephemeral_content="[observation 1]\n[observation 2]",
            manifest_summary="existing memories: none",
            open_questions_block="none",
        )
        assert "observation 1" in formatted
        assert "existing memories" in formatted


# ---------------------------------------------------------------------------
# Task 1.2 — Session-type extraction guidance + skip-friction
# ---------------------------------------------------------------------------


class TestSessionTypeGuidance:
    def test_research_guidance_present(self):
        assert "research" in OBSERVATION_EXTRACT_PROMPT
        # work_event=null guidance must appear in the prompt (via SESSION_TYPE GUIDANCE)
        assert "work_event=null" in OBSERVATION_EXTRACT_PROMPT

    def test_writing_guidance_present(self):
        assert "writing" in OBSERVATION_EXTRACT_PROMPT
        assert "aesthetic choices" in OBSERVATION_EXTRACT_PROMPT

    def test_skip_friction_present(self):
        # "name one specific" must appear before the skip sentinel ({{"skipped": true, ...}})
        skip_sentinel = '{{"skipped": true,'
        friction_phrase = "name one specific"
        prompt = OBSERVATION_EXTRACT_PROMPT
        assert friction_phrase in prompt, f"'{friction_phrase}' not found in OBSERVATION_EXTRACT_PROMPT"
        friction_pos = prompt.index(friction_phrase)
        sentinel_pos = prompt.index(skip_sentinel)
        assert friction_pos < sentinel_pos, (
            "SKIP DISCIPLINE ('name one specific') must appear before the skip sentinel"
        )

    def test_no_new_format_placeholders(self):
        # Ensure no new {placeholder} keys were introduced beyond the expected set
        import re
        # Find all {word} patterns (single-word placeholders, not {{ or }})
        placeholders = set(re.findall(r'(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})', OBSERVATION_EXTRACT_PROMPT))
        expected = {"session_type", "affect_hint", "transcript"}
        assert placeholders <= expected, (
            f"Unexpected format placeholders introduced: {placeholders - expected}"
        )

    def test_session_type_guidance_block_header(self):
        assert "SESSION_TYPE GUIDANCE" in OBSERVATION_EXTRACT_PROMPT or "SESSION-TYPE EXTRACTION GUIDANCE" in OBSERVATION_EXTRACT_PROMPT

    def test_code_session_type_guidance_present(self):
        assert "code" in OBSERVATION_EXTRACT_PROMPT


# ---------------------------------------------------------------------------
# Task B3 #33 — SESSION_TYPE_GUIDANCE dict
# ---------------------------------------------------------------------------


class TestSessionTypeGuidanceDict:
    # #33: verify dict exists with the expected session-type keys
    EXPECTED_KEYS = {"code", "research", "writing", "agent_driven", "unknown"}

    def test_dict_exists_in_module(self):
        assert hasattr(prompts_module, "SESSION_TYPE_GUIDANCE"), (
            "SESSION_TYPE_GUIDANCE dict must be present in core.prompts"
        )

    def test_expected_keys_present(self):
        missing = self.EXPECTED_KEYS - set(SESSION_TYPE_GUIDANCE.keys())
        assert not missing, f"SESSION_TYPE_GUIDANCE missing keys: {missing}"

    def test_all_values_are_strings(self):
        for key, val in SESSION_TYPE_GUIDANCE.items():
            assert isinstance(val, str), f"SESSION_TYPE_GUIDANCE['{key}'] must be a string"

    def test_non_empty_guidance_for_named_types(self):
        # Each named session type (not 'unknown') should have non-empty guidance
        for key in ("code", "research", "writing", "agent_driven"):
            assert SESSION_TYPE_GUIDANCE[key].strip(), (
                f"SESSION_TYPE_GUIDANCE['{key}'] must not be empty"
            )

    def test_template_var_in_extract_prompt_template(self):
        # {session_type_guidance} placeholder must appear in the raw template
        from core.prompts import _OBSERVATION_EXTRACT_PROMPT_TEMPLATE
        assert "{session_type_guidance}" in _OBSERVATION_EXTRACT_PROMPT_TEMPLATE, (
            "{session_type_guidance} template var missing from _OBSERVATION_EXTRACT_PROMPT_TEMPLATE"
        )

    def test_guidance_injected_via_format_extract_prompt(self):
        # Render with "code" session type; verify code guidance appears in output
        formatted = format_extract_prompt(
            transcript="sample content",
            session_type="code",
            affect_hint="",
        )
        guidance_text = SESSION_TYPE_GUIDANCE["code"]
        assert guidance_text in formatted, (
            "format_extract_prompt did not inject SESSION_TYPE_GUIDANCE['code'] into rendered prompt"
        )

    def test_unknown_fallback_when_session_type_missing(self):
        # Unrecognised session type should fall back to 'unknown' guidance
        formatted = format_extract_prompt(
            transcript="sample content",
            session_type="nonexistent_type",
            affect_hint="",
        )
        # 'unknown' guidance text should appear instead of raising KeyError
        assert SESSION_TYPE_GUIDANCE["unknown"] in formatted

    def test_research_guidance_injected(self):
        formatted = format_extract_prompt(
            transcript="research session",
            session_type="research",
            affect_hint="",
        )
        assert SESSION_TYPE_GUIDANCE["research"] in formatted


# ---------------------------------------------------------------------------
# Task C1 #30 — Orphan quality gate boundary invariant
# Orphaning is a synthesis-layer concept (core/issue_cards.py); it must NOT
# leak into extraction (Stage 1) or consolidation (Stage 2) prompts.
# ---------------------------------------------------------------------------


class TestOrphanQualityGateBoundary:
    # "orphan" and "cluster" are synthesis-only vocabulary; they do not apply
    # to observation extraction or consolidation and must stay absent.

    def test_orphan_not_in_extract_prompt(self):
        assert "orphan" not in OBSERVATION_EXTRACT_PROMPT.lower(), (
            "orphan/synthesis vocabulary must not appear in OBSERVATION_EXTRACT_PROMPT; "
            "orphaning is a quality gate in issue_cards.py (synthesis stage), not here."
        )

    def test_orphan_not_in_consolidation_prompt(self):
        assert "orphan" not in CONSOLIDATION_PROMPT.lower(), (
            "orphan/synthesis vocabulary must not appear in CONSOLIDATION_PROMPT; "
            "orphaning is a quality gate in issue_cards.py (synthesis stage), not here."
        )

    def test_issue_card_not_in_extract_prompt(self):
        assert "issue card" not in OBSERVATION_EXTRACT_PROMPT.lower(), (
            "issue card synthesis vocabulary must not leak into Stage 1 extraction prompt."
        )

    def test_quality_gate_in_extract_is_observation_gate(self):
        # The extraction quality gate (falsifiable/durable/novel/load-bearing) must remain;
        # it is the correct gate for this stage and is not equivalent to orphan logic.
        prompt = OBSERVATION_EXTRACT_PROMPT.lower()
        assert "falsifiable" in prompt or "durable" in prompt or "load-bearing" in prompt, (
            "OBSERVATION_EXTRACT_PROMPT must retain its observation-level quality gate language."
        )

    def test_behavioral_gate_in_consolidation(self):
        # CONSOLIDATION_PROMPT's behavioral gate is its quality gate for this stage.
        assert "behavioral gate" in CONSOLIDATION_PROMPT.lower() or "do something wrong" in CONSOLIDATION_PROMPT.lower(), (
            "CONSOLIDATION_PROMPT must retain its behavioral gate language."
        )


# ---------------------------------------------------------------------------
# Cross-prompt consistency
# ---------------------------------------------------------------------------


class TestCrossPromptConsistency:
    def test_kind_enum_consistent(self):
        kind_values = ["decision", "finding", "preference", "constraint", "correction", "open_question"]
        for v in kind_values:
            assert v in OBSERVATION_EXTRACT_PROMPT, f"kind value '{v}' missing from Stage 1"
            assert v in CONSOLIDATION_PROMPT, f"kind value '{v}' missing from Stage 2"

    def test_knowledge_type_enum_consistent(self):
        kt_values = ["factual", "conceptual", "procedural", "metacognitive"]
        for v in kt_values:
            assert v in OBSERVATION_EXTRACT_PROMPT, f"knowledge_type value '{v}' missing from Stage 1"
            assert v in CONSOLIDATION_PROMPT, f"knowledge_type value '{v}' missing from Stage 2"
