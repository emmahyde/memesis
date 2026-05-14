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

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Optional

from .codebook import encode_field_value, is_codebook_enabled
from .database import get_base_dir, get_vec_store
from .flags import get_flag
from .lifecycle import LifecycleManager
from .llm import call_llm
from .codebook import encode_field_value, is_codebook_enabled
from .models import ConsolidationLog, Memory, MemoryEdge
from .trace import get_active_writer

logger = logging.getLogger(__name__)

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



def _get_embeddings(candidates: list):
    """
    Retrieve stored embeddings for candidates from vec_memories.

    Returns numpy array of shape (N, 512), or None if embeddings
    are not available for all candidates.
    """
    try:
        import struct
        import numpy as np
    except ImportError:
        return None

    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        return None

    embeddings = []
    for c in candidates:
        cid = c.id if hasattr(c, 'id') and not isinstance(c, dict) else c["id"]
        raw = vec_store.get_embedding(cid)
        if raw is None:
            return None  # Missing embedding — fall back to tag-overlap
        vec = struct.unpack(f"{len(raw)//4}f", raw)
        embeddings.append(vec)

    return np.array(embeddings, dtype=np.float32)


class Crystallizer:
    """
    Transforms consolidated memories into crystallized insights.

    When memories earn promotion (reinforcement_count >= 3), this engine
    synthesizes them — grouping related observations and distilling them
    into denser, pattern-level knowledge.
    """

    def __init__(self, lifecycle: LifecycleManager):
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
                mem = Memory.get_by_id(c["id"])
                full_candidates.append(mem)
            except (KeyError, Memory.DoesNotExist):
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

    def _group_candidates(self, candidates: list) -> list[list]:
        """
        Group related candidates by theme.

        Phase 1: tries embedding cosine similarity (stored vector embeddings).
        Phase 2: falls back to tag-overlap if embeddings are unavailable.
        Ungrouped candidates form singleton groups (still get synthesized).
        """
        if len(candidates) <= 2:
            # Not enough to meaningfully cluster — each is its own group
            return [[c] for c in candidates]

        # Phase 1: Try embedding-based clustering
        # Skip if texts are too short to produce meaningful embeddings
        texts = [f"{c.title or ''} {(c.content or '')[:200]}" for c in candidates]
        min_text_len = min(len(t.strip()) for t in texts)
        embeddings = _get_embeddings(candidates) if min_text_len >= 10 else None

        if embeddings is not None:
            # Static 0.75 acts as the hard floor; _group_by_embeddings raises
            # the effective threshold via P75 of the batch's pairwise sims if
            # the active embedding model produces a tighter distribution.
            return self._group_by_embeddings(candidates, embeddings, threshold=0.75)

        # Phase 2: Fall back to tag-overlap (original logic)
        return self._group_by_tags(candidates)

    def _group_by_tags(self, candidates: list) -> list[list]:
        """
        Group related candidates by observation type and tag overlap.

        Simple heuristic: same observation_type AND at least one shared tag.
        Ungrouped candidates form singleton groups (still get synthesized).
        """
        # Extract tags from metadata
        def get_tags(mem):
            tag_list = mem.tag_list if hasattr(mem, 'tag_list') else []
            return set(tag_list)

        def get_obs_type(mem):
            tags = get_tags(mem)
            for t in tags:
                if t.startswith("type:"):
                    return t[5:]
            return None

        # Group by observation type first
        by_type: dict[str, list] = {}
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
        candidates: list,
        embeddings,  # numpy array (N, dim)
        threshold: float,
    ) -> list[list]:
        """
        Group candidates by embedding cosine similarity using union-find.

        Effective threshold is `max(static_threshold, P75_off_diagonal_sims)`,
        capped at 0.85. The percentile floor adapts to the embedding model's
        similarity scale: if a model produces a tight distribution where most
        pairs sit above the static cutoff, the floor lifts the bar so we don't
        merge everything. Static threshold remains a hard minimum so we never
        over-cluster on a sparse batch.
        """
        import numpy as np
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normed = embeddings / np.maximum(norms, 1e-9)
        sims = normed @ normed.T

        n = len(candidates)
        if n >= 2:
            iu = np.triu_indices(n, k=1)
            off_diag = sims[iu]
            adaptive = float(np.percentile(off_diag, 75)) if off_diag.size else threshold
            effective = min(0.85, max(threshold, adaptive))
        else:
            effective = threshold

        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                if sims[i, j] >= effective:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

        groups: dict[int, list] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(candidates[i])
        return list(groups.values())

    def _crystallize_group(self, group: list) -> Optional[dict]:
        """
        Synthesize a group of related memories into one crystallized insight.

        For singletons, still transforms the content (strips episodic details,
        generalizes the pattern).
        """
        _writer = get_active_writer()
        if _writer is not None:
            _writer.emit(
                stage="crystallize",
                event="crystallize_group_start",
                payload={"group_size": len(group), "memory_ids": [str(m.id) for m in group]},
            )

        # Format observations for the prompt
        obs_parts = []
        for i, mem in enumerate(group, 1):
            title = mem.title or "Untitled"
            content = mem.content or ""
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
        source_ids = [m.id for m in group]
        source_titles = [m.title or "?" for m in group]

        # NOTE: Disambiguation cost research gap (linguistic-compression).
        # Crystallized memories strip episodic details to achieve density.
        # This is analogous to Toki Pona's polysemy — compression shifts cost
        # to the decoder (the LLM must infer from context).  The source_pattern
        # field is our disambiguation mechanism, but we have no empirical
        # measurement of whether stripped context hurts task performance.
        #
        # To measure this: A/B test sessions where crystallized memories are
        # injected with vs without source_pattern provenance.  Compare task
        # completion rates, clarification requests, and user corrections.
        # This is deferred until we have automated eval harness support for
        # session-level outcome metrics.
        #
        # See: outputs/linguistic-compression-draft.md §7 (Ithkuil) and
        # §8 (ambiguity-verbosity trade-off) for theoretical framing.

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

        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', result["title"].lower())[:60]

        base_dir = get_base_dir()
        file_path = base_dir / "crystallized" / f"{safe_name}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Build full content with frontmatter
        frontmatter_lines = [
            '---',
            f'name: {result["title"]}',
            f'description: {result["insight"][:150]}',
            'type: memory',
            '---',
            '',
            content,
        ]
        full_content = '\n'.join(frontmatter_lines)

        if is_codebook_enabled():
            full_content = encode_field_value(full_content)

        # Hash from body only — must match what Memory.save() will compute
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

        # Dedup check
        if Memory.select().where(Memory.content_hash == content_hash).exists():
            return self._fallback_promote(group)

        now = datetime.now().isoformat()
        crystal_mem = Memory.create(
            stage="crystallized",
            title=result["title"],
            summary=result["insight"][:150],
            content=content,   # body only — better FTS, consistent hash
            tags=json.dumps(tags),
            importance=0.75,
            reinforcement_count=0,
            created_at=now,
            updated_at=now,
            content_hash=content_hash,
            # Defensive nulls — crystallizer is a non-card write path (D3)
            temporal_scope=None,
            confidence=None,
            affect_valence=None,
            actor=None,
            criterion_weights=None,
            rejected_options=None,
        )
        crystallized_id = crystal_mem.id

        # Write file
        file_path.write_text(full_content, encoding="utf-8")

        # Archive source memories — they've been subsumed.
        for mem in group:
            try:
                mem.reinforcement_count = 0
                mem.subsumed_by = crystallized_id
                mem.save()
                Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == mem.id).execute()
                ConsolidationLog.create(
                    timestamp=datetime.now().isoformat(),
                    action="subsumed",
                    memory_id=mem.id,
                    from_stage="consolidated",
                    to_stage="archived",
                    rationale=f"Subsumed into crystallized memory: {result['title']}",
                )
            except Exception:
                pass

        if get_flag("causal_edges"):
            self._create_subsumption_edges(group, crystallized_id, result["title"])

        if _writer is not None:
            _writer.emit(
                stage="crystallize",
                event="crystallize_group_end",
                payload={"crystallized_memory_id": str(crystallized_id), "sources_archived": len(group)},
            )

        return {
            "crystallized_id": crystallized_id,
            "source_ids": source_ids,
            "title": result["title"],
            "insight": result["insight"],
            "group_size": len(group),
        }

    @staticmethod
    def _create_subsumption_edges(
        group: list, crystallized_id: str, crystal_title: str
    ) -> None:
        """Create subsumed_into edges from source memories to the crystal."""
        now = datetime.now().isoformat()
        for mem in group:
            try:
                MemoryEdge.create(
                    source_id=mem.id,
                    target_id=crystallized_id,
                    edge_type="subsumed_into",
                    weight=1.0,
                    metadata=json.dumps({
                        "source_title": mem.title or "",
                        "crystal_title": crystal_title,
                        "created_at": now,
                    }),
                )
            except Exception as e:
                logger.warning("Failed to create subsumption edge: %s", e)

    def _fallback_promote(self, group: list) -> Optional[dict]:
        """Simple promotion without synthesis — used when LLM call fails."""
        promoted = []
        for mem in group:
            try:
                self.lifecycle.promote(
                    mem.id, "Auto-promoted: meets reinforcement threshold"
                )
                promoted.append(mem.id)
            except ValueError:
                pass
        if promoted:
            return {
                "crystallized_id": promoted[0],
                "source_ids": promoted,
                "title": group[0].title or "?",
                "insight": "(fallback — no synthesis)",
                "group_size": len(group),
            }
        return None
