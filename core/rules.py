"""
Rule evaluation — does a proposed tool call violate an active guardrail?

A Rule (core/models.Rule) carries a machine-checkable predicate. This module
turns a ``{tool_name, tool_input}`` payload into a verdict the PreToolUse guard
acts on. Detection mirrors core/verifier.py's ``evaluate_predicate`` shape.

Structured checks are deterministic (confidence 1.0). The ``semantic`` check
defers to an LLM judge and returns a fractional confidence — CLAUDE.md Rule 2,
the call goes through core.llm.call_llm.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path

from core.models import Rule

logger = logging.getLogger(__name__)

CHECK_KINDS = frozenset({
    "forbid_bash_pattern", "forbid_path_edit", "require_absent", "semantic",
})

# Confidence at or above which a semantic match is treated as a hard violation
# (the guard denies); below it the guard escalates to the user instead.
SEMANTIC_DENY_FLOOR = 0.85

# Tools whose input names a file path the forbid_path_edit check applies to.
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def sync_rules_from_memories() -> dict:
    """Sync the Rule table from crystallized/instinctive directive memories.

    Upserts one semantic Rule per qualifying memory. Deactivates rules whose
    backing memory was archived or demoted below crystallized. Returns counts
    for cron logging.
    """
    from core.models import Memory

    created = updated = deactivated = 0

    qualifying = list(
        Memory.select()
        .where(
            Memory.kind == "directive",
            Memory.stage.in_(["crystallized", "instinctive"]),
            Memory.archived_at.is_null(),
        )
    )
    qualifying_ids = {str(m.id) for m in qualifying}

    for mem in qualifying:
        mid = str(mem.id)
        text = (mem.content or mem.title or "").strip()
        if not text:
            continue
        existing = Rule.get_or_none(Rule.source_memory_id == mid)
        if existing is None:
            Rule.create(
                text=text,
                check_kind="semantic",
                severity="warn",
                status="active",
                source_memory_id=mid,
                scope=getattr(mem, "project", None) or None,
            )
            created += 1
        elif existing.text != text or existing.status != "active":
            existing.text = text
            existing.status = "active"
            existing.save()
            updated += 1

    # Deactivate rules whose backing memory was archived or no longer qualifies.
    for rule in Rule.select().where(
        Rule.source_memory_id.is_null(False),
        Rule.status == "active",
    ):
        if rule.source_memory_id not in qualifying_ids:
            rule.status = "disabled"
            rule.save()
            deactivated += 1

    logger.info(
        "sync_rules_from_memories: created=%d updated=%d deactivated=%d",
        created, updated, deactivated,
    )
    return {"created": created, "updated": updated, "deactivated": deactivated}


def active_rules(scope: str | None = None) -> list[Rule]:
    """Return enforced rules in scope — global rules plus those matching ``scope``.

    A rule with a NULL ``scope`` is global. A non-NULL scope is a project slug
    or path glob; it matches when ``scope`` glob-matches it or contains it.
    """
    rules = list(Rule.select().where(Rule.status == "active"))
    if scope is None:
        return rules
    return [r for r in rules if _scope_matches(r.scope, scope)]


def _scope_matches(rule_scope: str | None, ctx: str) -> bool:
    if not rule_scope:
        return True
    return fnmatch.fnmatch(ctx, rule_scope) or rule_scope in ctx


def _stringify(tool_input: dict) -> str:
    """Flatten a tool-input dict to a single searchable string."""
    try:
        return json.dumps(tool_input, default=str)
    except (TypeError, ValueError):
        return str(tool_input)


def _extract_json(raw: str) -> dict:
    """Extract the first {...} block from LLM output."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in LLM output: {(raw or '')[:200]!r}")
    return json.loads(match.group(0))


def evaluate_rule(rule: Rule, tool_name: str, tool_input: dict) -> tuple[bool, float, str]:
    """Evaluate one rule against a proposed tool call.

    Returns ``(violated, confidence, reason)``. ``violated`` is False whenever
    the rule does not apply to this tool. Structured checks report
    ``confidence`` 1.0; the semantic check reports a fractional confidence.
    A malformed predicate fails open (not a violation) and is logged.
    """
    tool_input = tool_input or {}
    kind = rule.check_kind
    arg = rule.check_arg or ""

    if kind == "forbid_bash_pattern":
        if tool_name != "Bash" or not arg:
            return False, 0.0, ""
        if _regex_search(arg, str(tool_input.get("command", "")), rule):
            return True, 1.0, f"command matches forbidden pattern /{arg}/"
        return False, 0.0, ""

    if kind == "forbid_path_edit":
        if tool_name not in _EDIT_TOOLS or not arg:
            return False, 0.0, ""
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if path and (fnmatch.fnmatch(path, arg) or fnmatch.fnmatch(Path(path).name, arg)):
            return True, 1.0, f"edits a path matching {arg}"
        return False, 0.0, ""

    if kind == "require_absent":
        if not arg:
            return False, 0.0, ""
        if _regex_search(arg, _stringify(tool_input), rule):
            return True, 1.0, f"input contains forbidden pattern /{arg}/"
        return False, 0.0, ""

    if kind == "semantic":
        return _evaluate_semantic(rule, tool_name, tool_input)

    logger.warning("rules: unknown check_kind %r on rule %s", kind, rule.id)
    return False, 0.0, ""


def _regex_search(pattern: str, text: str, rule: Rule) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error as exc:
        logger.warning("rules: bad regex %r in rule %s: %s", pattern, rule.id, exc)
        return False


def _evaluate_semantic(rule: Rule, tool_name: str, tool_input: dict) -> tuple[bool, float, str]:
    """LLM-judge a natural-language rule against a tool call. Fails open."""
    from core.llm import call_llm
    from core.prompts import RULE_SEMANTIC_JUDGE_PROMPT

    prompt = RULE_SEMANTIC_JUDGE_PROMPT.format(
        rule=rule.text,
        tool_name=tool_name,
        tool_input=_stringify(tool_input)[:2000],
    )
    try:
        parsed = _extract_json(call_llm(prompt, max_tokens=300, temperature=0))
    except Exception as exc:  # noqa: BLE001 — a judge failure must never block a call
        logger.warning("rules: semantic judge failed for rule %s: %s", rule.id, exc)
        return False, 0.0, ""

    violated = str(parsed.get("verdict", "")).upper() == "VIOLATION"
    try:
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(parsed.get("reason", "")).strip()
    return violated, confidence, reason
