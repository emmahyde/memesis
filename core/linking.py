"""
Cosine-similarity based linking for Memory.linked_observation_ids[].

Sprint A Wave 2 WS-F — panel finding C3 / OD-D.

After Stage 2 consolidation produces a new Memory, this module computes
similarity against existing memories using stored embeddings, selects the
top-k above threshold, validates UUIDs against the DB, and populates
memory.linked_observation_ids.

Integration point: called by consolidator.py _execute_keep / _execute_promote
via the link_memory() wrapper.
"""

import json
import logging
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — OD-D: threshold parameterized via env var (default 0.90)
# ---------------------------------------------------------------------------

LINK_COSINE_THRESHOLD: float = float(os.environ.get("MEMESIS_LINK_THRESHOLD", "0.90"))
LINK_MAX_NEIGHBORS: int = 3
LINK_MIN_NEIGHBORS: int = 0  # may return empty list

_TRACE_PATH = Path(
    os.environ.get(
        "MEMESIS_LINK_TRACE_PATH",
        str(Path.home() / "projects" / "memesis" / "backfill-output" / "observability" / "linking-trace.jsonl"),
    )
)


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _bytes_to_floats(embedding_bytes: bytes) -> list[float]:
    """Decode raw float32 bytes (from sqlite-vec) to a list of floats."""
    n = len(embedding_bytes) // 4
    return list(struct.unpack(f"{n}f", embedding_bytes))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _get_embedding_bytes(memory) -> bytes | None:
    """
    Retrieve raw embedding bytes for a Memory.

    Tries VecStore (sqlite-vec) first, then falls back to the inline
    ``embedding`` attribute if present (test injection path).
    """
    # Test-injection path: Memory objects in tests may carry a synthetic
    # ``embedding`` attribute that is already a list[float] or bytes.
    inline = getattr(memory, "embedding", None)
    if inline is not None:
        if isinstance(inline, (bytes, bytearray)):
            return bytes(inline)
        if isinstance(inline, (list, tuple)):
            # Pack floats to bytes so _bytes_to_floats can round-trip
            return struct.pack(f"{len(inline)}f", *inline)

    # Production path: look up via VecStore
    try:
        from .database import get_vec_store
        vec = get_vec_store()
        if vec and vec.available:
            return vec.get_embedding(str(memory.id))
    except Exception as exc:
        logger.debug("VecStore lookup failed for %s: %s", memory.id, exc)

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_links_for_observation(
    new_memory,
    candidate_memories: Iterable,
    *,
    threshold: float = LINK_COSINE_THRESHOLD,
    top_k: int = LINK_MAX_NEIGHBORS,
) -> list[tuple[str, float]]:
    """
    Return list of (memory_id, similarity_score) sorted desc by similarity,
    filtered by threshold (>=), capped at top_k.

    Args:
        new_memory: The newly created Memory whose embedding is the query.
        candidate_memories: Iterable of Memory objects to compare against.
            Must not include new_memory itself.
        threshold: Minimum cosine similarity to include a link (default 0.90).
        top_k: Maximum number of links to return (default 3).

    Returns:
        Sorted list of (memory_id, score) tuples, best match first.
        Empty list when no candidates exceed threshold.
    """
    query_bytes = _get_embedding_bytes(new_memory)
    if query_bytes is None:
        logger.debug("No embedding for memory %s — skipping link computation", new_memory.id)
        return []

    query_vec = _bytes_to_floats(query_bytes)

    scored: list[tuple[str, float]] = []
    for candidate in candidate_memories:
        cid = str(candidate.id)
        if cid == str(new_memory.id):
            continue
        cand_bytes = _get_embedding_bytes(candidate)
        if cand_bytes is None:
            continue
        cand_vec = _bytes_to_floats(cand_bytes)
        score = _cosine(query_vec, cand_vec)
        if score >= threshold:
            scored.append((cid, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def detect_topic_drift(new_memory, linked_memory) -> bool:
    """
    Cheap false-positive proxy: returns True when all three categorical axes
    (kind, subject, knowledge_type) differ between the new memory and the linked
    candidate, suggesting a surface-text similarity with no topical overlap.

    Used for observability logging only — does NOT exclude the link.

    Args:
        new_memory: The newly created Memory.
        linked_memory: A candidate Memory that passed the threshold check.

    Returns:
        True if kind AND subject AND knowledge_type all differ (and are non-null).
    """
    def _val(mem, attr: str) -> str | None:
        v = getattr(mem, attr, None)
        return v.strip().lower() if isinstance(v, str) and v.strip() else None

    new_kind = _val(new_memory, "kind")
    new_subject = _val(new_memory, "subject")
    new_kt = _val(new_memory, "knowledge_type")

    lnk_kind = _val(linked_memory, "kind")
    lnk_subject = _val(linked_memory, "subject")
    lnk_kt = _val(linked_memory, "knowledge_type")

    # If any axis is None on either side we cannot confirm drift
    if None in (new_kind, new_subject, new_kt, lnk_kind, lnk_subject, lnk_kt):
        return False

    return new_kind != lnk_kind and new_subject != lnk_subject and new_kt != lnk_kt


def link_memory(memory, db_session=None) -> list[str]:
    """
    High-level wrapper: compute cosine links for a newly created Memory,
    validate UUIDs against the DB, populate memory.linked_observation_ids,
    and emit an observability trace entry.

    Args:
        memory: A persisted Memory instance (must have a valid .id).
        db_session: Unused (reserved for future connection-injection).

    Returns:
        List of validated memory IDs that were written to
        memory.linked_observation_ids.
    """
    # Load candidates: all non-archived memories except the new one
    try:
        candidates = list(
            Memory.select().where(
                Memory.archived_at.is_null(),
                Memory.id != str(memory.id),
            )
        )
    except Exception as exc:
        logger.warning("Could not load candidate memories for linking: %s", exc)
        return []

    threshold = LINK_COSINE_THRESHOLD
    raw_links = find_links_for_observation(
        memory,
        candidates,
        threshold=threshold,
        top_k=LINK_MAX_NEIGHBORS,
    )

    # Validate UUIDs against the DB manifest (filter hallucinated ids)
    valid_ids: set[str] = {str(m.id) for m in candidates}
    validated: list[tuple[str, float]] = [(mid, score) for mid, score in raw_links if mid in valid_ids]

    # Detect topic drift for observability
    candidate_map = {str(m.id): m for m in candidates}
    selected_entries = []
    for mid, score in validated:
        drift = False
        if mid in candidate_map:
            drift = detect_topic_drift(memory, candidate_map[mid])
        selected_entries.append({"id": mid, "score": round(score, 6), "topic_drift": drift})
        if drift:
            logger.debug(
                "Linking: topic drift detected for %s → %s (score=%.4f)", memory.id, mid, score
            )

    # Entries rejected only by top_k cap (above threshold but didn't make the cut)
    all_above = find_links_for_observation(
        memory,
        candidates,
        threshold=threshold,
        top_k=len(candidates) + 1,  # uncapped
    )
    all_above_valid = [(mid, score) for mid, score in all_above if mid in valid_ids]
    rejected_by_cap = [
        {"id": mid, "score": round(score, 6)}
        for mid, score in all_above_valid
        if mid not in {e["id"] for e in selected_entries}
    ]

    linked_ids = [e["id"] for e in selected_entries]

    # Persist to Memory row
    try:
        memory.linked_observation_ids = json.dumps(linked_ids)
        memory.save()
    except Exception as exc:
        logger.warning("Could not save linked_observation_ids for %s: %s", memory.id, exc)

    # Observability trace
    _emit_trace(
        memory_id=str(memory.id),
        candidate_count=len(candidates),
        above_threshold_count=len(all_above_valid),
        selected=selected_entries,
        rejected_above_threshold_due_to_top_k_cap=rejected_by_cap,
        threshold=threshold,
    )

    logger.info(
        "Linking: memory=%s linked=%d/%d candidates (threshold=%.2f)",
        memory.id,
        len(linked_ids),
        len(candidates),
        threshold,
    )

    return linked_ids


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def _emit_trace(
    *,
    memory_id: str,
    candidate_count: int,
    above_threshold_count: int,
    selected: list[dict],
    rejected_above_threshold_due_to_top_k_cap: list[dict],
    threshold: float,
) -> None:
    """
    Append one JSON line to the linking-trace JSONL file.

    Schema:
    {
      "ts": "ISO8601",
      "memory_id": "...",
      "candidate_count": N,
      "above_threshold_count": K,
      "selected": [{"id": "...", "score": 0.93, "topic_drift": false}, ...],
      "rejected_above_threshold_due_to_top_k_cap": [...],
      "threshold": 0.90
    }
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "memory_id": memory_id,
        "candidate_count": candidate_count,
        "above_threshold_count": above_threshold_count,
        "selected": selected,
        "rejected_above_threshold_due_to_top_k_cap": rejected_above_threshold_due_to_top_k_cap,
        "threshold": threshold,
    }
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("Could not write linking trace: %s", exc)
