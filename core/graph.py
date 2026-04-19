"""
Graph expansion — 1-hop neighbor discovery for hybrid search results.

Pre-computes edges between memories from:
1. Thread membership (memories in the same NarrativeThread)
2. Tag co-occurrence (memories sharing non-meta tags)

Incremental edge types (caused_by, refined_from, subsumed_into,
contradicts, echo) are created by pipeline steps and preserved
across recomputation.

After hybrid search returns seed memories, expands one hop to neighbors
via the memory_edges table. Neighbors are added to the candidate pool
and re-ranked before final selection, with causal edges prioritised.
"""

import json
import logging
import struct
from itertools import combinations

from .flags import get_flag
from .models import Memory, MemoryEdge, NarrativeThread, ThreadMember, db

logger = logging.getLogger(__name__)

# Priority order for expand_neighbors: lower = higher priority.
_EDGE_PRIORITY = {
    "caused_by": 0,
    "refined_from": 1,
    "subsumed_into": 2,
    "contradicts": 3,
    "echo": 3,
    "thread_neighbor": 4,
    "tag_cooccurrence": 5,
}


def compute_edges() -> int:
    """Pre-compute memory edges from threads and tag co-occurrence.

    Only clears recomputable edge types (thread_neighbor, tag_cooccurrence).
    Incremental edges (causal, contradiction, echo) are preserved.

    Returns the number of recomputable edges created.
    """
    # Clear only recomputable edges — preserve incremental ones
    MemoryEdge.delete().where(
        MemoryEdge.edge_type.in_(list(MemoryEdge.RECOMPUTABLE_TYPES))
    ).execute()

    edges_created = 0

    # 1. Thread-based edges: memories in the same thread are neighbors
    threads = list(NarrativeThread.select())
    for thread in threads:
        member_ids = [
            tm.memory_id for tm in
            ThreadMember.select().where(ThreadMember.thread_id == thread.id)
        ]
        for a, b in combinations(member_ids, 2):
            MemoryEdge.create(source_id=a, target_id=b, edge_type="thread_neighbor")
            MemoryEdge.create(source_id=b, target_id=a, edge_type="thread_neighbor")
            edges_created += 2

    # 2. Tag co-occurrence: memories sharing non-meta tags
    active_memories = list(Memory.active())
    tag_to_mids: dict[str, list[str]] = {}

    for mem in active_memories:
        for tag in mem.tag_list:
            # Skip meta-tags
            if tag.startswith("type:") or tag.startswith("valence:"):
                continue
            tag_to_mids.setdefault(tag, []).append(mem.id)

    for tag, mids in tag_to_mids.items():
        if len(mids) < 2 or len(mids) > 20:
            # Skip singletons and overly-common tags
            continue
        for a, b in combinations(mids, 2):
            # Check if a recomputable edge already exists (e.g. thread_neighbor)
            exists = MemoryEdge.select().where(
                MemoryEdge.source_id == a,
                MemoryEdge.target_id == b,
                MemoryEdge.edge_type.in_(list(MemoryEdge.RECOMPUTABLE_TYPES)),
            ).exists()
            if not exists:
                MemoryEdge.create(source_id=a, target_id=b, edge_type="tag_cooccurrence")
                MemoryEdge.create(source_id=b, target_id=a, edge_type="tag_cooccurrence")
                edges_created += 2

    logger.info("Computed %d recomputable edges", edges_created)
    return edges_created


def expand_neighbors(
    seed_ids: list[str],
    max_expansion: int = 10,
    vec_store=None,
) -> list[str]:
    """Expand seed memory IDs by one hop via memory_edges.

    Returns neighbor IDs not already in seed_ids, limited to max_expansion.
    Causal edge types are prioritised over structural ones.  Within the
    same priority tier, neighbors are ordered by sqlite-vec cosine
    similarity to the seed set centroid (when available).
    """
    if not get_flag("graph_expansion") or not seed_ids:
        return []

    # Fetch all candidate neighbor edges
    edges = list(
        MemoryEdge.select(MemoryEdge.target_id, MemoryEdge.edge_type, MemoryEdge.weight)
        .where(
            MemoryEdge.source_id.in_(seed_ids),
            MemoryEdge.target_id.not_in(seed_ids),
        )
    )

    if not edges:
        return []

    # Deduplicate: keep the highest-priority edge per target
    best: dict[str, tuple[int, float]] = {}  # target_id -> (priority, weight)
    for e in edges:
        prio = _EDGE_PRIORITY.get(e.edge_type, 99)
        existing = best.get(e.target_id)
        if existing is None or prio < existing[0]:
            best[e.target_id] = (prio, e.weight or 0.0)

    # Sort by (priority asc, weight desc)
    ranked = sorted(best.items(), key=lambda kv: (kv[1][0], -kv[1][1]))

    # Optional: use sqlite-vec centroid similarity as tiebreaker
    target_ids = [tid for tid, _ in ranked[:max_expansion * 2]]
    if vec_store is not None and vec_store.available and target_ids:
        similarities = _centroid_similarities(seed_ids, target_ids, vec_store)
        if similarities:
            # Re-rank within same priority tier using similarity
            ranked = _rerank_with_similarity(ranked, similarities)

    return [tid for tid, _ in ranked[:max_expansion]]


def _centroid_similarities(
    seed_ids: list[str],
    target_ids: list[str],
    vec_store,
) -> dict[str, float]:
    """Compute cosine similarity of each target to the seed set centroid.

    Returns {target_id: similarity} or empty dict if embeddings unavailable.
    """
    # Build seed centroid
    seed_vecs = []
    dim = None
    for sid in seed_ids:
        raw = vec_store.get_embedding(sid)
        if raw is None:
            continue
        d = len(raw) // 4
        if dim is None:
            dim = d
        seed_vecs.append(struct.unpack(f"{d}f", raw))

    if not seed_vecs or dim is None:
        return {}

    # Average to get centroid
    centroid = [0.0] * dim
    for vec in seed_vecs:
        for i, v in enumerate(vec):
            centroid[i] += v
    n = len(seed_vecs)
    centroid = [c / n for c in centroid]

    # Normalise centroid
    mag = sum(c * c for c in centroid) ** 0.5
    if mag < 1e-9:
        return {}
    centroid = [c / mag for c in centroid]

    # Score each target
    result = {}
    for tid in target_ids:
        raw = vec_store.get_embedding(tid)
        if raw is None:
            continue
        vec = struct.unpack(f"{dim}f", raw)
        dot = sum(a * b for a, b in zip(centroid, vec))
        result[tid] = max(0.0, min(1.0, dot))

    return result


def _rerank_with_similarity(
    ranked: list[tuple[str, tuple[int, float]]],
    similarities: dict[str, float],
) -> list[tuple[str, tuple[int, float]]]:
    """Re-sort within same priority tier using similarity scores."""
    def sort_key(item):
        tid, (prio, weight) = item
        sim = similarities.get(tid, 0.0)
        # Primary: priority asc.  Secondary: similarity desc.
        return (prio, -sim)

    return sorted(ranked, key=sort_key)
