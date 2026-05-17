"""
Stored-vs-stored contradiction resolution lifecycle.

Exports:
  has_blocking_contradiction(memory_id) -> tuple[bool, str]
  resolve_contradictions_pass(session_id) -> dict
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from core.llm import call_llm
from core.models import ContradictionReview, ConsolidationLog, Memory, MemoryEdge
from core.prompts import STORED_CONTRADICTION_RESOLUTION_PROMPT
from core.schemas import StoredContradictionVerdict

logger = logging.getLogger(__name__)

MAX_LLM_RETRIES = 3


# ---------------------------------------------------------------------------
# Public: promotion gate
# ---------------------------------------------------------------------------


def has_blocking_contradiction(memory_id: str) -> tuple[bool, str]:
    """Return (True, reason) if any incident contradicts edge is unresolved or queued."""
    blocking = (
        MemoryEdge.select()
        .where(
            (
                (MemoryEdge.source_id == memory_id)
                | (MemoryEdge.target_id == memory_id)
            ),
            MemoryEdge.edge_type == "contradicts",
            MemoryEdge.resolution_state.in_(["unresolved", "queued"]),
        )
        .first()
    )
    if blocking is None:
        return False, ""
    other_id = (
        blocking.target_id if blocking.source_id == memory_id else blocking.source_id
    )
    return True, f"edge {blocking.id} vs {other_id[:8]} ({blocking.resolution_state})"


# ---------------------------------------------------------------------------
# Public: async resolver pass
# ---------------------------------------------------------------------------


def resolve_contradictions_pass(session_id: str) -> dict:
    """Sweep all unresolved contradicts edges and resolve or queue them.

    Steps:
    1. C-recheck: for open review rows whose edge is resolved/queued, recompute
       fingerprint; if changed → close row, flip edge back to unresolved.
    2. Main sweep: resolve each unresolved edge (skip both-instinctive pairs).
    3. Bidirectional sync: copy final state to reverse edge.

    Returns counts dict: resolved, queued, skipped, rechecked.
    """
    counts: dict[str, int] = {"resolved": 0, "queued": 0, "skipped": 0, "rechecked": 0}

    # --- C-recheck ---
    # Edges reset here are deferred to the *next* cron pass so this run doesn't
    # immediately re-resolve them (which would undo the fingerprint-change signal).
    rechecked_edge_ids: set[int] = set()
    open_reviews = list(
        ContradictionReview.select().where(ContradictionReview.status == "open")
    )
    for review in open_reviews:
        try:
            edge = MemoryEdge.get_by_id(review.edge_id)
        except MemoryEdge.DoesNotExist:
            continue
        if edge.resolution_state not in ("resolved", "queued"):
            continue
        try:
            mem_a = Memory.get_by_id(review.memory_id)
            mem_b = Memory.get_by_id(review.other_memory_id)
        except Memory.DoesNotExist:
            continue
        new_fp = _recheck_fingerprint(mem_a, mem_b)
        if new_fp != review.recheck_fingerprint:
            ContradictionReview.update(
                status="resolved",
                llm_rationale="superseded-by-recheck",
                resolved_at=datetime.now(timezone.utc).isoformat(),
            ).where(ContradictionReview.id == review.id).execute()
            MemoryEdge.update(resolution_state="unresolved").where(
                MemoryEdge.id == edge.id
            ).execute()
            rechecked_edge_ids.add(edge.id)
            counts["rechecked"] += 1
            logger.info("C-recheck: edge %d fingerprint changed, reset to unresolved", edge.id)

    # --- Main sweep ---
    unresolved_edges = list(
        MemoryEdge.select()
        .where(
            MemoryEdge.edge_type == "contradicts",
            MemoryEdge.resolution_state == "unresolved",
        )
    )

    processed_pairs: set[frozenset[str]] = set()

    for edge in unresolved_edges:
        if edge.id in rechecked_edge_ids:
            continue
        pair = frozenset([edge.source_id, edge.target_id])
        if pair in processed_pairs:
            continue

        try:
            mem_a = Memory.get_by_id(edge.source_id)
            mem_b = Memory.get_by_id(edge.target_id)
        except Memory.DoesNotExist:
            MemoryEdge.update(resolution_state="resolved").where(
                MemoryEdge.id == edge.id
            ).execute()
            counts["resolved"] += 1
            continue

        if mem_a.stage == "instinctive" and mem_b.stage == "instinctive":
            counts["skipped"] += 1
            continue

        result = _resolve_edge(edge, session_id)
        final_state = _edge_state(edge.id)

        # Sync reverse edge
        reverse = (
            MemoryEdge.select()
            .where(
                MemoryEdge.source_id == edge.target_id,
                MemoryEdge.target_id == edge.source_id,
                MemoryEdge.edge_type == "contradicts",
            )
            .first()
        )
        if reverse is not None:
            MemoryEdge.update(resolution_state=final_state).where(
                MemoryEdge.id == reverse.id
            ).execute()

        processed_pairs.add(pair)

        if final_state == "resolved":
            counts["resolved"] += 1
        elif final_state == "queued":
            counts["queued"] += 1

    logger.info(
        "resolve_contradictions_pass: %s",
        ", ".join(f"{k}={v}" for k, v in counts.items()),
    )
    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _edge_state(edge_id: int) -> str:
    try:
        return MemoryEdge.get_by_id(edge_id).resolution_state
    except MemoryEdge.DoesNotExist:
        return "resolved"


def _recheck_fingerprint(mem_a: Memory, mem_b: Memory) -> str:
    """SHA-256 over both memories' (id, content, stage), sorted by id."""
    parts = sorted(
        [(mem_a.id, mem_a.content or "", mem_a.stage),
         (mem_b.id, mem_b.content or "", mem_b.stage)],
        key=lambda t: t[0],
    )
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _apply_verdict(
    verdict: StoredContradictionVerdict,
    edge: MemoryEdge,
    session_id: str,
) -> None:
    """Mutate memories in DB according to the verdict."""
    now = datetime.now(timezone.utc).isoformat()

    if verdict.verdict in ("SUPERSEDE", "ARCHIVE"):
        winner_id = verdict.winner_id
        loser_id = (
            edge.target_id if edge.source_id == winner_id else edge.source_id
        )
        try:
            loser = Memory.get_by_id(loser_id)
        except Memory.DoesNotExist:
            return
        loser.content = f"[Superseded] {loser.content or ''}"
        loser.title = f"[Superseded] {loser.title or ''}"
        loser.save()
        Memory.update(archived_at=now).where(Memory.id == loser_id).execute()
        ConsolidationLog.create(
            timestamp=now,
            session_id=session_id,
            action="deprecated",
            memory_id=loser_id,
            from_stage=loser.stage,
            to_stage=loser.stage,
            rationale=f"Contradiction resolved ({verdict.verdict}): {verdict.rationale[:200]}",
        )

    elif verdict.verdict == "REFINE":
        winner_id = verdict.winner_id
        loser_id = (
            edge.target_id if edge.source_id == winner_id else edge.source_id
        )
        try:
            winner = Memory.get_by_id(winner_id)
            loser = Memory.get_by_id(loser_id)
        except Memory.DoesNotExist:
            return
        winner.content = verdict.merged_content
        winner.save()
        loser.content = f"[Superseded] {loser.content or ''}"
        loser.title = f"[Superseded] {loser.title or ''}"
        loser.save()
        Memory.update(archived_at=now).where(Memory.id == loser_id).execute()
        ConsolidationLog.create(
            timestamp=now,
            session_id=session_id,
            action="merged",
            memory_id=winner_id,
            from_stage=winner.stage,
            to_stage=winner.stage,
            rationale=f"Contradiction refined: {verdict.rationale[:200]}",
        )


def _resolve_edge(edge: MemoryEdge, session_id: str) -> None:
    """Resolve a single contradicts edge via one LLM call."""
    try:
        mem_a = Memory.get_by_id(edge.source_id)
        mem_b = Memory.get_by_id(edge.target_id)
    except Memory.DoesNotExist:
        MemoryEdge.update(resolution_state="resolved").where(
            MemoryEdge.id == edge.id
        ).execute()
        return

    # Instinctive guard — no LLM call
    if mem_a.stage == "instinctive" and mem_b.stage == "instinctive":
        _queue_edge(edge, mem_a, mem_b, rationale="both-instinctive-guard")
        return

    metadata = {}
    if edge.metadata:
        try:
            metadata = json.loads(edge.metadata)
        except (json.JSONDecodeError, TypeError):
            pass
    detection_rationale = metadata.get("rationale", metadata.get("evidence", ""))

    prompt = STORED_CONTRADICTION_RESOLUTION_PROMPT.format(
        memory_a_id=mem_a.id,
        memory_a_stage=mem_a.stage,
        memory_a_title=mem_a.title or "",
        memory_a_content=mem_a.content or "",
        memory_b_id=mem_b.id,
        memory_b_stage=mem_b.stage,
        memory_b_title=mem_b.title or "",
        memory_b_content=mem_b.content or "",
        detection_rationale=detection_rationale,
    )

    try:
        raw = call_llm(prompt, max_tokens=512, temperature=0)
        parsed = _extract_json(raw)
        verdict = StoredContradictionVerdict.model_validate(parsed)
    except Exception as exc:
        logger.warning("LLM resolution error for edge %d: %s", edge.id, exc)
        _handle_llm_error(edge, mem_a, mem_b, str(exc))
        return

    if verdict.verdict == "BLOCK":
        _queue_edge(edge, mem_a, mem_b, rationale=verdict.rationale)
        return

    _apply_verdict(verdict, edge, session_id)
    MemoryEdge.update(resolution_state="resolved").where(
        MemoryEdge.id == edge.id
    ).execute()


def _extract_json(raw: str) -> dict:
    """Extract the first {...} block from LLM output."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM output: {raw[:200]!r}")
    return json.loads(match.group(0))


def _queue_edge(
    edge: MemoryEdge,
    mem_a: Memory,
    mem_b: Memory,
    rationale: str,
) -> None:
    """Mark edge queued and ensure an open ContradictionReview row exists."""
    MemoryEdge.update(resolution_state="queued").where(
        MemoryEdge.id == edge.id
    ).execute()
    existing = (
        ContradictionReview.select()
        .where(
            ContradictionReview.edge_id == edge.id,
            ContradictionReview.status == "open",
        )
        .first()
    )
    if existing is None:
        fp = _recheck_fingerprint(mem_a, mem_b)
        ContradictionReview.create(
            memory_id=mem_a.id,
            edge_id=edge.id,
            other_memory_id=mem_b.id,
            project=getattr(mem_a, "project", None),
            llm_rationale=rationale,
            status="open",
            created_at=datetime.now(timezone.utc).isoformat(),
            recheck_fingerprint=fp,
            retry_count=0,
        )


def _handle_llm_error(
    edge: MemoryEdge,
    mem_a: Memory,
    mem_b: Memory,
    error_msg: str,
) -> None:
    """Increment retry count; at MAX_LLM_RETRIES flip edge to queued."""
    existing = (
        ContradictionReview.select()
        .where(
            ContradictionReview.edge_id == edge.id,
            ContradictionReview.status == "open",
        )
        .first()
    )
    if existing is None:
        fp = _recheck_fingerprint(mem_a, mem_b)
        existing = ContradictionReview.create(
            memory_id=mem_a.id,
            edge_id=edge.id,
            other_memory_id=mem_b.id,
            project=getattr(mem_a, "project", None),
            llm_rationale=f"resolution-llm-error: {error_msg[:200]}",
            status="open",
            created_at=datetime.now(timezone.utc).isoformat(),
            recheck_fingerprint=fp,
            retry_count=0,
        )

    new_count = existing.retry_count + 1
    ContradictionReview.update(retry_count=new_count).where(
        ContradictionReview.id == existing.id
    ).execute()

    if new_count >= MAX_LLM_RETRIES:
        MemoryEdge.update(resolution_state="queued").where(
            MemoryEdge.id == edge.id
        ).execute()
        logger.warning(
            "Edge %d hit MAX_LLM_RETRIES (%d), converted to queued BLOCK",
            edge.id,
            MAX_LLM_RETRIES,
        )
