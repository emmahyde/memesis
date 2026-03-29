"""
Tests for the SelfReflector — self-model seeding, reflection, and updates.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.lifecycle import LifecycleManager
from core.models import Memory, ConsolidationLog, db
from core.self_reflection import (
    SelfReflector, SELF_MODEL_TITLE, SELF_MODEL_SEED,
    OBSERVATION_HABIT_TITLE, OBSERVATION_HABIT_CONTENT,
    COMPACTION_GUIDANCE_TITLE, COMPACTION_GUIDANCE_CONTENT,
)


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def reflector(base):
    return SelfReflector(model="claude-sonnet-4-6")


def _llm_response(data: dict) -> str:
    return json.dumps(data)


class TestSeedSelfModel:
    def test_ensure_creates_when_absent(self, reflector, base):
        memory_id = reflector.ensure_self_model()
        assert memory_id is not None
        mem = Memory.get_by_id(memory_id)
        assert mem.stage == "instinctive"
        assert mem.title == SELF_MODEL_TITLE

    def test_ensure_returns_existing_when_present(self, reflector):
        first_id = reflector.ensure_self_model()
        second_id = reflector.ensure_self_model()
        assert first_id == second_id

    def test_seed_contains_known_tendencies(self, reflector, base):
        memory_id = reflector.ensure_self_model()
        content = Memory.get_by_id(memory_id).content
        assert "Over-structuring" in content
        assert "Defaulting to complexity" in content

    def test_seed_has_correct_metadata(self, reflector, base):
        memory_id = reflector.ensure_self_model()
        mem = Memory.get_by_id(memory_id)
        assert mem.importance == 0.90
        assert "self-awareness" in mem.tag_list

    def test_seed_is_in_instinctive_stage(self, reflector, base):
        memory_id = reflector.ensure_self_model()
        instinctive = list(Memory.by_stage("instinctive"))
        assert any(m.id == memory_id for m in instinctive)

    def test_seed_content_has_actionable_format(self, reflector, base):
        memory_id = reflector.ensure_self_model()
        content = Memory.get_by_id(memory_id).content
        assert "**What I do:**" in content
        assert "**Trigger:**" in content


class TestFindSelfModel:
    def test_find_returns_none_when_absent(self, reflector):
        assert reflector._find_self_model() is None

    def test_find_returns_memory_when_present(self, reflector, base):
        reflector.ensure_self_model()
        result = reflector._find_self_model()
        assert result is not None
        assert result.title == SELF_MODEL_TITLE

    def test_find_ignores_non_self_model_instinctive(self, reflector, base):
        now = datetime.now().isoformat()
        Memory.create(stage="instinctive", title="Workflow Rule", summary="A rule", content="Rule", tags="[]", created_at=now, updated_at=now)
        assert reflector._find_self_model() is None


class TestConsolidationHistory:
    def test_empty_history(self, reflector, base):
        assert reflector._get_consolidation_history() == ""

    def test_history_includes_recent_entries(self, reflector, base):
        now = datetime.now().isoformat()
        ConsolidationLog.create(timestamp=now, action="kept", memory_id="mem-001", from_stage="ephemeral", to_stage="consolidated", rationale="Useful correction about API design", session_id="sess-001")
        ConsolidationLog.create(timestamp=now, action="pruned", memory_id="mem-002", from_stage="ephemeral", to_stage="ephemeral", rationale="Trivial task detail", session_id="sess-001")
        history = reflector._get_consolidation_history()
        assert "KEPT" in history
        assert "PRUNED" in history
        assert "API design" in history

    def test_history_respects_session_count(self, reflector, base):
        now = datetime.now().isoformat()
        for i in range(20):
            ConsolidationLog.create(timestamp=now, action="kept", memory_id=f"mem-{i:03d}", from_stage="ephemeral", to_stage="consolidated", rationale=f"Entry {i}", session_id=f"sess-{i:03d}")
        history = reflector._get_consolidation_history(session_count=1)
        lines = [l for l in history.strip().splitlines() if l.strip()]
        assert len(lines) == 10


class TestReflect:
    def test_reflect_with_no_history_returns_empty(self, reflector):
        assert reflector.reflect() == {"observations": [], "deprecated": []}

    def test_reflect_calls_llm_with_history(self, reflector, base):
        reflector.ensure_self_model()
        now = datetime.now().isoformat()
        ConsolidationLog.create(timestamp=now, action="kept", memory_id="mem-001", from_stage="ephemeral", to_stage="consolidated", rationale="I suggested PostgreSQL but SQLite was better", session_id="sess-001")
        reflection_result = {"observations": [{"tendency": "Over-engineering database choices", "evidence": "Suggested PostgreSQL when SQLite sufficed", "trigger": "Database selection", "correction": "Check scale requirements first", "confidence": 0.75}], "deprecated": []}
        with patch("core.self_reflection._call_llm_transport") as mock:
            mock.return_value = _llm_response(reflection_result)
            result = reflector.reflect()
        assert len(result["observations"]) == 1

    def test_reflect_handles_malformed_json(self, reflector, base):
        reflector.ensure_self_model()
        now = datetime.now().isoformat()
        ConsolidationLog.create(timestamp=now, action="kept", memory_id="mem-001", from_stage="ephemeral", to_stage="consolidated", rationale="Some observation", session_id="sess-001")
        with patch("core.self_reflection._call_llm_transport") as mock:
            mock.return_value = "not valid json"
            result = reflector.reflect()
        assert result == {"observations": [], "deprecated": []}


class TestApplyReflection:
    def test_apply_adds_new_observations(self, reflector, base):
        model_id = reflector.ensure_self_model()
        reflector.apply_reflection({"observations": [{"tendency": "Rushing to implementation", "evidence": "Skipped requirements 3 times", "trigger": "Feature requests", "correction": "Ask clarifying questions first", "confidence": 0.6}], "deprecated": []})
        updated = Memory.get_by_id(model_id)
        assert "Rushing to implementation" in updated.content

    def test_apply_marks_deprecated_tendencies(self, reflector, base):
        model_id = reflector.ensure_self_model()
        reflector.apply_reflection({"observations": [], "deprecated": ["Over-structuring"]})
        assert "DEPRECATED" in Memory.get_by_id(model_id).content

    def test_apply_logs_consolidation_action(self, reflector, base):
        model_id = reflector.ensure_self_model()
        reflector.apply_reflection({"observations": [{"tendency": "test", "confidence": 0.5}], "deprecated": []})
        row = ConsolidationLog.get_or_none((ConsolidationLog.action == "merged") & (ConsolidationLog.memory_id == model_id))
        assert row is not None

    def test_apply_with_empty_reflection_is_safe(self, reflector, base):
        model_id = reflector.ensure_self_model()
        reflector.apply_reflection({"observations": [], "deprecated": []})
        assert "## Known Tendencies" in Memory.get_by_id(model_id).content


class TestMergeReflection:
    def test_merge_preserves_existing_content(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n\n### Old tendency\n**What I do:** Something\n"
        result = reflector._merge_reflection(content, {"observations": [], "deprecated": []})
        assert "### Old tendency" in result

    def test_merge_appends_new_observations(self, reflector):
        content = "# Self-Model\n\nLast updated: 2026-03-27\n\n## Known Tendencies\n"
        result = reflector._merge_reflection(content, {"observations": [{"tendency": "New pattern", "trigger": "When X happens", "correction": "Do Y instead", "confidence": 0.8, "evidence": "Saw this 5 times"}], "deprecated": []})
        assert "### New pattern" in result


class TestParseResponse:
    def test_parse_clean_json(self, reflector):
        raw = json.dumps({"observations": [{"tendency": "test", "confidence": 0.5}], "deprecated": []})
        result = reflector._parse_response(raw)
        assert len(result["observations"]) == 1

    def test_parse_json_with_markdown_fences(self, reflector):
        raw = "```json\n" + json.dumps({"observations": [], "deprecated": ["old thing"]}) + "\n```"
        result = reflector._parse_response(raw)
        assert result["deprecated"] == ["old thing"]

    def test_parse_missing_keys_returns_defaults(self, reflector):
        raw = json.dumps({"something_else": True})
        result = reflector._parse_response(raw)
        assert result == {"observations": [], "deprecated": []}


class TestObservationHabit:
    def test_ensure_creates_when_absent(self, reflector, base):
        memory_id = reflector.ensure_observation_habit()
        assert memory_id is not None
        mem = Memory.get_by_id(memory_id)
        assert mem.stage == "instinctive"
        assert mem.title == OBSERVATION_HABIT_TITLE

    def test_ensure_returns_existing_when_present(self, reflector):
        assert reflector.ensure_observation_habit() == reflector.ensure_observation_habit()

    def test_habit_contains_observation_guidance(self, reflector, base):
        memory_id = reflector.ensure_observation_habit()
        content = Memory.get_by_id(memory_id).content
        assert "Corrections" in content
        assert "Preference signals" in content

    def test_habit_has_correct_metadata(self, reflector, base):
        memory_id = reflector.ensure_observation_habit()
        mem = Memory.get_by_id(memory_id)
        assert mem.importance == 0.85
        assert "meta-cognition" in mem.tag_list


class TestInstinctiveLayer:
    def test_ensure_instinctive_layer_creates_all(self, reflector, base):
        result = reflector.ensure_instinctive_layer()
        assert SELF_MODEL_TITLE in result
        assert OBSERVATION_HABIT_TITLE in result
        assert COMPACTION_GUIDANCE_TITLE in result
        titles = {m.title for m in Memory.by_stage("instinctive")}
        assert SELF_MODEL_TITLE in titles
        assert OBSERVATION_HABIT_TITLE in titles

    def test_ensure_instinctive_layer_idempotent(self, reflector):
        assert reflector.ensure_instinctive_layer() == reflector.ensure_instinctive_layer()

    def test_both_injected_at_session_start(self, reflector, base):
        from core.retrieval import RetrievalEngine
        reflector.ensure_instinctive_layer()
        engine = RetrievalEngine()
        injected = engine.inject_for_session("test-session")
        assert "Self-Model" in injected
        assert "Observation Habit" in injected
