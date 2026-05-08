"""
Open question lifecycle management (Sprint B WS-H, DS-F9).

Implements:
- get_unresolved_questions: surface unresolved open_questions for session injection
- detect_resolution: cosine + knowledge_type alignment check for correction/finding → question
- mark_resolved: atomic two-row update
- pin_open_question: set is_pinned=True on open_question memories

QUESTION_RESOLUTION_THRESHOLD default 0.85, overridable via MEMESIS_QUESTION_THRESHOLD env var.
"""

import logging
import os
from datetime import datetime, timezone

from .linking import _cosine, _get_embedding_bytes, _bytes_to_floats
from .models import Memory, db

logger = logging.getLogger(__name__)

QUESTION_RESOLUTION_THRESHOLD: float = float(
    os.environ.get("MEMESIS_QUESTION_THRESHOLD", "0.85")
)

_RESOLVING_KINDS = frozenset({"correction", "finding"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_unresolved_questions(limit: int = 20) -> list:
    """
    Return Memory rows where kind='open_question' AND resolved_at IS NULL,
    ordered by importance desc, capped at limit.

    Used by session-injection pipeline to surface pending questions.
    """
    return list(
        Memory.select()
        .where(
            Memory.kind == "open_question",
            Memory.resolved_at.is_null(),
            Memory.archived_at.is_null(),
        )
        .order_by(Memory.importance.desc())
        .limit(limit)
    )


def detect_resolution(new_memory, candidate_questions: list | None = None) -> str | None:
    """
    Given a newly created Memory (kind=correction OR finding), check whether
    it semantically resolves any unresolved open_question.

    Returns the question's memory_id if found, else None.

    Resolution criteria:
    - new_memory.kind must be in ('correction', 'finding')
    - cosine similarity between new_memory and candidate >= QUESTION_RESOLUTION_THRESHOLD
    - knowledge_type alignment: if question has a non-null knowledge_type, it must match
      new_memory's knowledge_type (if null on question, any knowledge_type is accepted)

    Args:
        new_memory: A persisted Memory instance.
        candidate_questions: Optional list of Memory rows to check against.
            Defaults to get_unresolved_questions() if None.

    Returns:
        question.id string if a resolving question is found, else None.
    """
    if new_memory.kind not in _RESOLVING_KINDS:
        return None

    questions = candidate_questions if candidate_questions is not None else get_unresolved_questions()
    if not questions:
        return None

    new_bytes = _get_embedding_bytes(new_memory)
    if new_bytes is None:
        logger.debug(
            "detect_resolution: no embedding for memory %s — skipping cosine check",
            new_memory.id,
        )
        return None

    new_vec = _bytes_to_floats(new_bytes)

    best_id: str | None = None
    best_score: float = -1.0

    for question in questions:
        # Skip already-resolved questions
        if question.resolved_at is not None:
            continue

        q_bytes = _get_embedding_bytes(question)
        if q_bytes is None:
            continue

        q_vec = _bytes_to_floats(q_bytes)
        score = _cosine(new_vec, q_vec)

        if score < QUESTION_RESOLUTION_THRESHOLD:
            continue

        # knowledge_type alignment check
        q_kt = (question.knowledge_type or "").strip().lower() or None
        new_kt = (new_memory.knowledge_type or "").strip().lower() or None
        if q_kt is not None and new_kt is not None and q_kt != new_kt:
            logger.debug(
                "detect_resolution: knowledge_type mismatch for question %s "
                "(question=%s, new=%s) — skipping",
                question.id,
                q_kt,
                new_kt,
            )
            continue

        if score > best_score:
            best_score = score
            best_id = str(question.id)

    if best_id:
        logger.info(
            "detect_resolution: memory %s resolves question %s (score=%.4f)",
            new_memory.id,
            best_id,
            best_score,
        )

    return best_id


def mark_resolved(question, resolving: "Memory") -> None:
    """
    Atomic update: set question.resolved_at = now AND
    resolving.resolves_question_id = question.id.

    Both writes happen inside a single DB transaction.
    """
    now = datetime.now(timezone.utc)

    with db.atomic():
        Memory.update(resolved_at=now).where(Memory.id == question.id).execute()
        Memory.update(resolves_question_id=str(question.id)).where(
            Memory.id == resolving.id
        ).execute()

    # Refresh in-memory state to reflect DB
    question.resolved_at = now
    resolving.resolves_question_id = str(question.id)

    logger.info(
        "mark_resolved: question=%s marked resolved by memory=%s",
        question.id,
        resolving.id,
    )


def pin_open_question(memory) -> None:
    """
    Set is_pinned=True on a Memory whose kind is open_question.

    Raises ValueError if memory.kind is not 'open_question'.
    """
    if memory.kind != "open_question":
        raise ValueError(
            f"pin_open_question: expected kind='open_question', got kind={memory.kind!r} "
            f"(memory_id={memory.id})"
        )

    Memory.update(is_pinned=True).where(Memory.id == memory.id).execute()
    memory.is_pinned = True

    logger.debug("pin_open_question: pinned memory=%s", memory.id)
