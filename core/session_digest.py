"""
Session digest — summarise one session into a topic label + summary.

Written by the PreCompact hook. The SessionStart panel groups memories per
session by ``topic``, and the post-compact session reads ``summary`` to recover
what the pre-compact session was doing.

CLAUDE.md Rule 2 — the summarising LLM call goes through core.llm.call_llm.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from core.llm import call_llm
from core.models import SessionDigest
from core.prompts import SESSION_DIGEST_PROMPT
from core.rules import _extract_json

logger = logging.getLogger(__name__)

# Cap on the session text fed to the summariser.
MAX_DIGEST_INPUT_CHARS = 8000


def write_session_digest(
    session_id: str, content: str, memory_ids: list[str] | None = None
) -> SessionDigest | None:
    """Summarise a session and upsert its digest row.

    Returns the SessionDigest, or None when there is nothing to summarise or
    the LLM call fails — callers treat a None result as non-fatal.
    """
    if not session_id or session_id == "unknown" or not (content or "").strip():
        return None

    prompt = SESSION_DIGEST_PROMPT.format(content=content[:MAX_DIGEST_INPUT_CHARS])
    try:
        parsed = _extract_json(call_llm(prompt, max_tokens=300, temperature=0))
    except Exception as exc:  # noqa: BLE001 — a digest failure must not break PreCompact
        logger.warning("session_digest: summarise failed for %s: %s", session_id, exc)
        return None

    topic = (parsed.get("topic") or "").strip()
    if not topic:
        return None
    summary = (parsed.get("summary") or "").strip()
    ids_json = json.dumps(list(memory_ids or []))
    now = datetime.now().isoformat()

    SessionDigest.insert(
        session_id=session_id, topic=topic, summary=summary,
        memory_ids=ids_json, created_at=now,
    ).on_conflict(
        conflict_target=[SessionDigest.session_id],
        update={
            SessionDigest.topic: topic,
            SessionDigest.summary: summary,
            SessionDigest.memory_ids: ids_json,
        },
    ).execute()

    logger.info("session_digest: wrote digest for %s — %s", session_id, topic)
    return SessionDigest.get_by_id(session_id)
