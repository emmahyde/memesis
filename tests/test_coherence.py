"""Tests for ghost coherence check — self-model vs evidence validation."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.database import init_db, close_db
from core.models import Memory
from core.coherence import check_coherence, _is_rate_limited, _record_check


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


@pytest.fixture
def setup_memories(store):
    """Create instinctive claims and consolidated evidence."""
    claim = Memory.create(
        stage="instinctive",
        title="User prefers snake_case",
        content="The user always uses snake_case in Python.",
        importance=0.9,
        updated_at=datetime.now().isoformat(),
    )
    evidence = Memory.create(
        stage="consolidated",
        title="Coding style observation",
        content="User consistently used snake_case across 5 sessions.",
        importance=0.6,
        updated_at=datetime.now().isoformat(),
    )
    return claim, evidence


class TestGhostCoherence:

    def test_consistent_claim(self, setup_memories):
        claim, _ = setup_memories
        llm_response = json.dumps([
            {"claim_id": claim.id, "status": "consistent", "evidence": "Evidence supports snake_case preference"},
        ])
        with patch("core.coherence.call_llm", return_value=llm_response):
            result = check_coherence()
        assert len(result["consistent"]) == 1
        assert result["consistent"][0]["id"] == claim.id

    def test_divergent_claim_flags_memory(self, setup_memories):
        claim, _ = setup_memories
        llm_response = json.dumps([
            {"claim_id": claim.id, "status": "divergent", "evidence": "User switched to camelCase"},
        ])
        with patch("core.coherence.call_llm", return_value=llm_response):
            result = check_coherence()
        assert len(result["divergent"]) == 1
        fresh = Memory.get_by_id(claim.id)
        assert "coherence_divergent" in fresh.tag_list

    def test_unsupported_claim(self, setup_memories):
        claim, _ = setup_memories
        llm_response = json.dumps([
            {"claim_id": claim.id, "status": "unsupported", "evidence": "No recent evidence either way"},
        ])
        with patch("core.coherence.call_llm", return_value=llm_response):
            result = check_coherence()
        assert len(result["unsupported"]) == 1

    def test_llm_failure_returns_empty(self, setup_memories):
        with patch("core.coherence.call_llm", side_effect=Exception("API error")):
            result = check_coherence()
        assert result["consistent"] == []
        assert result["divergent"] == []

    def test_no_claims_returns_empty(self, store):
        # Only evidence, no instinctive claims
        Memory.create(
            stage="consolidated", title="Evidence",
            content="Something", importance=0.5,
            updated_at=datetime.now().isoformat(),
        )
        result = check_coherence()
        assert result["checked_at"] is None

    def test_flag_disabled_returns_empty(self, setup_memories, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"ghost_coherence": False})
        result = check_coherence()
        assert result["checked_at"] is None

    def test_single_llm_call(self, setup_memories):
        claim, _ = setup_memories
        call_count = 0
        resp = json.dumps([{"claim_id": claim.id, "status": "consistent", "evidence": "ok"}])

        def counting(*a, **kw):
            nonlocal call_count
            call_count += 1
            return resp

        with patch("core.coherence.call_llm", side_effect=counting):
            check_coherence()
        assert call_count == 1


class TestRateLimiting:

    def test_not_limited_when_no_marker(self, store):
        assert _is_rate_limited(store) is False

    def test_limited_after_check(self, store):
        _record_check(store)
        assert _is_rate_limited(store) is True

    def test_not_limited_after_24h(self, store):
        marker = store / ".coherence-last-check"
        old = (datetime.now() - timedelta(hours=25)).isoformat()
        marker.write_text(old)
        assert _is_rate_limited(store) is False

    def test_rate_limited_skips_check(self, setup_memories):
        store = setup_memories[0]  # just need the fixture to run
        from core.database import get_base_dir
        base = get_base_dir()
        _record_check(base)
        result = check_coherence()
        assert result.get("skipped") == "rate_limited"
