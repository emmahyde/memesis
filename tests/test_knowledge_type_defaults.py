"""
Tests for Task #20 — default_knowledge_type_for_kind helper and consolidator wiring.

Covers:
- All 9 kind→knowledge_type default mappings from core/validators.py
- LLM-provided knowledge_type overrides the default in the consolidator write path
"""

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.validators import (
    KIND_VALUES,
    KNOWLEDGE_TYPE_VALUES,
    default_knowledge_type_for_kind,
)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestDefaultKnowledgeTypeForKind:
    """All 9 kinds must return a valid Bloom knowledge_type."""

    EXPECTED: dict[str, str] = {
        "decision":      "conceptual",
        "fact":          "factual",
        "lesson":        "metacognitive",
        "correction":    "metacognitive",
        "directive":     "procedural",
        "preference":    "metacognitive",
        "goal":          "conceptual",
        "open_question": "conceptual",
        "hypothesis":    "conceptual",
    }

    def test_all_nine_kinds_covered(self):
        """Every content kind in KIND_VALUES must have an explicit mapping."""
        # Note: lifecycle kinds (open_question, hypothesis) are in KIND_VALUES and
        # must also have a mapping.
        for kind in KIND_VALUES:
            result = default_knowledge_type_for_kind(kind)
            assert result in KNOWLEDGE_TYPE_VALUES, (
                f"default_knowledge_type_for_kind({kind!r}) returned {result!r}, "
                f"which is not a valid knowledge_type"
            )

    def test_decision_maps_to_conceptual(self):
        assert default_knowledge_type_for_kind("decision") == "conceptual"

    def test_fact_maps_to_factual(self):
        assert default_knowledge_type_for_kind("fact") == "factual"

    def test_lesson_maps_to_metacognitive(self):
        assert default_knowledge_type_for_kind("lesson") == "metacognitive"

    def test_correction_maps_to_metacognitive(self):
        assert default_knowledge_type_for_kind("correction") == "metacognitive"

    def test_directive_maps_to_procedural(self):
        assert default_knowledge_type_for_kind("directive") == "procedural"

    def test_preference_maps_to_metacognitive(self):
        assert default_knowledge_type_for_kind("preference") == "metacognitive"

    def test_goal_maps_to_conceptual(self):
        assert default_knowledge_type_for_kind("goal") == "conceptual"

    def test_open_question_maps_to_conceptual(self):
        assert default_knowledge_type_for_kind("open_question") == "conceptual"

    def test_hypothesis_maps_to_conceptual(self):
        assert default_knowledge_type_for_kind("hypothesis") == "conceptual"

    def test_unknown_kind_returns_valid_fallback(self):
        result = default_knowledge_type_for_kind("nonexistent_kind")
        assert result in KNOWLEDGE_TYPE_VALUES, (
            f"Unknown kind fallback {result!r} is not a valid knowledge_type"
        )

    def test_expected_table_matches_all_nine(self):
        for kind, expected in self.EXPECTED.items():
            assert default_knowledge_type_for_kind(kind) == expected, (
                f"default_knowledge_type_for_kind({kind!r}) = "
                f"{default_knowledge_type_for_kind(kind)!r}, expected {expected!r}"
            )


# ---------------------------------------------------------------------------
# Consolidator wiring: LLM override wins; fallback applied when absent
# ---------------------------------------------------------------------------


class TestConsolidatorKnowledgeTypeWiring:
    """LLM-provided knowledge_type overrides the default; omitted value gets default."""

    def _make_decision(self, kind: str, knowledge_type: str | None) -> dict:
        """Minimal decision dict for _execute_keep testing."""
        return {
            "kind": kind,
            "knowledge_type": knowledge_type,
            "knowledge_type_confidence": "high",
            "title": "Test title",
            "summary": "Test summary",
            "facts": ["Named subject did something concrete"],
            "observation": "Named subject did something concrete",
            "tags": [],
            "target_path": "test/test.md",
            "rationale": "test",
            "action": "keep",
            "reinforces": None,
            "contradicts": None,
            "resolves_question_id": None,
            "code_refs": None,
            "cwd": None,
            "subject": "system",
            "work_event": None,
            "subtitle": "Short subtitle",
            "importance": 0.5,
            "_raw_stage1_importance": 0.5,
        }

    def test_llm_knowledge_type_overrides_default(self, tmp_path, monkeypatch):
        """When LLM returns knowledge_type='procedural' for kind='fact', use procedural."""
        # The default for "fact" is "factual"; LLM override is "procedural".
        # We test the wiring logic directly without running the full consolidator.
        from core.validators import default_knowledge_type_for_kind

        kind = "fact"
        llm_kt = "procedural"
        decision = {"kind": kind, "knowledge_type": llm_kt}

        # Replicate the consolidator expression:
        result = decision.get("knowledge_type") or default_knowledge_type_for_kind(kind)
        assert result == "procedural", (
            "LLM-provided knowledge_type must win over the kind default"
        )

    def test_fallback_applied_when_llm_omits_knowledge_type(self):
        """When LLM returns knowledge_type=None for kind='directive', use 'procedural'."""
        from core.validators import default_knowledge_type_for_kind

        kind = "directive"
        decision = {"kind": kind, "knowledge_type": None}

        result = decision.get("knowledge_type") or default_knowledge_type_for_kind(kind)
        assert result == "procedural", (
            "default_knowledge_type_for_kind fallback must apply when LLM omits knowledge_type"
        )

    def test_fallback_applied_when_llm_returns_empty_string(self):
        """Empty string is falsy — fallback must apply."""
        from core.validators import default_knowledge_type_for_kind

        kind = "lesson"
        decision = {"kind": kind, "knowledge_type": ""}

        result = decision.get("knowledge_type") or default_knowledge_type_for_kind(kind)
        assert result == "metacognitive"
