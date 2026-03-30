"""
Reconsolidation — update injected memories based on session evidence.

At PreCompact, compares injected memories against session content to detect:
- confirmations: session reinforces the memory (bump reinforcement_count)
- contradictions: session contradicts the memory (flag for review)
- refinements: session adds nuance or detail (append to content)

One batched LLM call per PreCompact, not per-memory.

When causal_edges is enabled, creates directed edges between memories
involved in contradictions and refinements, using sqlite-vec cosine
similarity to select the most semantically relevant co-injected targets.
"""

import json
import logging
import struct
from datetime import datetime

from .llm import call_llm, strip_markdown_fences
from .models import Memory, ConsolidationLog, MemoryEdge

logger = logging.getLogger(__name__)

# Maximum causal edges to create per reconsolidation event.
_MAX_CAUSAL_EDGES = 3

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

    # Capture which memories were already flagged before this session processes them.
    # Used by _create_contradiction_edges to determine superseded vs active tension.
    pre_flagged_ids = {m.id for m in memories if "contradiction_flagged" in m.tag_list}

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

    # Create causal edges for refined/contradicted memories
    if get_flag("causal_edges"):
        _create_causal_edges(decisions, mem_by_id, injected_ids, session_id, now)

    # Create bidirectional contradiction edges
    if get_flag("contradiction_tensors"):
        _create_contradiction_edges(
            decisions, mem_by_id, result["confirmed"], pre_flagged_ids, session_id, now
        )

    return result


def _create_causal_edges(
    decisions: list[dict],
    mem_by_id: dict[str, "Memory"],
    injected_ids: list[str],
    session_id: str,
    timestamp: str,
) -> None:
    """Create directed causal edges from reconsolidation decisions.

    For each refined/contradicted memory, finds the most semantically
    related co-injected memories via sqlite-vec cosine similarity and
    creates edges to them.  Falls back to confirmed co-injected memories
    if embeddings are unavailable.
    """
    # Collect affected memory IDs and their actions
    affected = []
    confirmed_ids = set()
    for d in decisions:
        mid = d.get("memory_id", "")
        action = d.get("action", "unmentioned")
        if mid not in mem_by_id:
            continue
        if action in ("refined", "contradicted"):
            affected.append((mid, action, d.get("evidence", "")))
        elif action == "confirmed":
            confirmed_ids.add(mid)

    if not affected:
        return

    # Build the pool of potential targets: co-injected memories excluding the
    # affected one itself.  Prefer confirmed memories (the session validated
    # them), but include all injected as fallback.
    all_ids = set(injected_ids)

    for mid, action, evidence in affected:
        edge_type = "refined_from" if action == "refined" else "caused_by"
        pool = (confirmed_ids - {mid}) or (all_ids - {mid})
        if not pool:
            continue

        # Use sqlite-vec to rank the pool by similarity to the affected memory
        targets = _rank_by_similarity(mid, list(pool), limit=_MAX_CAUSAL_EDGES)

        meta = json.dumps({
            "evidence": evidence,
            "session_id": session_id,
            "created_at": timestamp,
        })

        for target_id, similarity in targets:
            # Avoid duplicate edges
            exists = MemoryEdge.select().where(
                MemoryEdge.source_id == mid,
                MemoryEdge.target_id == target_id,
                MemoryEdge.edge_type == edge_type,
            ).exists()
            if not exists:
                MemoryEdge.create(
                    source_id=mid,
                    target_id=target_id,
                    edge_type=edge_type,
                    weight=similarity,
                    metadata=meta,
                )
                logger.info(
                    "Causal edge: %s -[%s]-> %s (sim=%.3f)",
                    mid[:8], edge_type, target_id[:8], similarity,
                )


def _create_contradiction_edges(
    decisions: list[dict],
    mem_by_id: dict[str, "Memory"],
    confirmed_ids: list[str],
    pre_flagged_ids: set[str],
    session_id: str,
    timestamp: str,
) -> None:
    """Create bidirectional contradicts edges from reconsolidation decisions.

    For each memory with action == "contradicted", creates edges between it
    and each confirmed memory in the same session.  Both directions are
    created: (contradicted → confirmed) and (confirmed → contradicted).

    Edges are marked resolved=False (active tension) by default.  If the
    contradicted memory was already carrying the "contradiction_flagged" tag
    before this session (i.e. it was contradicted in a prior session), the
    earlier position has been superseded; both edges are created with
    resolved=True and resolution="superseded".

    Args:
        decisions: Raw LLM decision list.
        mem_by_id: Memory objects keyed by ID.
        confirmed_ids: IDs that received action == "confirmed" this session.
        pre_flagged_ids: IDs that had "contradiction_flagged" tag before this
            session's main loop ran.
        session_id: Current session identifier.
        timestamp: ISO timestamp for created_at / detected_at fields.
    """
    confirmed_set = set(confirmed_ids)

    for d in decisions:
        mid = d.get("memory_id", "")
        action = d.get("action", "unmentioned")
        evidence = d.get("evidence", "")

        if mid not in mem_by_id or action != "contradicted":
            continue

        if not confirmed_set:
            continue

        # A memory flagged in a prior session is superseded; a first-time
        # contradiction is an active tension (resolved=False).
        already_flagged = mid in pre_flagged_ids
        resolved = already_flagged
        resolution = "superseded" if already_flagged else None

        for confirmed_id in confirmed_set:
            if confirmed_id == mid:
                continue

            meta = json.dumps({
                "evidence": evidence,
                "session_id": session_id,
                "created_at": timestamp,
                "resolved": resolved,
                "resolution": resolution,
                "detected_by": "reconsolidation",
                "detected_at": timestamp,
            })

            # A → B
            a_to_b_exists = MemoryEdge.select().where(
                MemoryEdge.source_id == mid,
                MemoryEdge.target_id == confirmed_id,
                MemoryEdge.edge_type == "contradicts",
            ).exists()
            if not a_to_b_exists:
                MemoryEdge.create(
                    source_id=mid,
                    target_id=confirmed_id,
                    edge_type="contradicts",
                    weight=0.7,
                    metadata=meta,
                )
                logger.info(
                    "Contradiction edge: %s -[contradicts]-> %s (resolved=%s)",
                    mid[:8], confirmed_id[:8], resolved,
                )

            # B → A
            b_to_a_exists = MemoryEdge.select().where(
                MemoryEdge.source_id == confirmed_id,
                MemoryEdge.target_id == mid,
                MemoryEdge.edge_type == "contradicts",
            ).exists()
            if not b_to_a_exists:
                MemoryEdge.create(
                    source_id=confirmed_id,
                    target_id=mid,
                    edge_type="contradicts",
                    weight=0.7,
                    metadata=meta,
                )
                logger.info(
                    "Contradiction edge: %s -[contradicts]-> %s (resolved=%s)",
                    confirmed_id[:8], mid[:8], resolved,
                )


def _rank_by_similarity(
    source_id: str,
    candidate_ids: list[str],
    limit: int = 3,
) -> list[tuple[str, float]]:
    """Rank candidate memories by cosine similarity to source via sqlite-vec.

    Returns list of (memory_id, similarity) tuples, highest first.
    Falls back to returning all candidates with weight 0.5 if embeddings
    are unavailable.
    """
    from .database import get_vec_store

    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        return [(cid, 0.5) for cid in candidate_ids[:limit]]

    # Get the source embedding
    source_embedding = vec_store.get_embedding(source_id)
    if source_embedding is None:
        return [(cid, 0.5) for cid in candidate_ids[:limit]]

    # Compute pairwise similarities using stored embeddings
    source_dim = len(source_embedding) // 4
    source_vec = struct.unpack(f"{source_dim}f", source_embedding)

    scored = []
    for cid in candidate_ids:
        cand_embedding = vec_store.get_embedding(cid)
        if cand_embedding is None:
            scored.append((cid, 0.5))
            continue

        cand_vec = struct.unpack(f"{source_dim}f", cand_embedding)

        # Cosine similarity (embeddings are pre-normalized by Titan v2)
        dot = sum(a * b for a, b in zip(source_vec, cand_vec))
        scored.append((cid, max(0.0, min(1.0, dot))))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
