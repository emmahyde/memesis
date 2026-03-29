"""
Three-tier retrieval engine with context matching and token budget.

Tier 1 — Instinctive: always injected, zero decision overhead.
Tier 2 — Crystallized: context-matched, token-budgeted.
Tier 3 — Active search: agent-initiated FTS with progressive disclosure.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .storage import MemoryStore

CONTEXT_WINDOW_CHARS = 200_000 * 4  # 200K tokens × 4 chars/token
THREAD_BUDGET_CHARS = 8_000
_THREAD_NARRATIVE_CAP = 1_000


class RetrievalEngine:
    """
    Three-tier retrieval engine for memory injection.

    Token budget is expressed as a fraction of the 200K-token context window
    (approximated at 4 chars per token).  Default 8% yields ~16 000 tokens.
    """

    def __init__(self, store: MemoryStore, token_budget_pct: float = 0.08):
        """
        Args:
            store: MemoryStore instance.
            token_budget_pct: Fraction of context window reserved for Tier-2
                crystallized memories.  Must be in (0, 1].
        """
        if not 0 < token_budget_pct <= 1:
            raise ValueError(
                f"token_budget_pct must be between 0 (exclusive) and 1 "
                f"(inclusive), got {token_budget_pct}"
            )
        self.store = store
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

        Logs every injected memory via store.record_injection().

        Args:
            session_id: Unique identifier for the current session.
            project_context: Optional project path for Tier-2 boosting.

        Returns:
            Formatted memory context block.  Empty string when no memories
            exist in either tier.
        """
        tier1 = self.get_instinctive_memories()
        tier2 = self.get_crystallized_for_context(
            project_context=project_context,
            token_limit=self.token_limit,
        )

        # Log injections for every memory surfaced, including project_context for D-08 tracking
        for memory in tier1 + tier2:
            self.store.record_injection(memory["id"], session_id, project_context=project_context)

        if not tier1 and not tier2:
            return ""

        sections = ["---MEMORY CONTEXT---", ""]

        # Tier 1 — Instinctive (behavioral guidelines)
        if tier1:
            sections.append("## Your Behavioral Guidelines (always active)")
            for memory in tier1:
                sections.append("")
                title = memory.get("title") or "Guideline"
                sections.append(f"### {title}")
                content = memory.get("content", "").strip()
                if content:
                    sections.append(content)

        # Tier 2 — Crystallized (context-relevant knowledge)
        if tier2:
            sections.append("")
            sections.append("## Context-Relevant Knowledge")
            for memory in tier2:
                sections.append("")
                title = memory.get("title") or "Memory"
                importance = memory.get("importance", 0.5)
                sections.append(f"### {title} (importance: {importance:.2f})")
                summary = memory.get("summary", "").strip()
                if summary:
                    sections.append(f"*{summary}*")
                content = memory.get("content", "").strip()
                if content:
                    sections.append(content)

        # Tier 2.5 — Narrative threads (episodic arcs for injected memories)
        thread_narratives = self._get_thread_narratives(tier2)
        if thread_narratives:
            sections.append("")
            sections.append("## Narrative Threads (how understanding evolved)")
            for thread in thread_narratives:
                sections.append("")
                title = thread.get("title", "Thread")
                sections.append(f"### {title}")
                narrative = thread.get("narrative", "").strip()
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

        Results include a ``summary`` field prominently alongside full content.
        Does NOT log injections — active search results are surfaced on demand,
        not pre-injected.

        Args:
            query: FTS5 query string.
            session_id: Current session identifier (reserved for future logging).
            limit: Maximum number of results.

        Returns:
            List of memory dicts ordered by FTS relevance, each with:
            ``id``, ``title``, ``summary``, ``content``, ``importance``,
            ``stage``, ``tags``, ``rank``.
        """
        results = self.store.search_fts(query, limit=limit)

        disclosed = []
        for memory in results:
            disclosed.append({
                "id": memory["id"],
                "title": memory.get("title"),
                "summary": memory.get("summary"),  # prominently available
                "content": memory.get("content", ""),
                "importance": memory.get("importance", 0.5),
                "stage": memory.get("stage"),
                "tags": memory.get("tags", []),
                "rank": memory.get("rank"),
                "project_context": memory.get("project_context"),
            })

        return disclosed

    def _get_thread_narratives(self, tier2_memories: list[dict]) -> list[dict]:
        """
        Find narrative threads whose members appear in tier2_memories.
        Uses batch query. Applies per-narrative cap and total budget (shortest-first).
        Updates last_surfaced_at lazily.
        """
        if not tier2_memories:
            return []

        memory_ids = [m["id"] for m in tier2_memories]
        candidates = self.store.get_threads_for_memories_batch(memory_ids)

        # Per-narrative cap: truncate at sentence boundary
        for t in candidates:
            narrative = t.get("narrative") or ""
            if len(narrative) > _THREAD_NARRATIVE_CAP:
                truncated = narrative[:_THREAD_NARRATIVE_CAP]
                last_period = truncated.rfind(".")
                if last_period > _THREAD_NARRATIVE_CAP // 2:
                    truncated = truncated[:last_period + 1]
                t["narrative"] = truncated

        # Greedy budget: shortest first maximises arc count
        candidates_sorted = sorted(candidates, key=lambda t: len(t.get("narrative") or ""))
        budget_remaining = THREAD_BUDGET_CHARS
        selected = []
        for thread in candidates_sorted:
            cost = len(thread.get("narrative") or "")
            if cost <= budget_remaining:
                selected.append(thread)
                budget_remaining -= cost

        # Lazy update: record surfacing timestamp
        if selected:
            now = datetime.now(timezone.utc).isoformat()
            self.store.update_threads_last_surfaced([t["id"] for t in selected], now)

        return selected

    def get_instinctive_memories(self) -> list[dict]:
        """
        Return all instinctive memories with their file content loaded.

        Tier 1 — no filtering, no budget limits.  Every instinctive memory is
        always returned.
        """
        records = self.store.list_by_stage("instinctive")
        result = []
        for record in records:
            full = self.store.get(record["id"])
            result.append(full)
        return result

    def get_crystallized_for_context(
        self,
        project_context: str = None,
        token_limit: int = None,
    ) -> list[dict]:
        """
        Return token-budgeted crystallized memories, optionally boosted by
        project context.

        Sort order:
            1. Project-matching memories (when project_context provided) first.
            2. importance DESC.
            3. last_used_at DESC (None sorts last).

        Args:
            project_context: If provided, memories whose ``project_context``
                matches are placed before non-matching ones.
            token_limit: Character budget (chars ≈ tokens × 4).  Defaults to
                ``self.token_limit``.

        Returns:
            List of memory dicts (with content) that fit within the budget.
        """
        if token_limit is None:
            token_limit = self.token_limit

        records = self.store.list_by_stage("crystallized")

        # Three-pass stable sort: last_used_at DESC, then importance DESC,
        # then project-match first.
        # 1. Sort by last_used_at DESC (stable)
        # 2. Then by importance DESC (stable)
        # 3. Then project-match first (stable)
        records_sorted = sorted(
            records,
            key=lambda m: m.get("last_used_at") or "",
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: m.get("importance") or 0.0,
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: (
                0
                if (
                    project_context is not None
                    and m.get("project_context") == project_context
                )
                else 1
            ),
        )

        # Apply token budget
        budget_remaining = token_limit
        selected = []

        for record in records_sorted:
            full = self.store.get(record["id"])
            content = full.get("content", "") or ""
            cost = len(content)

            if cost <= budget_remaining:
                selected.append(full)
                budget_remaining -= cost
            # If a single memory exceeds the remaining budget we skip it and
            # keep trying — a smaller memory later in the list may still fit.
            # (This is a best-effort packing approach, not strict first-fit.)

        return selected
