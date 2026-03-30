"""
Narrative thread engine — detects and synthesizes episodic arcs across memories.

Human memory doesn't just store facts — it builds stories. "I kept reaching for
threads, got corrected, learned asyncio is idiomatic here" is more useful than
three separate facts. The narrative arc makes knowledge sticky because it encodes
the journey, not just the destination.

This module:
1. ThreadDetector — finds clusters of memories that share a temporal + topical
   arc (corrections that built on each other, preferences that evolved, etc.)
2. ThreadNarrator — calls Claude to synthesize a cluster into a narrative
3. Integration point for RetrievalEngine — threads can be injected alongside
   or instead of individual memories
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from .database import get_vec_store
from .llm import call_llm
from .models import Memory, MemoryEdge, NarrativeThread, ThreadMember, db

logger = logging.getLogger(__name__)


def _get_embeddings(memories: list):
    """Retrieve stored embeddings from vec_memories."""
    try:
        import struct
        import numpy as np
    except ImportError:
        return None

    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        return None

    embeddings = []
    for m in memories:
        mid = m.id if hasattr(m, 'id') and not isinstance(m, dict) else m["id"]
        raw = vec_store.get_embedding(mid)
        if raw is None:
            return None
        vec = struct.unpack(f"{len(raw)//4}f", raw)
        embeddings.append(vec)

    return np.array(embeddings, dtype=np.float32)

# ---------------------------------------------------------------------------
# Narrative synthesis prompt
# ---------------------------------------------------------------------------

NARRATIVE_PROMPT = """You are building a narrative thread from a sequence of related memories.

These memories share a topical connection and were created over time. Your job is to
tell the STORY of how understanding evolved — not just summarize the facts.

MEMORIES (in chronological order):
{memories}

YOUR TASK: Synthesize these into a narrative arc.

WHAT MAKES A GOOD NARRATIVE:
- Shows the JOURNEY: what was believed first, what changed, what's understood now
- Preserves the corrections — knowing I was wrong is as valuable as knowing I'm right
- Includes the WHY behind changes ("got corrected because..." not just "changed to...")
- Is denser than listing the memories but preserves the temporal structure
- Ends with the current state of understanding — what would I do NOW because of this arc

EXAMPLES:
- Three corrections about async patterns →
  "Started by reaching for threading (familiar from training data). Got corrected: this
   codebase uses asyncio because the event loop architecture requires cooperative
   scheduling. Tried again with asyncio but used `run_in_executor` as a crutch — corrected
   again: native coroutines are preferred. Third session: used pure async/await correctly.
   The pattern: check the runtime model before choosing concurrency primitives."

- Two preferences that evolved →
  "Initially suggested splitting the auth refactor into 3 PRs by layer. Emma pushed back:
   one PR is less review overhead when the change is coherent. Later, a large cross-cutting
   change was split by abstraction layer — that one made sense to split. The principle:
   PR sizing follows coherence, not size. A single-layer refactor stays together; a
   cross-cutting change splits by boundary."

RULES:
- Title should capture the arc theme (e.g., "Learning async patterns in this codebase")
- Maximum 5 sentences for the narrative body
- Write in first person — this is MY journey of understanding
- If memories don't form a real arc (just related facts), say so — don't force a story
- End with a "Current understanding:" line that captures the takeaway

Respond ONLY with valid JSON:
{{
  "title": "Arc theme (what was learned through this journey)",
  "narrative": "The story of how understanding evolved",
  "current_understanding": "What I'd do now because of this arc",
  "arc_type": "correction_chain|preference_evolution|knowledge_building|pattern_discovery",
  "confidence": 0.0-1.0
}}"""


class ThreadDetector:
    """
    Finds clusters of memories that form narrative arcs.
    """

    # Minimum memories to form a thread
    MIN_CLUSTER_SIZE = 2
    # Maximum age gap between any two memories in a cluster (days)
    MAX_AGE_SPAN_DAYS = 90
    # Minimum tag overlap ratio to consider memories related
    MIN_TAG_OVERLAP = 0.3

    def __init__(self):
        pass

    def detect_threads(
        self,
        stages: list[str] = None,
        exclude_threaded: bool = True,
    ) -> list[list]:
        """
        Find memory clusters that could form narrative threads.
        """
        if stages is None:
            stages = ["consolidated", "crystallized"]

        # Gather candidate memories
        candidates = []
        for stage in stages:
            candidates.extend(list(Memory.by_stage(stage)))

        if exclude_threaded:
            candidates = self._exclude_already_threaded(candidates)

        if len(candidates) < self.MIN_CLUSTER_SIZE:
            return []

        # Load full content for clustering
        full_candidates = []
        for c in candidates:
            try:
                mem = Memory.get_by_id(c.id)
                full_candidates.append(mem)
            except Memory.DoesNotExist:
                continue

        # Try embedding-based clustering first
        embeddings = _get_embeddings(full_candidates)

        if embeddings is not None:
            clusters = self._cluster_by_embeddings(full_candidates, embeddings, threshold=0.70)
        else:
            clusters = self._cluster_by_tags(full_candidates)

        # Filter clusters that are too small or too spread
        valid_clusters = []
        for cluster in clusters:
            if len(cluster) >= self.MIN_CLUSTER_SIZE:
                sorted_cluster = sorted(cluster, key=lambda m: m.created_at or "")
                if self._has_temporal_spread(sorted_cluster):
                    valid_clusters.append(sorted_cluster)

        return valid_clusters

    def _exclude_already_threaded(self, candidates: list) -> list:
        """Remove memories that already belong to a thread."""
        result = []
        for c in candidates:
            threads = (
                NarrativeThread.select()
                .join(ThreadMember, on=(NarrativeThread.id == ThreadMember.thread_id))
                .where(ThreadMember.memory_id == c.id)
            )
            if not list(threads):
                result.append(c)
        return result

    def _cluster_by_tags(self, memories: list) -> list[list]:
        """Group memories by tag overlap using greedy transitive clustering."""
        generic_prefixes = {"source:", "stage:"}

        def get_meaningful_tags(mem) -> set[str]:
            tags = mem.tag_list if hasattr(mem, 'tag_list') else []
            return {
                t for t in tags
                if not any(t.startswith(p) for p in generic_prefixes)
            }

        n = len(memories)
        if n == 0:
            return []

        # Build adjacency by tag overlap
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        tags_list = [get_meaningful_tags(m) for m in memories]

        for i in range(n):
            for j in range(i + 1, n):
                ti, tj = tags_list[i], tags_list[j]
                if not ti or not tj:
                    continue
                overlap = len(ti & tj)
                min_size = min(len(ti), len(tj))
                if min_size > 0 and overlap / min_size >= self.MIN_TAG_OVERLAP:
                    union(i, j)

        # Collect clusters
        groups: dict[int, list] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(memories[i])

        return list(groups.values())

    def _cluster_by_embeddings(
        self,
        memories: list,
        embeddings,  # numpy array (N, 512)
        threshold: float,
    ) -> list[list]:
        """Group memories using cosine similarity on embeddings with union-find."""
        import numpy as np

        n = len(memories)
        if n == 0:
            return []

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normed = embeddings / np.maximum(norms, 1e-9)
        sims = normed @ normed.T

        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                if sims[i, j] >= threshold:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

        groups: dict[int, list] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(memories[i])

        return list(groups.values())

    def _has_temporal_spread(self, sorted_cluster: list) -> bool:
        """Check that a cluster spans multiple sessions."""
        if len(sorted_cluster) < 2:
            return False

        try:
            oldest = datetime.fromisoformat(sorted_cluster[0].created_at)
            newest = datetime.fromisoformat(sorted_cluster[-1].created_at)
        except (ValueError, TypeError, AttributeError):
            return False

        span = newest - oldest
        return timedelta(hours=1) <= span <= timedelta(days=self.MAX_AGE_SPAN_DAYS)


class ThreadNarrator:
    """
    Synthesizes memory clusters into narrative threads via LLM.
    """

    def __init__(self):
        pass

    def narrate_cluster(self, cluster: list) -> Optional[dict]:
        """
        Synthesize a memory cluster into a narrative thread.
        """
        if not cluster:
            return None

        # Format memories for the prompt
        mem_parts = []
        for i, mem in enumerate(cluster, 1):
            title = mem.title or "Untitled"
            created = mem.created_at or "unknown"
            content = mem.content or ""
            content = self._strip_frontmatter(content)
            mem_parts.append(
                f"[{i}] **{title}** (created: {created})\n{content}"
            )

        memories_text = "\n\n".join(mem_parts)
        prompt = NARRATIVE_PROMPT.format(memories=memories_text)

        try:
            raw = call_llm(prompt, max_tokens=1024, temperature=0.3)
            result = json.loads(raw)
        except Exception:
            return None

        confidence = result.get("confidence", 0.0)
        if confidence < 0.4:
            return None

        narrative_body = result["narrative"]
        current = result.get("current_understanding", "")
        if current:
            narrative_body += f"\n\n**Current understanding:** {current}"

        return {
            "title": result["title"],
            "summary": result["narrative"][:150],
            "narrative": narrative_body,
            "arc_type": result.get("arc_type", "knowledge_building"),
            "confidence": confidence,
            "member_ids": [m.id for m in cluster],
        }

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        lines = content.split("\n")
        for i in range(1, len(lines)):
            if lines[i] == "---":
                return "\n".join(lines[i + 1:]).strip()
        return content


def build_threads() -> list[dict]:
    """
    End-to-end thread building: detect clusters, narrate them, persist.

    Returns list of created thread dicts.
    """
    from .flags import get_flag

    detector = ThreadDetector()
    narrator = ThreadNarrator()

    clusters = detector.detect_threads()
    created = []

    for cluster in clusters:
        result = narrator.narrate_cluster(cluster)
        if result is None:
            continue

        now = datetime.now().isoformat()

        # Validate all member IDs exist
        member_ids = result["member_ids"]
        valid_ids = []
        for mid in member_ids:
            try:
                Memory.get_by_id(mid)
                valid_ids.append(mid)
            except Memory.DoesNotExist:
                continue

        if not valid_ids:
            continue

        thread = NarrativeThread.create(
            title=result["title"],
            summary=result["summary"],
            narrative=result["narrative"],
            created_at=now,
            updated_at=now,
        )

        for position, mid in enumerate(valid_ids):
            ThreadMember.create(
                thread_id=thread.id,
                memory_id=mid,
                position=position,
            )

        result["id"] = thread.id
        created.append(result)

        # Create resolved contradiction edges for correction_chain threads
        if result.get("arc_type") == "correction_chain" and get_flag("contradiction_tensors"):
            _create_thread_contradiction_edges(thread.id, valid_ids, now)

    return created


def _create_thread_contradiction_edges(
    thread_id: str,
    member_ids: list[str],
    timestamp: str,
) -> None:
    """Create resolved contradicts edges between early and late members of a
    correction_chain thread.

    The member_ids list is in chronological order (position order).  We split
    at the median: positions 0..median-1 are "early" (the corrected position)
    and positions median..-1 are "late" (the current understanding).  We then
    create bidirectional contradicts edges between early[0] and late[-1].

    Weight is 0.3 — historical contradiction, already resolved.
    """
    n = len(member_ids)
    if n < 2:
        return

    median = n // 2
    early_ids = member_ids[:median]
    late_ids = member_ids[median:]

    source_id = early_ids[0]
    target_id = late_ids[-1]

    meta = json.dumps({
        "evidence": f"correction_chain thread: {thread_id}",
        "thread_id": thread_id,
        "arc_type": "correction_chain",
        "resolved": True,
        "resolution": "correction_chain",
        "created_at": timestamp,
        "detected_at": timestamp,
        "detected_by": "thread_narrator",
    })

    for src, tgt in [(source_id, target_id), (target_id, source_id)]:
        exists = MemoryEdge.select().where(
            MemoryEdge.source_id == src,
            MemoryEdge.target_id == tgt,
            MemoryEdge.edge_type == "contradicts",
        ).exists()
        if not exists:
            MemoryEdge.create(
                source_id=src,
                target_id=tgt,
                edge_type="contradicts",
                weight=0.3,
                metadata=meta,
            )
            logger.info(
                "Thread contradiction edge: %s -[contradicts]-> %s (thread=%s)",
                src[:8], tgt[:8], thread_id[:8],
            )
