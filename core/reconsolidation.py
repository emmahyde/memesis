"""
Reconsolidation — update injected memories based on session evidence.

At PreCompact, compares injected memories against session content to detect:
- confirmations: session reinforces the memory (bump reinforcement_count)
- contradictions: session contradicts the memory (flag for review)
- refinements: session adds nuance or detail (append to content)

One batched LLM call per PreCompact, not per-memory.
"""

import json
import logging
from datetime import datetime

from .llm import call_llm, strip_markdown_fences
from .models import Memory, ConsolidationLog

logger = logging.getLogger(__name__)

RECONSOLIDATION_PROMPT = """You are analyzing whether a session's content confirms, contradicts, or refines memories that were injected at the start.

## Injected Memories
{memories_block}

## Session Content (excerpt)
{session_excerpt}

For each memory, determine ONE of:
- "confirmed" — session content is consistent with or reinforces this memory
- "contradicted" — session content contradicts or invalidates this memory
- "refined" — session content adds nuance, detail, or correction to this memory
- "unmentioned" — session content does not reference this memory at all

Return a JSON array. Each element: {{"memory_id": "...", "action": "confirmed|contradicted|refined|unmentioned", "evidence": "one-sentence explanation"}}

Only return the JSON array, no other text."""


def reconsolidate(
    injected_ids: list[str],
    session_content: str,
    session_id: str,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """Run reconsolidation for injected memories against session content.

    Args:
        injected_ids: Memory IDs that were injected this session.
        session_content: Combined conversation + ephemeral text.
        session_id: Current session identifier.
        model: LLM model to use.

    Returns:
        {"confirmed": [id, ...], "contradicted": [id, ...], "refined": [id, ...]}
    """
    from .flags import get_flag

    if not get_flag("reconsolidation"):
        return {"confirmed": [], "contradicted": [], "refined": []}

    if not injected_ids or not session_content.strip():
        return {"confirmed": [], "contradicted": [], "refined": []}

    # Load injected memories
    memories = list(Memory.select().where(Memory.id.in_(injected_ids)))
    if not memories:
        return {"confirmed": [], "contradicted": [], "refined": []}

    # Build memories block for prompt
    mem_lines = []
    for mem in memories:
        title = mem.title or "Untitled"
        content = (mem.content or "")[:300]
        mem_lines.append(f"### [{mem.id}] {title}\n{content}")
    memories_block = "\n\n".join(mem_lines)

    # Truncate session content to stay within token budget
    session_excerpt = session_content[:4000]

    prompt = RECONSOLIDATION_PROMPT.format(
        memories_block=memories_block,
        session_excerpt=session_excerpt,
    )

    try:
        raw = call_llm(prompt, model=model)
        cleaned = strip_markdown_fences(raw)
        decisions = json.loads(cleaned)
    except Exception as e:
        logger.warning("Reconsolidation LLM call failed: %s", e)
        return {"confirmed": [], "contradicted": [], "refined": []}

    # Process decisions
    result = {"confirmed": [], "contradicted": [], "refined": []}
    now = datetime.now().isoformat()
    mem_by_id = {m.id: m for m in memories}

    for decision in decisions:
        mid = decision.get("memory_id", "")
        action = decision.get("action", "unmentioned")
        evidence = decision.get("evidence", "")

        if mid not in mem_by_id or action == "unmentioned":
            continue

        mem = mem_by_id[mid]

        if action == "confirmed":
            mem.reinforcement_count = (mem.reinforcement_count or 0) + 1
            mem.save()
            result["confirmed"].append(mid)

        elif action == "contradicted":
            # Flag but don't auto-delete — add contradiction tag
            tags = mem.tag_list
            if "contradiction_flagged" not in tags:
                tags.append("contradiction_flagged")
                mem.tag_list = tags
                mem.save()
            result["contradicted"].append(mid)

            ConsolidationLog.create(
                timestamp=now,
                session_id=session_id,
                action="deprecated",
                memory_id=mid,
                rationale=f"Contradicted: {evidence}",
            )

        elif action == "refined":
            # Append refinement to content
            refinement = f"\n\n**Refined ({now[:10]}):** {evidence}"
            mem.content = (mem.content or "") + refinement
            mem.save()
            result["refined"].append(mid)

        logger.info("Reconsolidation: %s -> %s (%s)", mid[:8], action, evidence[:60])

    return result
