"""
Graph expansion — 1-hop neighbor discovery for hybrid search results.

Pre-computes edges between memories from:
1. Thread membership (memories in the same NarrativeThread)
2. Tag co-occurrence (memories sharing non-meta tags)

After hybrid search returns seed memories, expands one hop to neighbors
via the memory_edges table. Neighbors are added to the candidate pool
and re-ranked before final selection.
"""

import json
import logging
from itertools import combinations

from .models import Memory, MemoryEdge, NarrativeThread, ThreadMember, db

logger = logging.getLogger(__name__)


def compute_edges() -> int:
    """Pre-compute memory edges from threads and tag co-occurrence.

    Clears existing edges and rebuilds from scratch.
    Returns the number of edges created.
    """
    # Clear existing edges
    MemoryEdge.delete().execute()

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
            # Check if edge already exists (from thread)
            exists = MemoryEdge.select().where(
                MemoryEdge.source_id == a,
                MemoryEdge.target_id == b,
            ).exists()
            if not exists:
                MemoryEdge.create(source_id=a, target_id=b, edge_type="tag_cooccurrence")
                MemoryEdge.create(source_id=b, target_id=a, edge_type="tag_cooccurrence")
                edges_created += 2

    logger.info("Computed %d memory edges", edges_created)
    return edges_created


def expand_neighbors(seed_ids: list[str], max_expansion: int = 10) -> list[str]:
    """Expand seed memory IDs by one hop via memory_edges.

    Returns neighbor IDs not already in seed_ids, limited to max_expansion.
    """
    from .flags import get_flag

    if not get_flag("graph_expansion") or not seed_ids:
        return []

    neighbors = (
        MemoryEdge.select(MemoryEdge.target_id)
        .where(
            MemoryEdge.source_id.in_(seed_ids),
            MemoryEdge.target_id.not_in(seed_ids),
        )
        .distinct()
        .limit(max_expansion)
    )

    return [e.target_id for e in neighbors]
