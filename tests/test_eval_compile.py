"""
Tests for core/eval_compile.py.

Coverage:
- EvalSpec dataclass fields and defaults
- extract_spec_from_text patches core.eval_compile.call_llm
- Each of the four match modes renders syntactically valid Python (compile() builtin)
- absence mode inverts the entity_presence assertion
- LLM fallback stub contains the required TODO comment
"""

import json
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.eval_compile import (
    EvalSpec,
    compile_to_pytest,
    extract_spec_from_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(
    slug="test-slug",
    expected_entities=None,
    polarity=None,
    stage_target=None,
    match_mode="entity_presence",
) -> EvalSpec:
    return EvalSpec(
        slug=slug,
        expected_entities=expected_entities or ["oauth", "token"],
        polarity=polarity,
        stage_target=stage_target,
        match_mode=match_mode,
    )


def _compile_and_check(spec: EvalSpec, replay_path: str = "/tmp/replay") -> str:
    """Compile to pytest source and assert it is syntactically valid."""
    src = compile_to_pytest(spec, replay_path)
    assert isinstance(src, str) and len(src) > 0, "compile_to_pytest returned empty string"
    # Verify it parses as valid Python
    code = compile(src, "<test>", "exec")
    assert code is not None
    return src


# ---------------------------------------------------------------------------
# EvalSpec dataclass
# ---------------------------------------------------------------------------

class TestEvalSpecDataclass:
    def test_fields_present(self):
        spec = EvalSpec(
            slug="my-slug",
            expected_entities=["foo", "bar"],
            polarity="positive",
            stage_target="consolidated",
            match_mode="entity_presence",
        )
        assert spec.slug == "my-slug"
        assert spec.expected_entities == ["foo", "bar"]
        assert spec.polarity == "positive"
        assert spec.stage_target == "consolidated"
        assert spec.match_mode == "entity_presence"

    def test_polarity_nullable(self):
        spec = _make_spec(polarity=None)
        assert spec.polarity is None

    def test_stage_target_nullable(self):
        spec = _make_spec(stage_target=None)
        assert spec.stage_target is None

    def test_description_optional(self):
        spec = EvalSpec(
            slug="x",
            expected_entities=[],
            polarity=None,
            stage_target=None,
            match_mode="absence",
        )
        assert spec.description == ""

    def test_valid_match_modes(self):
        for mode in ("entity_presence", "semantic_similarity", "polarity_match", "absence"):
            spec = _make_spec(match_mode=mode)
            assert spec.match_mode == mode


# ---------------------------------------------------------------------------
# extract_spec_from_text — patches core.eval_compile.call_llm
# ---------------------------------------------------------------------------

class TestExtractSpecFromText:
    def _mock_llm_response(self, data: dict) -> str:
        return json.dumps(data)

    def test_basic_extraction(self):
        response = self._mock_llm_response({
            "slug": "oauth-token-expiry",
            "expected_entities": ["OAuth", "token", "expiry"],
            "polarity": None,
            "stage_target": "consolidated",
            "match_mode": "entity_presence",
        })
        with patch("core.eval_compile.call_llm", return_value=response) as mock_llm:
            spec = extract_spec_from_text("Memory about OAuth token expiry consolidated")
            mock_llm.assert_called_once()

        assert spec.slug == "oauth-token-expiry"
        assert "OAuth" in spec.expected_entities
        assert spec.stage_target == "consolidated"
        assert spec.match_mode == "entity_presence"
        assert spec.polarity is None

    def test_absence_mode_extraction(self):
        response = self._mock_llm_response({
            "slug": "no-plain-password",
            "expected_entities": ["password"],
            "polarity": None,
            "stage_target": None,
            "match_mode": "absence",
        })
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("No memory should contain plain passwords")

        assert spec.match_mode == "absence"
        assert spec.slug == "no-plain-password"

    def test_polarity_mode_extraction(self):
        response = self._mock_llm_response({
            "slug": "friction-deploy",
            "expected_entities": ["deploy", "CI"],
            "polarity": "friction",
            "stage_target": None,
            "match_mode": "polarity_match",
        })
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("Memory about deploy friction")

        assert spec.match_mode == "polarity_match"
        assert spec.polarity == "friction"

    def test_slug_sanitized(self):
        """Slug with spaces/special chars is normalized."""
        response = self._mock_llm_response({
            "slug": "My Slug!! With Spaces",
            "expected_entities": ["foo"],
            "polarity": None,
            "stage_target": None,
            "match_mode": "entity_presence",
        })
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("test")

        assert " " not in spec.slug
        assert "!" not in spec.slug

    def test_slug_truncated_to_40_chars(self):
        long_slug = "a" * 100
        response = self._mock_llm_response({
            "slug": long_slug,
            "expected_entities": ["x"],
            "polarity": None,
            "stage_target": None,
            "match_mode": "entity_presence",
        })
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("test")

        assert len(spec.slug) <= 40

    def test_invalid_match_mode_falls_back(self):
        response = self._mock_llm_response({
            "slug": "test",
            "expected_entities": ["x"],
            "polarity": None,
            "stage_target": None,
            "match_mode": "some_unknown_mode",
        })
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("test")

        assert spec.match_mode == "entity_presence"

    def test_json_wrapped_in_text(self):
        """LLM response with surrounding text still parses."""
        response = 'Here is the JSON:\n{"slug": "wrapped", "expected_entities": ["foo"], "polarity": null, "stage_target": null, "match_mode": "entity_presence"}'
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text("test")

        assert spec.slug == "wrapped"

    def test_invalid_json_raises_value_error(self):
        with patch("core.eval_compile.call_llm", return_value="not json at all"):
            with pytest.raises(ValueError, match="JSON"):
                extract_spec_from_text("test")

    def test_description_stored_on_spec(self):
        response = self._mock_llm_response({
            "slug": "x",
            "expected_entities": [],
            "polarity": None,
            "stage_target": None,
            "match_mode": "entity_presence",
        })
        desc = "A specific test description"
        with patch("core.eval_compile.call_llm", return_value=response):
            spec = extract_spec_from_text(desc)

        assert spec.description == desc


# ---------------------------------------------------------------------------
# compile_to_pytest — syntactic validity via compile() builtin
# ---------------------------------------------------------------------------

class TestCompileToPytestSyntax:
    """All four match modes must produce syntactically valid Python."""

    def test_entity_presence_valid_python(self):
        spec = _make_spec(match_mode="entity_presence")
        _compile_and_check(spec)

    def test_absence_valid_python(self):
        spec = _make_spec(match_mode="absence")
        _compile_and_check(spec)

    def test_polarity_match_valid_python(self):
        spec = _make_spec(match_mode="polarity_match", polarity="friction")
        _compile_and_check(spec)

    def test_semantic_similarity_valid_python(self):
        spec = _make_spec(match_mode="semantic_similarity")
        _compile_and_check(spec)

    def test_filename_pattern_in_source(self):
        """Compiled source should work as a <slug>_recall.py file."""
        spec = _make_spec(slug="deploy-policy")
        src = compile_to_pytest(spec, "/tmp/replay")
        # The test function must be discoverable by pytest
        assert "def test_" in src

    def test_replay_store_path_embedded(self):
        spec = _make_spec()
        src = compile_to_pytest(spec, "/custom/replay/path")
        assert "/custom/replay/path" in src

    def test_init_db_called_in_source(self):
        spec = _make_spec()
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "init_db" in src

    def test_memory_select_in_source(self):
        spec = _make_spec()
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "Memory.select()" in src

    def test_entities_embedded(self):
        spec = _make_spec(expected_entities=["oauth", "token", "expiry"])
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "oauth" in src
        assert "token" in src
        assert "expiry" in src


# ---------------------------------------------------------------------------
# Absence mode inverts assertion
# ---------------------------------------------------------------------------

class TestAbsenceModeInversion:
    def test_absence_asserts_not_found(self):
        """Absence mode must contain a negative assertion."""
        spec = _make_spec(match_mode="absence")
        src = compile_to_pytest(spec, "/tmp/replay")
        # The absence check uses `not matches` or `assert not`
        assert "assert not matches" in src or "not found" in src.lower() or "absent" in src.lower()

    def test_entity_presence_asserts_found(self):
        """entity_presence mode must contain a positive assertion."""
        spec = _make_spec(match_mode="entity_presence")
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "assert any(" in src

    def test_absence_and_presence_are_inverted(self):
        """Compile both modes and verify they contain different assertion logic."""
        spec_presence = _make_spec(match_mode="entity_presence", slug="test-presence")
        spec_absence = _make_spec(match_mode="absence", slug="test-absence")

        src_presence = compile_to_pytest(spec_presence, "/tmp/replay")
        src_absence = compile_to_pytest(spec_absence, "/tmp/replay")

        # Presence uses `any(...)` affirmation; absence uses `not matches`
        assert "assert any(" in src_presence
        assert "assert not matches" in src_absence

    def test_absence_mentions_should_be_absent(self):
        spec = _make_spec(match_mode="absence", slug="no-plain-password")
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "absent" in src.lower()


# ---------------------------------------------------------------------------
# LLM fallback stub
# ---------------------------------------------------------------------------

class TestLLMFallbackStub:
    def test_semantic_similarity_contains_todo_comment(self):
        """semantic_similarity mode has the required TODO fallback comment."""
        spec = _make_spec(match_mode="semantic_similarity")
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "# TODO: LLM-generated assertion fallback" in src


# ---------------------------------------------------------------------------
# Stage target filtering
# ---------------------------------------------------------------------------

class TestStageTargetFiltering:
    def test_stage_filter_in_entity_presence(self):
        spec = _make_spec(match_mode="entity_presence", stage_target="crystallized")
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "crystallized" in src

    def test_no_stage_filter_when_none(self):
        spec = _make_spec(match_mode="entity_presence", stage_target=None)
        src = compile_to_pytest(spec, "/tmp/replay")
        # Should not contain a stage filter line
        assert "Memory.stage ==" not in src

    def test_stage_filter_in_absence(self):
        spec = _make_spec(match_mode="absence", stage_target="ephemeral")
        src = compile_to_pytest(spec, "/tmp/replay")
        assert "ephemeral" in src
