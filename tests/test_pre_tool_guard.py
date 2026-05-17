"""Tests for hooks/pre_tool_guard.py — PreToolUse rule enforcement."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.models import Rule
from hooks.pre_tool_guard import _evaluate, main


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _rule(check_kind, check_arg, severity="block", status="active",
          text="r", scope=None) -> Rule:
    return Rule.create(
        text=text, check_kind=check_kind, check_arg=check_arg,
        severity=severity, status=status, scope=scope,
    )


# --- _evaluate --------------------------------------------------------------


def test_no_rules_allows(db):
    decision, _ = _evaluate("Bash", {"command": "ls"}, "")
    assert decision == "allow"


def test_block_rule_denies(db):
    _rule("forbid_bash_pattern", r"rm\s+-rf")
    decision, reason = _evaluate("Bash", {"command": "rm -rf /"}, "")
    assert decision == "deny"
    assert "memesis rule" in reason


def test_ask_severity_rule_asks(db):
    _rule("forbid_bash_pattern", r"curl", severity="ask")
    decision, _ = _evaluate("Bash", {"command": "curl example.com"}, "")
    assert decision == "ask"


def test_warn_severity_never_blocks(db):
    _rule("forbid_bash_pattern", r"echo", severity="warn")
    decision, _ = _evaluate("Bash", {"command": "echo hi"}, "")
    assert decision == "warn"


def test_violation_count_incremented(db):
    rule = _rule("forbid_bash_pattern", r"rm")
    _evaluate("Bash", {"command": "rm x"}, "")
    assert Rule.get_by_id(rule.id).violation_count == 1


def test_strongest_verdict_wins(db):
    _rule("forbid_bash_pattern", r"curl", severity="ask")
    _rule("forbid_bash_pattern", r"\brm\b", severity="block")
    decision, _ = _evaluate("Bash", {"command": "rm x && curl y"}, "")
    assert decision == "deny"


def test_semantic_low_confidence_asks(db):
    _rule("semantic", "no network access")
    resp = json.dumps({"verdict": "VIOLATION", "confidence": 0.5, "reason": "maybe"})
    with patch("core.llm.call_llm", return_value=resp):
        decision, _ = _evaluate("Bash", {"command": "curl x"}, "")
    assert decision == "ask"


def test_semantic_high_confidence_denies(db):
    _rule("semantic", "no network access")
    resp = json.dumps({"verdict": "VIOLATION", "confidence": 0.95, "reason": "net"})
    with patch("core.llm.call_llm", return_value=resp):
        decision, _ = _evaluate("Bash", {"command": "curl x"}, "")
    assert decision == "deny"


def test_confident_structured_deny_skips_semantic(db):
    """A confident structured deny must not pay for the LLM judge."""
    _rule("forbid_bash_pattern", r"\brm\b", severity="block")
    _rule("semantic", "no network access")
    with patch("core.llm.call_llm", side_effect=AssertionError("judge should be skipped")):
        decision, _ = _evaluate("Bash", {"command": "rm x"}, "")
    assert decision == "deny"


# --- main -------------------------------------------------------------------


def test_main_malformed_stdin_fails_open(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    main()
    assert capsys.readouterr().out.strip() == ""


def test_main_emits_deny(db, monkeypatch, capsys):
    _rule("forbid_bash_pattern", r"rm\s+-rf")
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "cwd": "/x"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("core.database.connect_light", lambda base_dir=None: None)
    main()
    parsed = json.loads(capsys.readouterr().out)
    out = parsed["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"


def test_main_allows_when_clean(db, monkeypatch, capsys):
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/x"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("core.database.connect_light", lambda base_dir=None: None)
    main()
    assert capsys.readouterr().out.strip() == ""
