#!/usr/bin/env python3
"""PreToolUse hook — soft-blocks tool calls that violate an active rule.

Reads the {tool_name, tool_input} payload Claude Code sends on stdin, evaluates
it against every active Rule in scope (core/rules.py), and emits a permission
decision:

  deny  — a `block`-severity rule matched with high confidence; the agent is
          told why and self-corrects mid-turn.
  ask   — a `block` rule matched with low (semantic) confidence, or an `ask`
          rule matched; the user decides.
  allow — nothing matched (emitted as empty output).

`warn`-severity rules never block. The hook fails OPEN: any error allows the
call, so a bug here can never wedge a session. It uses connect_light() — no
DDL, no migrations — to stay cheap on the per-tool-call hot path.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks._safe import emit_stdout

# Strength ordering for picking the single strongest verdict.
_RANK = {"allow": 0, "warn": 1, "ask": 2, "deny": 3}


def _decide(rule, confidence: float) -> str:
    """Map a violated rule + match confidence to a permission decision."""
    from core.rules import SEMANTIC_DENY_FLOOR

    if rule.severity == "warn":
        return "warn"
    if rule.severity == "ask":
        return "ask"
    # severity == "block": deny when confident, else escalate to the user.
    return "deny" if confidence >= SEMANTIC_DENY_FLOOR else "ask"


def _evaluate(tool_name: str, tool_input: dict, scope: str):
    """Return (decision, reason) for the strongest in-scope rule violation."""
    from core.models import Rule
    from core.rules import active_rules, evaluate_rule

    rules = active_rules(scope)
    # Structured checks are cheap and deterministic — run them first so a
    # confident deny lets us skip the LLM-backed semantic checks entirely.
    structured = [r for r in rules if r.check_kind != "semantic"]
    semantic = [r for r in rules if r.check_kind == "semantic"]

    decision, reason, hit_id = "allow", "", None
    for rule in structured + semantic:
        if decision == "deny" and rule.check_kind == "semantic":
            break  # already denying — no need to pay for an LLM judge
        violated, confidence, why = evaluate_rule(rule, tool_name, tool_input)
        if not violated:
            continue
        verdict = _decide(rule, confidence)
        if _RANK[verdict] > _RANK[decision]:
            decision, hit_id = verdict, rule.id
            detail = f" ({why})" if why else ""
            reason = f"memesis rule: {rule.text}{detail}"

    if hit_id is not None:
        # Best-effort violation tally — never let a counter write block the call.
        try:
            Rule.update(violation_count=Rule.violation_count + 1).where(
                Rule.id == hit_id
            ).execute()
        except Exception:  # noqa: BLE001
            pass

    return decision, reason


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — malformed input must not block the call
        emit_stdout("")
        return

    try:
        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input") or {}
        cwd = payload.get("cwd") or os.getcwd()

        from core.database import connect_light, project_slug

        connect_light()
        decision, reason = _evaluate(tool_name, tool_input, project_slug(cwd) or "")
    except Exception:  # noqa: BLE001 — fail OPEN, never wedge a session
        emit_stdout("")
        return

    if decision in ("deny", "ask"):
        emit_stdout({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        })
    else:
        emit_stdout("")


if __name__ == "__main__":
    main()
