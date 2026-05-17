"""Tests for core/session_digest.py — per-session topic + summary."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.models import SessionDigest
from core.session_digest import write_session_digest


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


DIGEST_RESP = json.dumps({
    "topic": "Rules engine and PreToolUse enforcement",
    "summary": "Built the Rule model and the guard hook. Tests pass.",
})


def test_writes_digest(db):
    with patch("core.session_digest.call_llm", return_value=DIGEST_RESP):
        digest = write_session_digest("sess-1", "work on the rules engine", ["m1", "m2"])
    assert digest is not None
    assert digest.topic == "Rules engine and PreToolUse enforcement"
    assert json.loads(digest.memory_ids) == ["m1", "m2"]
    assert SessionDigest.get_by_id("sess-1").summary.startswith("Built the Rule model")


def test_upsert_overwrites_existing(db):
    with patch("core.session_digest.call_llm", return_value=DIGEST_RESP):
        write_session_digest("sess-1", "first pass", ["m1"])
    resp2 = json.dumps({"topic": "New topic", "summary": "updated"})
    with patch("core.session_digest.call_llm", return_value=resp2):
        write_session_digest("sess-1", "second pass", ["m2"])
    rows = list(SessionDigest.select())
    assert len(rows) == 1
    assert rows[0].topic == "New topic"
    assert json.loads(rows[0].memory_ids) == ["m2"]


def test_skips_unknown_session(db):
    with patch("core.session_digest.call_llm") as mock_llm:
        assert write_session_digest("unknown", "content") is None
        mock_llm.assert_not_called()


def test_skips_empty_content(db):
    with patch("core.session_digest.call_llm") as mock_llm:
        assert write_session_digest("sess-1", "   ") is None
        mock_llm.assert_not_called()


def test_llm_error_returns_none(db):
    with patch("core.session_digest.call_llm", side_effect=RuntimeError("boom")):
        assert write_session_digest("sess-1", "real content") is None
    assert SessionDigest.select().count() == 0


def test_empty_topic_returns_none(db):
    resp = json.dumps({"topic": "", "summary": "has a summary but no topic"})
    with patch("core.session_digest.call_llm", return_value=resp):
        assert write_session_digest("sess-1", "real content") is None
    assert SessionDigest.select().count() == 0
