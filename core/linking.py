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
from typing import Iterable, Optional

from .models import Memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — OD-D: threshold parameterized via env var.
# Recalibrated 2026-05-13 for bge-small-en-v1.5 @ 384d (was 0.90 for Titan @ 1024d).
# Empirical distribution on N=300 pairs from this corpus: mean=0.645, stdev=0.046,
# max=0.927. 0.72 ≈ mean + 1.5σ — captures genuine semantic neighbors while staying
# clear of the dense background-similarity floor.
# ---------------------------------------------------------------------------

LINK_COSINE_THRESHOLD: float = float(os.environ.get("MEMESIS_LINK_THRESHOLD", "0.72"))
# Auto-promote: when a newly KEPT memory is this close to an existing one,
# treat it as a duplicate. Bump the existing memory's reinforcement_count and
# archive the new dupe instead of leaving both in the store. Tighter than the
# link threshold because the action is destructive (subsumes the new memory).
LINK_AUTO_PROMOTE_THRESHOLD: float = float(os.environ.get("MEMESIS_AUTO_PROMOTE_THRESHOLD", "0.85"))
# Paraphrase-aware dedup: embeddings of reworded duplicates often land *below*
# the auto-promote threshold. Candidates whose cosine falls in the near-miss
# band [LINK_LLM_REVIEW_FLOOR, LINK_AUTO_PROMOTE_THRESHOLD) are escalated to an
# LLM duplicate-confirmation call. The LLM pass fires only when a near-miss
# exists, so cost stays bounded — most KEEPs have no candidate in the band.
LINK_LLM_REVIEW_FLOOR: float = float(os.environ.get("MEMESIS_DEDUP_LLM_FLOOR", "0.70"))
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

    Precedence — inline first, then VecStore:
      1. ``memory.embedding`` attribute (list[float] or bytes). Test-injection path.
      2. VecStore lookup keyed by memory.id. Production path. VecStore is the
         numpy-over-Peewee-BlobField implementation in core/vec.py (replaced
         sqlite-vec + apsw); 384-dim float32 blobs.

    GOTCHA: inline only works for memories the caller constructed and held a
    reference to. Memories returned by ``Memory.select()`` / ``Memory.get_by_id()``
    are fresh instances with no inline ``embedding`` attribute, so they fall
    through to VecStore. Tests that need similarity matching across multiple
    memories (e.g. ``link_memory``, ``auto_promote_if_dupe``, both of which
    fetch candidates via ``Memory.select()``) MUST persist embeddings via
    ``vec_store.store_embedding(memory_id, bytes)`` — setting ``mem.embedding``
    inline on the test fixture is not enough.

    See ``tests/test_linking.py::_persist_with_embedding`` for the canonical
    pattern.
    """
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


def _llm_confirms_duplicate(mem_a, mem_b) -> bool:
    """Ask the LLM whether two near-miss memories are duplicates.

    Returns False on any error — a failed confirmation must never trigger the
    destructive subsumption. Compares all text fields (title + summary + content)
    so paraphrases that embeddings miss can still be caught.
    """
    try:
        from .llm import call_llm
        from .prompts import DEDUP_CONFIRM_PROMPT

        prompt = DEDUP_CONFIRM_PROMPT.format(
            a_title=mem_a.title or "",
            a_summary=mem_a.summary or "",
            a_content=(mem_a.content or "")[:1500],
            b_title=mem_b.title or "",
            b_summary=mem_b.summary or "",
            b_content=(mem_b.content or "")[:1500],
        )
        raw = call_llm(prompt, max_tokens=16, temperature=0)
    except Exception as exc:  # noqa: BLE001 — never let dedup confirmation crash a KEEP
        logger.warning("dedup LLM confirmation failed: %s", exc)
        return False
    verdict = (raw or "").strip().upper()
    return verdict.startswith("DUPLICATE") or "DUPLICATE" in verdict


def auto_promote_if_dupe(memory) -> Optional[str]:
    """
    If a newly created memory is near-identical (cosine >= LINK_AUTO_PROMOTE_THRESHOLD)
    to an existing non-archived memory, treat it as a duplicate KEEP:
      - bump the existing memory's reinforcement_count
      - archive the new memory and set subsumed_by to the survivor
    Returns the survivor's id if subsumption occurred, else None.

    Called from the consolidator after link_memory() to collapse semantic dupes
    that the LLM didn't recognize as PROMOTE-worthy.
    """
    from datetime import datetime
    try:
        candidates = list(
            Memory.select().where(
                Memory.archived_at.is_null(),
                Memory.id != str(memory.id),
            )
        )
    except Exception as exc:
        logger.warning("auto_promote_if_dupe: candidate load failed for %s: %s", memory.id, exc)
        return None
    if not candidates:
        return None
    neighbors = find_links_for_observation(
        memory,
        candidates,
        threshold=LINK_AUTO_PROMOTE_THRESHOLD,
        top_k=1,
    )
    if neighbors:
        target_id, score = neighbors[0]
    else:
        # Near-miss band: cosine missed it, but a paraphrase may still be a
        # duplicate. Escalate the best near-miss to an LLM judgment call.
        near = find_links_for_observation(
            memory,
            candidates,
            threshold=LINK_LLM_REVIEW_FLOOR,
            top_k=1,
        )
        if not near:
            return None
        cand_id, cand_score = near[0]
        if cand_score >= LINK_AUTO_PROMOTE_THRESHOLD:
            return None  # would have been caught above; defensive
        try:
            cand = Memory.get_by_id(cand_id)
        except Memory.DoesNotExist:
            return None
        if not _llm_confirms_duplicate(memory, cand):
            return None
        logger.info(
            "AUTO-PROMOTE: LLM confirmed paraphrase duplicate %s ~ %s (cosine=%.3f)",
            str(memory.id)[:8], str(cand_id)[:8], cand_score,
        )
        target_id, score = cand_id, cand_score
    try:
        target = Memory.get_by_id(target_id)
    except Memory.DoesNotExist:
        return None
    target.reinforcement_count = (target.reinforcement_count or 0) + 1
    target.save()
    Memory.update(
        archived_at=datetime.now().isoformat(),
        subsumed_by=target_id,
    ).where(Memory.id == str(memory.id)).execute()
    logger.info(
        "AUTO-PROMOTE: %s subsumed by %s (cosine=%.3f, target rc=%d)",
        str(memory.id)[:8], str(target_id)[:8], score, target.reinforcement_count,
    )
    return target_id


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
