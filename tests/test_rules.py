"""Tests for core/rules.py — guardrail predicate evaluation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.models import Rule
from core.rules import active_rules, evaluate_rule


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _rule(check_kind, check_arg, text="rule", status="active", scope=None) -> Rule:
    return Rule.create(
        text=text, check_kind=check_kind, check_arg=check_arg,
        status=status, scope=scope,
    )


# --- forbid_bash_pattern ----------------------------------------------------


def test_forbid_bash_pattern_matches(db):
    rule = _rule("forbid_bash_pattern", r"rm\s+-rf")
    violated, conf, reason = evaluate_rule(rule, "Bash", {"command": "rm -rf /tmp/x"})
    assert violated is True
    assert conf == 1.0
    assert "forbidden pattern" in reason


def test_forbid_bash_pattern_ignores_non_bash(db):
    rule = _rule("forbid_bash_pattern", r"rm\s+-rf")
    violated, _, _ = evaluate_rule(rule, "Edit", {"command": "rm -rf /tmp/x"})
    assert violated is False


def test_forbid_bash_pattern_no_match(db):
    rule = _rule("forbid_bash_pattern", r"rm\s+-rf")
    violated, _, _ = evaluate_rule(rule, "Bash", {"command": "ls -la"})
    assert violated is False


def test_bad_regex_fails_open(db):
    rule = _rule("forbid_bash_pattern", r"[unclosed")
    violated, _, _ = evaluate_rule(rule, "Bash", {"command": "anything"})
    assert violated is False


# --- forbid_path_edit -------------------------------------------------------


def test_forbid_path_edit_matches_glob(db):
    rule = _rule("forbid_path_edit", "*.lock")
    violated, conf, _ = evaluate_rule(rule, "Write", {"file_path": "/repo/a/b.lock"})
    assert violated is True and conf == 1.0


def test_forbid_path_edit_ignores_non_edit_tool(db):
    rule = _rule("forbid_path_edit", "*.lock")
    violated, _, _ = evaluate_rule(rule, "Bash", {"file_path": "/repo/a/b.lock"})
    assert violated is False


def test_forbid_path_edit_no_match(db):
    rule = _rule("forbid_path_edit", "*.lock")
    violated, _, _ = evaluate_rule(rule, "Edit", {"file_path": "/repo/a/b.py"})
    assert violated is False


# --- require_absent ---------------------------------------------------------


def test_require_absent_finds_pattern_anywhere(db):
    rule = _rule("require_absent", r"sk-[A-Za-z0-9]{8}")
    violated, _, _ = evaluate_rule(
        rule, "Write", {"file_path": "x.py", "content": "KEY = 'sk-abcd1234'"}
    )
    assert violated is True


def test_require_absent_clean_input(db):
    rule = _rule("require_absent", r"sk-[A-Za-z0-9]{8}")
    violated, _, _ = evaluate_rule(rule, "Write", {"content": "no secrets here"})
    assert violated is False


# --- semantic ---------------------------------------------------------------


def test_semantic_violation(db):
    rule = _rule("semantic", "never delete the production database")
    resp = json.dumps({"verdict": "VIOLATION", "confidence": 0.92, "reason": "drops prod"})
    with patch("core.llm.call_llm", return_value=resp):
        violated, conf, reason = evaluate_rule(rule, "Bash", {"command": "dropdb prod"})
    assert violated is True
    assert conf == 0.92
    assert reason == "drops prod"


def test_semantic_ok(db):
    rule = _rule("semantic", "never delete the production database")
    resp = json.dumps({"verdict": "OK", "confidence": 0.1, "reason": "read only"})
    with patch("core.llm.call_llm", return_value=resp):
        violated, _, _ = evaluate_rule(rule, "Bash", {"command": "psql -c 'select 1'"})
    assert violated is False


def test_semantic_llm_error_fails_open(db):
    rule = _rule("semantic", "anything")
    with patch("core.llm.call_llm", side_effect=RuntimeError("timeout")):
        violated, conf, _ = evaluate_rule(rule, "Bash", {"command": "x"})
    assert violated is False and conf == 0.0


# --- active_rules / scope ---------------------------------------------------


def test_active_rules_excludes_proposed_and_disabled(db):
    _rule("forbid_bash_pattern", "a", status="active")
    _rule("forbid_bash_pattern", "b", status="proposed")
    _rule("forbid_bash_pattern", "c", status="disabled")
    assert len(active_rules()) == 1


def test_active_rules_scope_filter(db):
    _rule("forbid_bash_pattern", "g", scope=None)            # global
    _rule("forbid_bash_pattern", "p", scope="memesis")       # project-scoped
    _rule("forbid_bash_pattern", "o", scope="other-project")

    in_scope = active_rules(scope="-Users-emmahyde-projects-memesis")
    args = {r.check_arg for r in in_scope}
    assert "g" in args and "p" in args and "o" not in args


def test_unknown_check_kind_is_not_a_violation(db):
    rule = _rule("bogus_kind", "x")
    violated, _, _ = evaluate_rule(rule, "Bash", {"command": "x"})
    assert violated is False
