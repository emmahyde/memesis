"""
Rule proposal — derive candidate guardrails from enforceable memories.

A memory of kind directive or correction may encode a rule the agent should
be held to. run_rule_proposal_sweep() audits such memories with one LLM call
each: the LLM extracts a machine-checkable predicate, and the result is
inserted as a Rule with status='proposed' — inert until the user activates it
via /memesis:rules.

Each memory is proposed at most once — a memory already named by some Rule's
source_memory_id is skipped. CLAUDE.md Rule 2 — the LLM call goes through
core.llm.call_llm.
"""

from __future__ import annotations

import logging

from core.database import get_commit_ref
from core.llm import call_llm
from core.models import Memory, Rule
from core.prompts import RULE_PROPOSAL_PROMPT
from core.rules import CHECK_KINDS, _extract_json

logger = logging.getLogger(__name__)

# Memory kinds whose contents may prescribe an enforceable rule.
_RULE_BEARING_KINDS = ("directive", "correction")
_VALID_SEVERITIES = frozenset({"block", "ask", "warn"})


def _propose_one(memory: Memory) -> dict | None:
    """Audit one memory. Return a sanitised rule spec, or None if not a rule."""
    prompt = RULE_PROPOSAL_PROMPT.format(
        kind=memory.kind or "",
        title=memory.title or "",
        content=(memory.content or "")[:3000],
    )
    parsed = _extract_json(call_llm(prompt, max_tokens=400, temperature=0))

    if not parsed.get("is_rule"):
        return None

    check_kind = parsed.get("check_kind")
    if check_kind not in CHECK_KINDS:
        return None

    text = (parsed.get("text") or "").strip()
    if not text:
        return None

    check_arg = (parsed.get("check_arg") or "").strip()
    if not check_arg:
        # A semantic rule with no explicit arg is judged against its own text.
        if check_kind == "semantic":
            check_arg = text
        else:
            return None

    severity = parsed.get("severity")
    if severity not in _VALID_SEVERITIES:
        severity = "ask"

    return {
        "text": text, "check_kind": check_kind,
        "check_arg": check_arg, "severity": severity,
    }


def run_rule_proposal_sweep(limit: int = 10) -> dict:
    """Audit up to `limit` rule-bearing memories not yet linked to a rule.

    Returns a counts dict: ``checked``, ``proposed``, ``skipped``, ``errors``.
    """
    counts = {"checked": 0, "proposed": 0, "skipped": 0, "errors": 0}

    sourced = {
        r.source_memory_id
        for r in Rule.select(Rule.source_memory_id).where(
            Rule.source_memory_id.is_null(False)
        )
    }

    query = Memory.active().where(
        Memory.kind.in_(list(_RULE_BEARING_KINDS))
    )
    candidates = [m for m in query if m.id not in sourced][:limit]

    for memory in candidates:
        counts["checked"] += 1
        try:
            spec = _propose_one(memory)
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the sweep
            counts["errors"] += 1
            logger.warning("rule_proposal: failed for %s: %s", memory.id, exc)
            continue

        if spec is None:
            counts["skipped"] += 1
            continue

        Rule.create(
            text=spec["text"],
            check_kind=spec["check_kind"],
            check_arg=spec["check_arg"],
            severity=spec["severity"],
            status="proposed",
            scope=memory.project,
            source_memory_id=memory.id,
            commit_ref=get_commit_ref(),
        )
        counts["proposed"] += 1
        logger.info(
            "rule_proposal: proposed rule from %s (%s)",
            memory.title or "untitled", memory.id,
        )

    logger.info("rule proposal sweep: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
