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
import re
from datetime import datetime, timedelta
from typing import Optional

from .llm import call_llm
from .storage import MemoryStore


def _get_embeddings(store, memories: list[dict]):
    """Retrieve stored embeddings from vec_memories."""
    try:
        import struct
        import numpy as np
    except ImportError:
        return None

    embeddings = []
    for m in memories:
        raw = store.get_embedding(m["id"])
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

    A narrative arc is a sequence of memories that:
    1. Share topical overlap (tag intersection or FTS similarity)
    2. Have temporal spread (created across multiple sessions/days)
    3. Show evolution (corrections, refinements, or building knowledge)

    The detector does NOT require all three — two of three is sufficient.
    The narrator will reject false arcs during synthesis.
    """

    # Minimum memories to form a thread
    MIN_CLUSTER_SIZE = 2
    # Maximum age gap between any two memories in a cluster (days)
    MAX_AGE_SPAN_DAYS = 90
    # Minimum tag overlap ratio to consider memories related
    MIN_TAG_OVERLAP = 0.3

    def __init__(self, store: MemoryStore):
        self.store = store

    def detect_threads(
        self,
        stages: list[str] = None,
        exclude_threaded: bool = True,
    ) -> list[list[dict]]:
        """
        Find memory clusters that could form narrative threads.

        Args:
            stages: Which stages to scan. Defaults to consolidated + crystallized.
            exclude_threaded: Skip memories already in a thread.

        Returns:
            List of clusters, where each cluster is an ordered list of memory
            dicts (chronological).
        """
        if stages is None:
            stages = ["consolidated", "crystallized"]

        # Gather candidate memories
        candidates = []
        for stage in stages:
            candidates.extend(self.store.list_by_stage(stage))

        if exclude_threaded:
            candidates = self._exclude_already_threaded(candidates)

        if len(candidates) < self.MIN_CLUSTER_SIZE:
            return []

        # Load full content for clustering
        full_candidates = []
        for c in candidates:
            try:
                mem = self.store.get(c["id"])
                full_candidates.append(mem)
            except (KeyError, ValueError):
                continue

        # Try embedding-based clustering first (D-09: threshold 0.70 — topical
        # overlap, not content convergence). Fall back to tag overlap.
        embeddings = _get_embeddings(self.store, full_candidates)

        if embeddings is not None:
            clusters = self._cluster_by_embeddings(full_candidates, embeddings, threshold=0.70)
        else:
            clusters = self._cluster_by_tags(full_candidates)

        # Filter clusters that are too small or too spread
        valid_clusters = []
        for cluster in clusters:
            if len(cluster) >= self.MIN_CLUSTER_SIZE:
                sorted_cluster = sorted(cluster, key=lambda m: m.get("created_at", ""))
                if self._has_temporal_spread(sorted_cluster):
                    valid_clusters.append(sorted_cluster)

        return valid_clusters

    def _exclude_already_threaded(self, candidates: list[dict]) -> list[dict]:
        """Remove memories that already belong to a thread."""
        result = []
        for c in candidates:
            threads = self.store.get_threads_for_memory(c["id"])
            if not threads:
                result.append(c)
        return result

    def _cluster_by_tags(self, memories: list[dict]) -> list[list[dict]]:
        """
        Group memories by tag overlap using greedy transitive clustering.

        Two memories are related if they share at least MIN_TAG_OVERLAP
        fraction of their tags (excluding generic tags like 'source:*').
        """
        generic_prefixes = {"source:", "stage:"}

        def get_meaningful_tags(mem: dict) -> set[str]:
            tags = mem.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []
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
        groups: dict[int, list[dict]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(memories[i])

        return list(groups.values())

    def _cluster_by_embeddings(
        self,
        memories: list[dict],
        embeddings,  # numpy array (N, 512)
        threshold: float,
    ) -> list[list[dict]]:
        """
        Group memories using cosine similarity on embeddings with union-find.

        Two memories are placed in the same cluster if their cosine similarity
        is >= threshold. Same union-find structure as _cluster_by_tags.
        """
        import numpy as np

        n = len(memories)
        if n == 0:
            return []

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normed = embeddings / np.maximum(norms, 1e-9)
        sims = normed @ normed.T  # (N, N) cosine similarity matrix

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

        groups: dict[int, list[dict]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(memories[i])

        return list(groups.values())

    def _has_temporal_spread(self, sorted_cluster: list[dict]) -> bool:
        """
        Check that a cluster spans multiple sessions (not all from one burst).

        Returns True if the oldest and newest memories are at least 1 hour apart
        and no more than MAX_AGE_SPAN_DAYS apart.
        """
        if len(sorted_cluster) < 2:
            return False

        try:
            oldest = datetime.fromisoformat(sorted_cluster[0]["created_at"])
            newest = datetime.fromisoformat(sorted_cluster[-1]["created_at"])
        except (KeyError, ValueError, TypeError):
            return False

        span = newest - oldest
        return timedelta(hours=1) <= span <= timedelta(days=self.MAX_AGE_SPAN_DAYS)


class ThreadNarrator:
    """
    Synthesizes memory clusters into narrative threads via LLM.

    Takes a cluster from ThreadDetector and produces a narrative arc —
    a story of how understanding evolved across sessions.
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def narrate_cluster(self, cluster: list[dict]) -> Optional[dict]:
        """
        Synthesize a memory cluster into a narrative thread.

        Args:
            cluster: Chronologically ordered list of memory dicts (with content).

        Returns:
            Dict with thread fields if synthesis succeeded:
            {
                "title": str,
                "summary": str,
                "narrative": str,
                "arc_type": str,
                "confidence": float,
                "member_ids": [str, ...],
            }
            None if the LLM determined these don't form a real arc.
        """
        if not cluster:
            return None

        # Format memories for the prompt
        mem_parts = []
        for i, mem in enumerate(cluster, 1):
            title = mem.get("title", "Untitled")
            created = mem.get("created_at", "unknown")
            content = mem.get("content", "")
            # Strip frontmatter from content for cleaner prompt
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
            # LLM doesn't think these form a real arc
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
            "member_ids": [m["id"] for m in cluster],
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


def build_threads(store: MemoryStore) -> list[dict]:
    """
    End-to-end thread building: detect clusters, narrate them, persist.

    Returns list of created thread dicts.
    """
    detector = ThreadDetector(store)
    narrator = ThreadNarrator(store)

    clusters = detector.detect_threads()
    created = []

    for cluster in clusters:
        result = narrator.narrate_cluster(cluster)
        if result is None:
            continue

        thread_id = store.create_thread(
            title=result["title"],
            summary=result["summary"],
            narrative=result["narrative"],
            member_ids=result["member_ids"],
        )

        result["id"] = thread_id
        created.append(result)

    return created
