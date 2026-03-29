"""
Crystallization engine — transforms consolidated memories into higher-level insights.

Human memory doesn't just promote — it transforms. Episodic memories ("I burned
my hand on the stove Tuesday") become semantic memories ("stoves are hot"). The
specific details fall away; the pattern crystallizes.

This module implements that transformation. When consolidated memories accumulate
enough reinforcement to earn promotion, the crystallizer:

1. Groups related candidates by theme
2. Synthesizes each group into a single, denser insight via LLM
3. Creates the crystallized memory with transformed content
4. Archives the source memories (subsumed, not deleted)

A single promotion candidate still gets transformed — stripped of episodic
detail, generalized into a reusable pattern.
"""

import json
from typing import Optional

from .lifecycle import LifecycleManager
from .llm import call_llm
from .storage import MemoryStore

# ---------------------------------------------------------------------------
# Crystallization prompt
# ---------------------------------------------------------------------------

CRYSTALLIZATION_PROMPT = """You are transforming episodic observations into semantic knowledge — the way human memory consolidates specific experiences into general understanding.

SOURCE OBSERVATIONS (these have proven valuable across multiple sessions):
{observations}

YOUR TASK: Synthesize these into ONE crystallized insight.

THE TRANSFORMATION:
- Strip away session-specific details (dates, file paths, one-time contexts)
- Extract the PATTERN — the general principle these observations share
- Preserve the behavioral teeth — what would I do differently because of this?
- Be denser than the sources — a crystallized memory should pack more signal per word

EPISODIC → SEMANTIC EXAMPLES:
- "Bedrock requires AnthropicBedrock()" + "Bedrock model IDs use us.anthropic prefix" + "Bedrock doesn't support all API features"
  → "AWS Bedrock wraps the Anthropic API but diverges at every interface point: client class, model ID format, and feature availability. Treat each surface as potentially different."

- "Emma prefers single PRs for refactors" + "Emma slices large PRs by abstraction layer"
  → "PR sizing follows a principle: one PR per coherent change, where 'coherent' means a single abstraction layer or a complete refactor. Splitting within a layer adds review overhead without reducing risk."

- "I defaulted to PostgreSQL when SQLite was fine" + "I suggested threads when asyncio was right"
  → "Pattern: I reach for heavyweight/familiar tools before checking if the problem's actual constraints allow something simpler. The correction isn't 'always use the simple thing' — it's 'check the constraints first.'"

RULES:
- Title should be a general principle, not a specific fact
- The insight should be useful even if you forget the source observations
- If observations don't share a real pattern, don't force synthesis — keep the strongest one and generalize it
- Maximum 3 sentences for the insight body. Density over length.

Respond ONLY with valid JSON:
{{
  "title": "General principle (not a specific fact)",
  "insight": "The crystallized understanding — dense, behavioral, pattern-level",
  "observation_type": "correction|preference_signal|workflow_pattern|self_observation|domain_knowledge|shared_insight|decision_context",
  "tags": ["tag1", "tag2"],
  "source_pattern": "One sentence: what these observations have in common"
}}"""



def _get_embeddings(texts: list[str]):
    """
    Encode texts with sentence-transformers (all-MiniLM-L6-v2).

    Returns numpy array of shape (len(texts), 384), or None if
    sentence-transformers is unavailable. Caller falls back to tag-overlap.
    """
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(texts)
    except Exception:
        import sys
        print(
            "[crystallizer] sentence-transformers unavailable, falling back to tag-overlap",
            file=sys.stderr,
        )
        return None


class Crystallizer:
    """
    Transforms consolidated memories into crystallized insights.

    When memories earn promotion (reinforcement_count >= 3), this engine
    synthesizes them — grouping related observations and distilling them
    into denser, pattern-level knowledge.
    """

    def __init__(self, store: MemoryStore, lifecycle: LifecycleManager):
        self.store = store
        self.lifecycle = lifecycle

    def crystallize_candidates(self) -> list[dict]:
        """
        Find promotion candidates, group by theme, synthesize, and promote.

        Returns list of crystallization results:
        [{"crystallized_id": ..., "source_ids": [...], "title": ...}, ...]
        """
        candidates = self.lifecycle.get_promotion_candidates()
        if not candidates:
            return []

        # Load full memory content for each candidate
        full_candidates = []
        for c in candidates:
            try:
                mem = self.store.get(c["id"])
                full_candidates.append(mem)
            except (KeyError, ValueError):
                continue

        if not full_candidates:
            return []

        # Group related candidates
        groups = self._group_candidates(full_candidates)

        results = []
        for group in groups:
            result = self._crystallize_group(group)
            if result:
                results.append(result)

        return results

    def _group_candidates(self, candidates: list[dict]) -> list[list[dict]]:
        """
        Group related candidates by theme.

        Phase 1: tries embedding cosine similarity (sentence-transformers).
        Phase 2: falls back to tag-overlap if embeddings are unavailable.
        Ungrouped candidates form singleton groups (still get synthesized).
        """
        if len(candidates) <= 2:
            # Not enough to meaningfully cluster — each is its own group
            return [[c] for c in candidates]

        # Phase 1: Try embedding-based clustering
        # Skip if texts are too short to produce meaningful embeddings
        # (degenerate inputs like single-character titles would cluster incorrectly)
        texts = [f"{c.get('title', '')} {c.get('content', '')[:200]}" for c in candidates]
        min_text_len = min(len(t.strip()) for t in texts)
        embeddings = _get_embeddings(texts) if min_text_len >= 10 else None

        if embeddings is not None:
            return self._group_by_embeddings(candidates, embeddings, threshold=0.75)

        # Phase 2: Fall back to tag-overlap (original logic)
        return self._group_by_tags(candidates)

    def _group_by_tags(self, candidates: list[dict]) -> list[list[dict]]:
        """
        Group related candidates by observation type and tag overlap.

        Simple heuristic: same observation_type AND at least one shared tag.
        Ungrouped candidates form singleton groups (still get synthesized).
        """
        # Extract tags from metadata
        def get_tags(mem):
            tags = mem.get("tags", "")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
            return set(tags) if isinstance(tags, list) else set()

        def get_obs_type(mem):
            tags = get_tags(mem)
            for t in tags:
                if t.startswith("type:"):
                    return t[5:]
            return None

        # Group by observation type first
        by_type: dict[str, list[dict]] = {}
        ungrouped = []
        for mem in candidates:
            obs_type = get_obs_type(mem)
            if obs_type:
                by_type.setdefault(obs_type, []).append(mem)
            else:
                ungrouped.append(mem)

        groups = []

        # Within each type, check for tag overlap to form sub-groups
        for obs_type, mems in by_type.items():
            if len(mems) == 1:
                groups.append(mems)
                continue

            # Simple greedy clustering: if two memories share a non-type tag, group them
            used = set()
            for i, m1 in enumerate(mems):
                if i in used:
                    continue
                group = [m1]
                used.add(i)
                tags1 = get_tags(m1) - {f"type:{obs_type}", "source:backfill"}
                for j, m2 in enumerate(mems):
                    if j in used:
                        continue
                    tags2 = get_tags(m2) - {f"type:{obs_type}", "source:backfill"}
                    if tags1 & tags2:  # Any shared tag
                        group.append(m2)
                        used.add(j)
                        tags1 |= tags2  # Expand for transitive grouping
                groups.append(group)

            # Any remaining ungrouped within this type
            for i, m in enumerate(mems):
                if i not in used:
                    groups.append([m])

        # Add completely ungrouped
        for m in ungrouped:
            groups.append([m])

        return groups

    def _group_by_embeddings(
        self,
        candidates: list[dict],
        embeddings,  # numpy array (N, 384)
        threshold: float,
    ) -> list[list[dict]]:
        """
        Group candidates by embedding cosine similarity using union-find.

        Pairs with cosine similarity >= threshold are merged into the same group.
        """
        import numpy as np
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normed = embeddings / np.maximum(norms, 1e-9)
        sims = normed @ normed.T  # (N, N) cosine similarity matrix

        n = len(candidates)
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
            groups.setdefault(find(i), []).append(candidates[i])
        return list(groups.values())

    def _crystallize_group(self, group: list[dict]) -> Optional[dict]:
        """
        Synthesize a group of related memories into one crystallized insight.

        For singletons, still transforms the content (strips episodic details,
        generalizes the pattern).
        """
        # Format observations for the prompt
        obs_parts = []
        for i, mem in enumerate(group, 1):
            title = mem.get("title", "Untitled")
            content = mem.get("content", "")
            obs_parts.append(f"[{i}] **{title}**\n{content}")

        observations_text = "\n\n".join(obs_parts)
        prompt = CRYSTALLIZATION_PROMPT.format(observations=observations_text)

        try:
            raw = call_llm(prompt, max_tokens=1024, temperature=0)
            result = json.loads(raw)
        except Exception:
            # If synthesis fails, fall back to simple promotion
            return self._fallback_promote(group)

        # Create the crystallized memory
        source_ids = [m["id"] for m in group]
        source_titles = [m.get("title", "?") for m in group]

        tags = list(result.get("tags", []))
        obs_type = result.get("observation_type", "")
        if obs_type and f"type:{obs_type}" not in tags:
            tags.append(f"type:{obs_type}")
        tags.append("source:crystallization")

        content = result["insight"]
        source_pattern = result.get("source_pattern", "")
        if source_pattern:
            content += f"\n\n**Source pattern:** {source_pattern}"
        content += f"\n\n**Synthesized from:** {', '.join(source_titles)}"

        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', result["title"].lower())[:60]

        crystallized_id = self.store.create(
            path=f"crystallized/{safe_name}.md",
            content=content,
            metadata={
                "stage": "crystallized",
                "title": result["title"],
                "summary": result["insight"][:150],
                "tags": tags,
                "importance": 0.75,  # Crystallized memories start higher
            },
        )

        # Archive source memories — they've been subsumed.
        # Mark with subsumed_by so the relevance engine inhibits them
        # during rehydration (retrieval-induced forgetting: strengthening
        # the crystallized insight suppresses the source episodes).
        for mem in group:
            try:
                self.store.update(mem["id"], metadata={
                    "reinforcement_count": 0,
                    "subsumed_by": crystallized_id,
                })
                self.store.archive(mem["id"])
                self.store.log_consolidation(
                    action="subsumed",
                    memory_id=mem["id"],
                    from_stage="consolidated",
                    to_stage="archived",
                    rationale=f"Subsumed into crystallized memory: {result['title']}",
                )
            except (ValueError, KeyError):
                pass

        return {
            "crystallized_id": crystallized_id,
            "source_ids": source_ids,
            "title": result["title"],
            "insight": result["insight"],
            "group_size": len(group),
        }

    def _fallback_promote(self, group: list[dict]) -> Optional[dict]:
        """Simple promotion without synthesis — used when LLM call fails."""
        promoted = []
        for mem in group:
            try:
                self.lifecycle.promote(
                    mem["id"], "Auto-promoted: meets reinforcement threshold"
                )
                promoted.append(mem["id"])
            except ValueError:
                pass
        if promoted:
            return {
                "crystallized_id": promoted[0],
                "source_ids": promoted,
                "title": group[0].get("title", "?"),
                "insight": "(fallback — no synthesis)",
                "group_size": len(group),
            }
        return None
