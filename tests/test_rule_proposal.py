"""Tests for core/rule_proposal.py — candidate-rule derivation from memories."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.models import Memory, Rule
from core.rule_proposal import _propose_one, run_rule_proposal_sweep


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _mem(kind=None, content="some rule-bearing content") -> Memory:
    return Memory.create(
        stage="consolidated", title="t", summary="s", content=content,
        importance=0.6, kind=kind,
    )


RULE_RESP = json.dumps({
    "is_rule": True,
    "text": "never edit the lockfile by hand",
    "check_kind": "forbid_path_edit",
    "check_arg": "*.lock",
    "severity": "block",
    "rationale": "lockfiles are generated",
})
NOT_RULE_RESP = json.dumps({"is_rule": False, "rationale": "just a fact"})


# --- _propose_one -----------------------------------------------------------


def test_propose_one_returns_spec(db):
    m = _mem(kind="directive")
    with patch("core.rule_proposal.call_llm", return_value=RULE_RESP):
        spec = _propose_one(m)
    assert spec["check_kind"] == "forbid_path_edit"
    assert spec["severity"] == "block"


def test_propose_one_non_rule_returns_none(db):
    m = _mem(kind="directive")
    with patch("core.rule_proposal.call_llm", return_value=NOT_RULE_RESP):
        assert _propose_one(m) is None


def test_propose_one_bad_check_kind_returns_none(db):
    m = _mem(kind="directive")
    bad = json.dumps({"is_rule": True, "text": "x", "check_kind": "bogus",
                      "check_arg": "y", "severity": "block"})
    with patch("core.rule_proposal.call_llm", return_value=bad):
        assert _propose_one(m) is None


def test_propose_one_semantic_without_arg_uses_text(db):
    m = _mem(kind="correction")
    resp = json.dumps({"is_rule": True, "text": "be careful with migrations",
                       "check_kind": "semantic", "check_arg": "", "severity": "ask"})
    with patch("core.rule_proposal.call_llm", return_value=resp):
        spec = _propose_one(m)
    assert spec["check_arg"] == "be careful with migrations"


def test_propose_one_invalid_severity_defaults_to_ask(db):
    m = _mem(kind="directive")
    resp = json.dumps({"is_rule": True, "text": "x", "check_kind": "semantic",
                       "check_arg": "x", "severity": "nonsense"})
    with patch("core.rule_proposal.call_llm", return_value=resp):
        spec = _propose_one(m)
    assert spec["severity"] == "ask"


# --- run_rule_proposal_sweep ------------------------------------------------


def test_sweep_proposes_rule(db):
    m = _mem(kind="directive")
    with patch("core.rule_proposal.call_llm", return_value=RULE_RESP):
        result = run_rule_proposal_sweep()
    assert result["proposed"] == 1
    rule = Rule.get(Rule.source_memory_id == m.id)
    assert rule.status == "proposed"
    assert rule.check_kind == "forbid_path_edit"


def test_sweep_skips_already_sourced(db):
    m = _mem(kind="directive")
    Rule.create(text="x", check_kind="semantic", check_arg="x",
                status="proposed", source_memory_id=m.id)
    with patch("core.rule_proposal.call_llm") as mock_llm:
        result = run_rule_proposal_sweep()
        mock_llm.assert_not_called()
    assert result["checked"] == 0


def test_sweep_ignores_non_rule_bearing_kinds(db):
    _mem(kind="fact")
    with patch("core.rule_proposal.call_llm") as mock_llm:
        result = run_rule_proposal_sweep()
        mock_llm.assert_not_called()
    assert result["checked"] == 0


def test_sweep_includes_correction_kind(db):
    _mem(kind="correction")
    with patch("core.rule_proposal.call_llm", return_value=RULE_RESP):
        result = run_rule_proposal_sweep()
    assert result["proposed"] == 1


def test_sweep_llm_error_is_non_fatal(db):
    _mem(kind="directive")
    with patch("core.rule_proposal.call_llm", side_effect=RuntimeError("boom")):
        result = run_rule_proposal_sweep()
    assert result["errors"] == 1
    assert result["proposed"] == 0


def test_sweep_respects_limit(db):
    for _ in range(5):
        _mem(kind="directive")
    with patch("core.rule_proposal.call_llm", return_value=NOT_RULE_RESP):
        result = run_rule_proposal_sweep(limit=2)
    assert result["checked"] == 2
