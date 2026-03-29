"""
Tests for the SelfReflector — self-model seeding, reflection, and updates.

All Anthropic API calls are mocked; no real network requests are made.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.lifecycle import LifecycleManager
from core.self_reflection import (
    SelfReflector, SELF_MODEL_TITLE, SELF_MODEL_SEED,
    OBSERVATION_HABIT_TITLE, OBSERVATION_HABIT_CONTENT,
    COMPACTION_GUIDANCE_TITLE, COMPACTION_GUIDANCE_CONTENT,
)
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    return MemoryStore(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def reflector(tmp_store):
    return SelfReflector(store=tmp_store, model="claude-sonnet-4-6")


def _llm_response(data: dict) -> MagicMock:
    """Build a mock Anthropic messages.create() return value."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(data))]
    return mock_msg


# ---------------------------------------------------------------------------
# Self-model seeding
# ---------------------------------------------------------------------------

class TestSeedSelfModel:
    def test_ensure_creates_when_absent(self, reflector, tmp_store):
        memory_id = reflector.ensure_self_model()
        assert memory_id is not None

        memory = tmp_store.get(memory_id)
        assert memory["stage"] == "instinctive"
        assert memory["title"] == SELF_MODEL_TITLE

    def test_ensure_returns_existing_when_present(self, reflector, tmp_store):
        first_id = reflector.ensure_self_model()
        second_id = reflector.ensure_self_model()
        assert first_id == second_id

    def test_seed_contains_known_tendencies(self, reflector, tmp_store):
        memory_id = reflector.ensure_self_model()
        memory = tmp_store.get(memory_id)
        content = memory["content"]

        assert "Over-structuring" in content
        assert "Defaulting to complexity" in content
        assert "Generating before searching" in content
        assert "Scope optimism" in content

    def test_seed_has_correct_metadata(self, reflector, tmp_store):
        memory_id = reflector.ensure_self_model()
        memory = tmp_store.get(memory_id)

        assert memory["importance"] == 0.90
        assert "self-awareness" in memory["tags"]
        assert "type:self_observation" in memory["tags"]

    def test_seed_is_in_instinctive_stage(self, reflector, tmp_store):
        memory_id = reflector.ensure_self_model()
        instinctive = tmp_store.list_by_stage("instinctive")
        assert any(m["id"] == memory_id for m in instinctive)

    def test_seed_content_has_actionable_format(self, reflector, tmp_store):
        memory_id = reflector.ensure_self_model()
        memory = tmp_store.get(memory_id)
        content = memory["content"]

        # Each tendency should have the structured format
        assert "**What I do:**" in content
        assert "**Trigger:**" in content
        assert "**Correction:**" in content
        assert "**Confidence:**" in content


# ---------------------------------------------------------------------------
# Finding self-model
# ---------------------------------------------------------------------------

class TestFindSelfModel:
    def test_find_returns_none_when_absent(self, reflector):
        result = reflector._find_self_model()
        assert result is None

    def test_find_returns_memory_when_present(self, reflector, tmp_store):
        reflector.ensure_self_model()
        result = reflector._find_self_model()
        assert result is not None
        assert result["title"] == SELF_MODEL_TITLE

    def test_find_ignores_non_self_model_instinctive(self, reflector, tmp_store):
        # Create a different instinctive memory
        tmp_store.create(
            path="workflow.md",
            content="Some workflow rule",
            metadata={
                "stage": "instinctive",
                "title": "Workflow Rule",
                "summary": "A rule about workflow",
            },
        )
        result = reflector._find_self_model()
        assert result is None


# ---------------------------------------------------------------------------
# Consolidation history
# ---------------------------------------------------------------------------

class TestConsolidationHistory:
    def test_empty_history(self, reflector, tmp_store):
        history = reflector._get_consolidation_history()
        assert history == ""

    def test_history_includes_recent_entries(self, reflector, tmp_store):
        # Create some consolidation log entries
        tmp_store.log_consolidation(
            action="kept",
            memory_id="mem-001",
            from_stage="ephemeral",
            to_stage="consolidated",
            rationale="Useful correction about API design",
            session_id="sess-001",
        )
        tmp_store.log_consolidation(
            action="pruned",
            memory_id="mem-002",
            from_stage="ephemeral",
            to_stage="ephemeral",
            rationale="Trivial task detail",
            session_id="sess-001",
        )

        history = reflector._get_consolidation_history()
        assert "KEPT" in history
        assert "PRUNED" in history
        assert "API design" in history

    def test_history_respects_session_count(self, reflector, tmp_store):
        for i in range(20):
            tmp_store.log_consolidation(
                action="kept",
                memory_id=f"mem-{i:03d}",
                from_stage="ephemeral",
                to_stage="consolidated",
                rationale=f"Entry {i}",
                session_id=f"sess-{i:03d}",
            )

        # session_count=1 → limit of 10 entries
        history = reflector._get_consolidation_history(session_count=1)
        lines = [l for l in history.strip().splitlines() if l.strip()]
        assert len(lines) == 10


# ---------------------------------------------------------------------------
# Reflect (with mocked LLM)
# ---------------------------------------------------------------------------

class TestReflect:
    def test_reflect_with_no_history_returns_empty(self, reflector):
        result = reflector.reflect()
        assert result == {"observations": [], "deprecated": []}

    def test_reflect_calls_llm_with_history(self, reflector, tmp_store):
        reflector.ensure_self_model()
        tmp_store.log_consolidation(
            action="kept",
            memory_id="mem-001",
            from_stage="ephemeral",
            to_stage="consolidated",
            rationale="I suggested PostgreSQL but SQLite was better",
            session_id="sess-001",
        )

        reflection_result = {
            "observations": [
                {
                    "tendency": "Over-engineering database choices",
                    "evidence": "Suggested PostgreSQL when SQLite sufficed",
                    "trigger": "Database selection",
                    "correction": "Check scale requirements first",
                    "confidence": 0.75,
                }
            ],
            "deprecated": [],
        }

        with patch("core.self_reflection.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _llm_response(reflection_result)
            result = reflector.reflect()

        assert len(result["observations"]) == 1
        assert result["observations"][0]["tendency"] == "Over-engineering database choices"

    def test_reflect_handles_malformed_json(self, reflector, tmp_store):
        reflector.ensure_self_model()
        tmp_store.log_consolidation(
            action="kept",
            memory_id="mem-001",
            from_stage="ephemeral",
            to_stage="consolidated",
            rationale="Some observation",
            session_id="sess-001",
        )

        bad_response = MagicMock()
        bad_response.content = [MagicMock(text="not valid json")]

        with patch("core.self_reflection.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = bad_response
            result = reflector.reflect()

        assert result == {"observations": [], "deprecated": []}


# ---------------------------------------------------------------------------
# Apply reflection
# ---------------------------------------------------------------------------

class TestApplyReflection:
    def test_apply_adds_new_observations(self, reflector, tmp_store):
        model_id = reflector.ensure_self_model()

        reflection = {
            "observations": [
                {
                    "tendency": "Rushing to implementation",
                    "evidence": "Skipped requirements gathering 3 times",
                    "trigger": "Feature requests",
                    "correction": "Ask clarifying questions first",
                    "confidence": 0.6,
                }
            ],
            "deprecated": [],
        }

        reflector.apply_reflection(reflection)

        updated = tmp_store.get(model_id)
        assert "Rushing to implementation" in updated["content"]
        assert "Ask clarifying questions first" in updated["content"]

    def test_apply_marks_deprecated_tendencies(self, reflector, tmp_store):
        model_id = reflector.ensure_self_model()

        reflection = {
            "observations": [],
            "deprecated": ["Over-structuring"],
        }

        reflector.apply_reflection(reflection)

        updated = tmp_store.get(model_id)
        assert "DEPRECATED" in updated["content"]

    def test_apply_updates_last_updated_date(self, reflector, tmp_store):
        model_id = reflector.ensure_self_model()

        reflection = {"observations": [], "deprecated": []}
        reflector.apply_reflection(reflection)

        updated = tmp_store.get(model_id)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in updated["content"]

    def test_apply_logs_consolidation_action(self, reflector, tmp_store):
        model_id = reflector.ensure_self_model()

        reflection = {
            "observations": [{"tendency": "test", "confidence": 0.5}],
            "deprecated": [],
        }
        reflector.apply_reflection(reflection)

        with sqlite3.connect(tmp_store.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM consolidation_log WHERE action = 'merged' AND memory_id = ?",
                (model_id,),
            ).fetchone()
        assert row is not None

    def test_apply_with_empty_reflection_is_safe(self, reflector, tmp_store):
        model_id = reflector.ensure_self_model()
        original = tmp_store.get(model_id)["content"]

        reflection = {"observations": [], "deprecated": []}
        reflector.apply_reflection(reflection)

        updated = tmp_store.get(model_id)
        # Content should be essentially the same (only date may differ)
        assert "## Known Tendencies" in updated["content"]


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

class TestMergeReflection:
    def test_merge_preserves_existing_content(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n\n### Old tendency\n**What I do:** Something\n"

        result = reflector._merge_reflection(
            content,
            {"observations": [], "deprecated": []},
        )
        assert "### Old tendency" in result

    def test_merge_appends_new_observations(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n"

        result = reflector._merge_reflection(
            content,
            {
                "observations": [
                    {
                        "tendency": "New pattern",
                        "trigger": "When X happens",
                        "correction": "Do Y instead",
                        "confidence": 0.8,
                        "evidence": "Saw this 5 times",
                    }
                ],
                "deprecated": [],
            },
        )
        assert "### New pattern" in result
        assert "**Trigger:** When X happens" in result
        assert "**Correction:** Do Y instead" in result

    def test_merge_marks_deprecated(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n\n### Old tendency\n**What I do:** Something\n"

        result = reflector._merge_reflection(
            content,
            {"observations": [], "deprecated": ["Old tendency"]},
        )
        assert "DEPRECATED" in result

    def test_merge_handles_missing_deprecated_section(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n"

        # Trying to deprecate something that doesn't exist should not crash
        result = reflector._merge_reflection(
            content,
            {"observations": [], "deprecated": ["Nonexistent tendency"]},
        )
        assert "DEPRECATED" not in result


# ---------------------------------------------------------------------------
# Parse response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_parse_clean_json(self, reflector):
        raw = json.dumps({
            "observations": [{"tendency": "test", "confidence": 0.5}],
            "deprecated": [],
        })
        result = reflector._parse_response(raw)
        assert len(result["observations"]) == 1

    def test_parse_json_with_markdown_fences(self, reflector):
        raw = "```json\n" + json.dumps({
            "observations": [],
            "deprecated": ["old thing"],
        }) + "\n```"
        result = reflector._parse_response(raw)
        assert result["deprecated"] == ["old thing"]

    def test_parse_missing_keys_returns_defaults(self, reflector):
        raw = json.dumps({"something_else": True})
        result = reflector._parse_response(raw)
        assert result == {"observations": [], "deprecated": []}


# ---------------------------------------------------------------------------
# Observation habit
# ---------------------------------------------------------------------------

class TestObservationHabit:
    def test_ensure_creates_when_absent(self, reflector, tmp_store):
        memory_id = reflector.ensure_observation_habit()
        assert memory_id is not None

        memory = tmp_store.get(memory_id)
        assert memory["stage"] == "instinctive"
        assert memory["title"] == OBSERVATION_HABIT_TITLE

    def test_ensure_returns_existing_when_present(self, reflector):
        first_id = reflector.ensure_observation_habit()
        second_id = reflector.ensure_observation_habit()
        assert first_id == second_id

    def test_habit_contains_observation_guidance(self, reflector, tmp_store):
        memory_id = reflector.ensure_observation_habit()
        memory = tmp_store.get(memory_id)
        content = memory["content"]

        assert "Corrections" in content
        assert "Preference signals" in content
        assert "Self-observations" in content
        assert "/memesis:learn" in content

    def test_habit_has_correct_metadata(self, reflector, tmp_store):
        memory_id = reflector.ensure_observation_habit()
        memory = tmp_store.get(memory_id)

        assert memory["importance"] == 0.85
        assert "meta-cognition" in memory["tags"]
        assert "type:workflow_pattern" in memory["tags"]


# ---------------------------------------------------------------------------
# Instinctive layer seeding
# ---------------------------------------------------------------------------

class TestInstinctiveLayer:
    def test_ensure_instinctive_layer_creates_all(self, reflector, tmp_store):
        result = reflector.ensure_instinctive_layer()
        assert SELF_MODEL_TITLE in result
        assert OBSERVATION_HABIT_TITLE in result
        assert COMPACTION_GUIDANCE_TITLE in result

        instinctive = tmp_store.list_by_stage("instinctive")
        titles = {m["title"] for m in instinctive}
        assert SELF_MODEL_TITLE in titles
        assert OBSERVATION_HABIT_TITLE in titles
        assert COMPACTION_GUIDANCE_TITLE in titles

    def test_ensure_instinctive_layer_idempotent(self, reflector):
        first = reflector.ensure_instinctive_layer()
        second = reflector.ensure_instinctive_layer()
        assert first == second

    def test_both_injected_at_session_start(self, reflector, tmp_store):
        """Both instinctive memories should be picked up by the retrieval engine."""
        from core.retrieval import RetrievalEngine

        reflector.ensure_instinctive_layer()
        engine = RetrievalEngine(tmp_store)

        injected = engine.inject_for_session("test-session")
        assert "Self-Model" in injected
        assert "Observation Habit" in injected
