"""
Three-tier retrieval engine with context matching and token budget.

Tier 1 — Instinctive: always injected, zero decision overhead.
Tier 2 — Crystallized: context-matched, token-budgeted.
Tier 3 — Active search: agent-initiated FTS with progressive disclosure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .database import get_base_dir
from .models import Memory, NarrativeThread, RetrievalLog, ThreadMember, db

if TYPE_CHECKING:
    from .vec import VecStore

CONTEXT_WINDOW_CHARS = 200_000 * 4  # 200K tokens x 4 chars/token
THREAD_BUDGET_CHARS = 8_000
_THREAD_NARRATIVE_CAP = 1_000


def _record_injection(memory_id: str, session_id: str, project_context: str = None) -> None:
    """Record that a memory was injected into a session."""
    now = datetime.now().isoformat()
    Memory.update(
        last_injected_at=now,
        injection_count=Memory.injection_count + 1,
    ).where(Memory.id == memory_id).execute()

    RetrievalLog.create(
        timestamp=now,
        session_id=session_id,
        memory_id=memory_id,
        retrieval_type='injected',
        project_context=project_context,
    )


class RetrievalEngine:
    """
    Three-tier retrieval engine for memory injection.

    Token budget is expressed as a fraction of the 200K-token context window
    (approximated at 4 chars per token).  Default 8% yields ~16 000 tokens.
    """

    def __init__(self, token_budget_pct: float = 0.08):
        """
        Args:
            token_budget_pct: Fraction of context window reserved for Tier-2
                crystallized memories.  Must be in (0, 1].
        """
        if not 0 < token_budget_pct <= 1:
            raise ValueError(
                f"token_budget_pct must be between 0 (exclusive) and 1 "
                f"(inclusive), got {token_budget_pct}"
            )
        self.token_budget_pct = token_budget_pct
        # token_limit is in *characters* (chars/4 is the token estimate)
        self.token_limit = int(token_budget_pct * 200_000) * 4  # chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject_for_session(
        self,
        session_id: str,
        project_context: str = None,
    ) -> str:
        """
        Build the full memory context string for injection into a session.
        """
        tier1 = self.get_instinctive_memories()
        tier2 = self.get_crystallized_for_context(
            project_context=project_context,
            token_limit=self.token_limit,
        )

        # Log injections for every memory surfaced
        for memory in tier1 + tier2:
            _record_injection(memory.id, session_id, project_context=project_context)

        if not tier1 and not tier2:
            return ""

        sections = ["---MEMORY CONTEXT---", ""]

        # Tier 1 — Instinctive (behavioral guidelines)
        if tier1:
            sections.append("## Your Behavioral Guidelines (always active)")
            for memory in tier1:
                sections.append("")
                title = memory.title or "Guideline"
                sections.append(f"### {title}")
                content = (memory.content or "").strip()
                if content:
                    sections.append(content)

        # Tier 2 — Crystallized (context-relevant knowledge)
        if tier2:
            sections.append("")
            sections.append("## Context-Relevant Knowledge")
            for memory in tier2:
                sections.append("")
                title = memory.title or "Memory"
                importance = memory.importance or 0.5
                sections.append(f"### {title} (importance: {importance:.2f})")
                summary = (memory.summary or "").strip()
                if summary:
                    sections.append(f"*{summary}*")
                content = (memory.content or "").strip()
                if content:
                    sections.append(content)

        # Tier 2.5 — Narrative threads (episodic arcs for injected memories)
        thread_narratives = self._get_thread_narratives(tier2)
        if thread_narratives:
            sections.append("")
            sections.append("## Narrative Threads (how understanding evolved)")
            for thread in thread_narratives:
                sections.append("")
                title = thread.title or "Thread"
                sections.append(f"### {title}")
                narrative = (thread.narrative or "").strip()
                if narrative:
                    sections.append(narrative)

        sections.append("")
        sections.append("---END MEMORY CONTEXT---")

        return "\n".join(sections)

    def active_search(
        self,
        query: str,
        session_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """
        Agent-initiated FTS search (Tier 3) with progressive disclosure.
        """
        results = Memory.search_fts(query, limit=limit)

        disclosed = []
        for memory in results:
            disclosed.append({
                "id": memory.id,
                "title": memory.title,
                "summary": memory.summary,
                "content": memory.content or "",
                "importance": memory.importance or 0.5,
                "stage": memory.stage,
                "tags": memory.tag_list,
                "rank": getattr(memory, '_rank', None),
                "project_context": memory.project_context,
            })

        return disclosed

    def hybrid_search(
        self,
        query: str,
        query_embedding: bytes | None = None,
        k: int = 20,
        rrf_k: int = 60,
        vec_store: "VecStore | None" = None,
    ) -> list[tuple[str, float]]:
        """
        Reciprocal Rank Fusion over FTS and vector search legs.

        Combines BM25 full-text search with KNN vector search into a single
        ranked list.  Each leg contributes RRF terms: 1 / (rrf_k + rank).
        Memories absent from a leg are not penalised — they simply receive
        fewer RRF terms.

        Args:
            query: Text query sent to the FTS leg.
            query_embedding: Serialised embedding bytes for the vector leg.
                If None, the vector leg is skipped.
            k: Maximum number of results to return; also the per-leg candidate
                limit fed to FTS / vector search.
            rrf_k: RRF smoothing constant (default 60 per research literature).
            vec_store: Optional VecStore instance.  If None or not available,
                the method falls back to FTS-only ranking.

        Returns:
            List of (memory_id, rrf_score) tuples, sorted by score descending,
            limited to at most ``k`` entries.
        """
        # --- FTS leg -------------------------------------------------------
        fts_results = Memory.search_fts(query, limit=k)
        # Build {memory_id: 1-based rank} from FTS order
        fts_ranks: dict[str, int] = {
            mem.id: rank for rank, mem in enumerate(fts_results, start=1)
        }

        # --- Vector leg (conditional) ---------------------------------------
        vec_ranks: dict[str, int] = {}
        use_vec = (
            vec_store is not None
            and vec_store.available
            and query_embedding is not None
        )
        if use_vec:
            vec_results = vec_store.search_vector(query_embedding, k=k)
            vec_ranks = {
                memory_id: rank
                for rank, (memory_id, _distance) in enumerate(vec_results, start=1)
            }

        # --- RRF fusion ----------------------------------------------------
        all_ids = set(fts_ranks) | set(vec_ranks)
        if not all_ids:
            return []

        scores: dict[str, float] = {}
        for memory_id in all_ids:
            score = 0.0
            if memory_id in fts_ranks:
                score += 1.0 / (rrf_k + fts_ranks[memory_id])
            if memory_id in vec_ranks:
                score += 1.0 / (rrf_k + vec_ranks[memory_id])
            scores[memory_id] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:k]

    def _get_thread_narratives(self, tier2_memories: list) -> list:
        """
        Find narrative threads whose members appear in tier2_memories.
        """
        if not tier2_memories:
            return []

        memory_ids = [m.id for m in tier2_memories]
        if not memory_ids:
            return []

        # Batch query for threads containing any of the tier2 memory IDs
        candidates = list(
            NarrativeThread.select()
            .join(ThreadMember, on=(NarrativeThread.id == ThreadMember.thread_id))
            .where(ThreadMember.memory_id.in_(memory_ids))
            .distinct()
            .order_by(NarrativeThread.updated_at.desc())
        )

        # Per-narrative cap: truncate at sentence boundary
        for t in candidates:
            narrative = t.narrative or ""
            if len(narrative) > _THREAD_NARRATIVE_CAP:
                truncated = narrative[:_THREAD_NARRATIVE_CAP]
                last_period = truncated.rfind(".")
                if last_period > _THREAD_NARRATIVE_CAP // 2:
                    truncated = truncated[:last_period + 1]
                t.narrative = truncated

        # Greedy budget: shortest first maximises arc count
        candidates_sorted = sorted(candidates, key=lambda t: len(t.narrative or ""))
        budget_remaining = THREAD_BUDGET_CHARS
        selected = []
        for thread in candidates_sorted:
            cost = len(thread.narrative or "")
            if cost <= budget_remaining:
                selected.append(thread)
                budget_remaining -= cost

        # Lazy update: record surfacing timestamp
        if selected:
            now = datetime.now(timezone.utc).isoformat()
            thread_ids = [t.id for t in selected]
            NarrativeThread.update(last_surfaced_at=now).where(
                NarrativeThread.id.in_(thread_ids)
            ).execute()

        return selected

    def get_instinctive_memories(self) -> list:
        """
        Return all instinctive memories with their content loaded.
        Tier 1 — no filtering, no budget limits.
        """
        return list(Memory.by_stage("instinctive"))

    def get_crystallized_for_context(
        self,
        project_context: str = None,
        token_limit: int = None,
    ) -> list:
        """
        Return token-budgeted crystallized memories, optionally boosted by
        project context.
        """
        if token_limit is None:
            token_limit = self.token_limit

        records = list(Memory.by_stage("crystallized"))

        # Three-pass stable sort
        records_sorted = sorted(
            records,
            key=lambda m: m.last_used_at or "",
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: m.importance or 0.0,
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: (
                0
                if (
                    project_context is not None
                    and m.project_context == project_context
                )
                else 1
            ),
        )

        # Apply token budget
        budget_remaining = token_limit
        selected = []

        for record in records_sorted:
            content = record.content or ""
            cost = len(content)

            if cost <= budget_remaining:
                selected.append(record)
                budget_remaining -= cost

        return selected
