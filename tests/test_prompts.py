"""
Tests for core/prompts.py — Wave 2 WS-D prompt rewrite verification.

Asserts:
- OBSERVATION_EXTRACT_PROMPT (Stage 1) contains required language fragments
- CONSOLIDATION_PROMPT (Stage 2) contains required language fragments
- CONCEPT_TAGS dict is removed from the module
- Both prompts format without KeyError given mock data
- Skip protocol is documented in Stage 1
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.prompts as prompts_module
from core.prompts import CONSOLIDATION_PROMPT, OBSERVATION_EXTRACT_PROMPT


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
        # Smoke test: no KeyError (session_type added in Sprint B WS-G)
        formatted = OBSERVATION_EXTRACT_PROMPT.format(
            transcript="mock session content",
            session_type="code",
        )
        assert "mock session content" in formatted


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
        lower = CONSOLIDATION_PROMPT.lower()
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
