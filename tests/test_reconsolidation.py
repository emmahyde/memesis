"""Tests for reconsolidation — memory updates from session evidence."""

import json
from unittest.mock import patch

import pytest

from core.database import init_db, close_db
from core.models import Memory, ConsolidationLog
from core.reconsolidation import reconsolidate


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


@pytest.fixture
def memories(store):
    m1 = Memory.create(
        stage="crystallized",
        title="User prefers snake_case",
        content="The user always uses snake_case in Python code.",
        importance=0.7,
        reinforcement_count=3,
    )
    m2 = Memory.create(
        stage="crystallized",
        title="Deploy window is Friday",
        content="Deployments happen on Fridays.",
        importance=0.6,
        reinforcement_count=1,
    )
    return [m1, m2]


class TestReconsolidation:

    def test_confirmed_bumps_reinforcement(self, memories):
        llm_response = json.dumps([
            {"memory_id": memories[0].id, "action": "confirmed", "evidence": "User used snake_case throughout"},
            {"memory_id": memories[1].id, "action": "unmentioned", "evidence": ""},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate(
                [m.id for m in memories],
                "Some session about python coding with snake_case",
                "test-session",
            )
        assert memories[0].id in result["confirmed"]
        fresh = Memory.get_by_id(memories[0].id)
        assert fresh.reinforcement_count == 4  # was 3

    def test_contradicted_flags_memory(self, memories):
        llm_response = json.dumps([
            {"memory_id": memories[1].id, "action": "contradicted", "evidence": "Deploy window moved to Tuesday"},
            {"memory_id": memories[0].id, "action": "unmentioned", "evidence": ""},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate(
                [m.id for m in memories],
                "We changed deploy window to Tuesday",
                "test-session",
            )
        assert memories[1].id in result["contradicted"]
        fresh = Memory.get_by_id(memories[1].id)
        assert "contradiction_flagged" in fresh.tag_list

    def test_refined_appends_to_content(self, memories):
        llm_response = json.dumps([
            {"memory_id": memories[0].id, "action": "refined", "evidence": "Also uses camelCase for JS"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate(
                [m.id for m in memories],
                "In JS files, user uses camelCase",
                "test-session",
            )
        assert memories[0].id in result["refined"]
        fresh = Memory.get_by_id(memories[0].id)
        assert "camelCase" in fresh.content

    def test_unmentioned_no_changes(self, memories):
        llm_response = json.dumps([
            {"memory_id": memories[0].id, "action": "unmentioned", "evidence": ""},
            {"memory_id": memories[1].id, "action": "unmentioned", "evidence": ""},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate(
                [m.id for m in memories],
                "Unrelated session about databases",
                "test-session",
            )
        assert result == {"confirmed": [], "contradicted": [], "refined": []}

    def test_empty_ids_returns_empty(self, store):
        result = reconsolidate([], "some content", "test-session")
        assert result == {"confirmed": [], "contradicted": [], "refined": []}

    def test_empty_content_returns_empty(self, memories):
        result = reconsolidate([m.id for m in memories], "", "test-session")
        assert result == {"confirmed": [], "contradicted": [], "refined": []}

    def test_llm_failure_returns_empty(self, memories):
        with patch("core.reconsolidation.call_llm", side_effect=Exception("API error")):
            result = reconsolidate(
                [m.id for m in memories],
                "some content",
                "test-session",
            )
        assert result == {"confirmed": [], "contradicted": [], "refined": []}

    def test_contradicted_hypothesis_decays_evidence_count(self, store):
        hyp = Memory.create(
            stage="consolidated",
            kind="hypothesis",
            title="User prefers short PRs",
            content="Based on observations, user slices PRs by abstraction layer.",
            importance=0.6,
            evidence_count=2,
        )
        llm_response = json.dumps([
            {"memory_id": hyp.id, "action": "contradicted", "evidence": "User merged a large omnibus PR"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate([hyp.id], "User merged a large omnibus PR", "s1")
        assert hyp.id in result["contradicted"]
        fresh = Memory.get_by_id(hyp.id)
        assert fresh.evidence_count == 1  # decremented from 2

    def test_contradicted_hypothesis_demotes_at_zero(self, store):
        hyp = Memory.create(
            stage="consolidated",
            kind="hypothesis",
            title="User always uses type hints",
            content="Observed pattern: every function has return type annotations.",
            importance=0.55,
            evidence_count=1,
        )
        llm_response = json.dumps([
            {"memory_id": hyp.id, "action": "contradicted", "evidence": "User wrote a function with no type hints"},
        ])
        with patch("core.reconsolidation.call_llm", return_value=llm_response):
            result = reconsolidate([hyp.id], "User wrote function with no hints", "s2")
        assert hyp.id in result["contradicted"]
        fresh = Memory.get_by_id(hyp.id)
        assert fresh.evidence_count == 0
        assert fresh.stage == "ephemeral"
        assert fresh.kind is None

    def test_flag_disabled_returns_empty(self, memories, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"reconsolidation": False})
        result = reconsolidate(
            [m.id for m in memories],
            "User used snake_case everywhere",
            "test-session",
        )
        assert result == {"confirmed": [], "contradicted": [], "refined": []}

    def test_batched_single_llm_call(self, memories):
        """Verify only one LLM call for multiple memories."""
        call_count = 0
        original_response = json.dumps([
            {"memory_id": memories[0].id, "action": "confirmed", "evidence": "yes"},
            {"memory_id": memories[1].id, "action": "confirmed", "evidence": "yes"},
        ])

        def counting_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_response

        with patch("core.reconsolidation.call_llm", side_effect=counting_llm):
            reconsolidate(
                [m.id for m in memories],
                "session content",
                "test-session",
            )
        assert call_count == 1
