"""
Relevance scoring, archival, and rehydration engine.

Computes a continuous relevance score for each memory based on importance,
recency, usage patterns, and context match.  Memories that fade below the
archival threshold are excluded from injection but remain searchable.
When new observations or context changes make archived memories relevant
again, they are rehydrated — returned to the active pool automatically.

The relevance score uses an exponential decay model:

    relevance = importance^0.4 * recency^0.3 * usage_signal^0.2 * context_boost^0.1

Where:
    recency      = 0.5 ^ (days_since_last_activity / half_life)
    usage_signal = clamp(0.3 + 0.7 * (usage_count / max(injection_count, 1)), 0.3, 1.0)
    context_boost = 1.5 when project matches, 1.0 otherwise

This produces a smooth decay curve — memories fade gradually, never cliff-edge.
"""

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from .storage import MemoryStore

logger = logging.getLogger(__name__)

# Archival: memories below this relevance are candidates for archival.
ARCHIVE_THRESHOLD = 0.15

# Rehydration: archived memories above this relevance (in context) are
# candidates for rehydration.
REHYDRATE_THRESHOLD = 0.30

# Half-life in days: after this many days without activity, recency drops to 0.5.
RECENCY_HALF_LIFE = 60

# How many FTS results to check for rehydration matches.
REHYDRATION_FTS_LIMIT = 20


class RelevanceEngine:
    """
    Computes relevance scores and manages archival/rehydration lifecycle.

    The engine does not own persistence — it reads from and writes to
    MemoryStore, making archival decisions based on computed scores.
    """

    def __init__(
        self,
        store: MemoryStore,
        archive_threshold: float = ARCHIVE_THRESHOLD,
        rehydrate_threshold: float = REHYDRATE_THRESHOLD,
        half_life_days: float = RECENCY_HALF_LIFE,
    ):
        self.store = store
        self.archive_threshold = archive_threshold
        self.rehydrate_threshold = rehydrate_threshold
        self.half_life_days = half_life_days

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def compute_relevance(
        self,
        memory: dict,
        project_context: str = None,
        now: datetime = None,
    ) -> float:
        """
        Compute a relevance score in [0, 1] for a memory.

        Args:
            memory: Memory dict from MemoryStore (must include metadata fields).
            project_context: Current project path for context matching.
            now: Override current time (for testing).

        Returns:
            Relevance score between 0.0 and 1.0.
        """
        if now is None:
            now = datetime.now()

        importance = memory.get("importance", 0.5)

        # Recency: exponential decay from last activity
        days_since = self._days_since_last_activity(memory, now)
        recency = 0.5 ** (days_since / self.half_life_days) if self.half_life_days > 0 else 1.0

        # Usage signal: memories that are actually used (not just injected) get boosted
        usage_count = memory.get("usage_count", 0)
        injection_count = max(memory.get("injection_count", 0), 1)
        usage_ratio = usage_count / injection_count
        usage_signal = min(1.0, 0.3 + 0.7 * usage_ratio)

        # Context boost: memories from the same project are more relevant
        context_boost = 1.0
        if project_context and memory.get("project_context") == project_context:
            context_boost = 1.5

        # Weighted geometric mean
        relevance = (
            (importance ** 0.4)
            * (recency ** 0.3)
            * (usage_signal ** 0.2)
            * (context_boost ** 0.1)
        )

        return min(1.0, max(0.0, relevance))

    # ------------------------------------------------------------------
    # Archival
    # ------------------------------------------------------------------

    def get_archival_candidates(self, project_context: str = None) -> list[dict]:
        """
        Find active memories whose relevance has decayed below the archive threshold.

        Returns memories sorted by relevance ascending (least relevant first),
        each annotated with a 'relevance' key.

        Args:
            project_context: Current project path for context scoring.

        Returns:
            List of memory dicts with 'relevance' key added.
        """
        candidates = []

        for stage in ("consolidated", "crystallized"):
            memories = self.store.list_by_stage(stage, include_archived=False)
            for memory in memories:
                relevance = self.compute_relevance(memory, project_context)
                if relevance < self.archive_threshold:
                    memory["relevance"] = relevance
                    candidates.append(memory)

        candidates.sort(key=lambda m: m["relevance"])
        return candidates

    def archive_stale(self, project_context: str = None) -> list[dict]:
        """
        Archive memories that have decayed below the relevance threshold.

        Logs each archival decision to the consolidation log.

        Args:
            project_context: Current project path for context scoring.

        Returns:
            List of archived memory dicts.
        """
        candidates = self.get_archival_candidates(project_context)
        archived = []

        for memory in candidates:
            try:
                self.store.archive(memory["id"])
                self.store.log_consolidation(
                    action="deprecated",
                    memory_id=memory["id"],
                    from_stage=memory["stage"],
                    to_stage="archived",
                    rationale=f"Relevance decayed to {memory['relevance']:.3f} "
                              f"(threshold: {self.archive_threshold})",
                )
                archived.append(memory)
                logger.info(
                    "Archived %s (%s) — relevance %.3f",
                    memory.get("title", "untitled"),
                    memory["id"],
                    memory["relevance"],
                )
            except ValueError as e:
                logger.warning("Failed to archive %s: %s", memory["id"], e)

        return archived

    # ------------------------------------------------------------------
    # Rehydration
    # ------------------------------------------------------------------

    def get_rehydration_candidates(
        self,
        project_context: str = None,
    ) -> list[dict]:
        """
        Find archived memories that are relevant to the current context.

        Scores all archived memories against the current project context.
        Those above the rehydration threshold are candidates.

        Args:
            project_context: Current project path for context scoring.

        Returns:
            List of archived memory dicts with 'relevance' key, sorted by
            relevance descending.
        """
        archived = self.store.list_archived()
        candidates = []

        for memory in archived:
            # Inhibition: memories subsumed into a crystallized insight
            # should not be rehydrated — they would compete with their
            # parent (retrieval-induced forgetting).
            if memory.get("subsumed_by"):
                continue

            relevance = self.compute_relevance(memory, project_context)
            if relevance >= self.rehydrate_threshold:
                memory["relevance"] = relevance
                candidates.append(memory)

        candidates.sort(key=lambda m: m["relevance"], reverse=True)
        return candidates

    def rehydrate_for_context(self, project_context: str = None) -> list[dict]:
        """
        Unarchive memories that are relevant to the current context.

        Logs each rehydration to the consolidation log.

        Args:
            project_context: Current project path.

        Returns:
            List of rehydrated memory dicts.
        """
        candidates = self.get_rehydration_candidates(project_context)
        rehydrated = []

        for memory in candidates:
            try:
                self.store.unarchive(memory["id"])
                self.store.log_consolidation(
                    action="promoted",
                    memory_id=memory["id"],
                    from_stage="archived",
                    to_stage=memory["stage"],
                    rationale=f"Rehydrated — relevance {memory['relevance']:.3f} "
                              f"exceeds threshold {self.rehydrate_threshold} "
                              f"in context {project_context or 'global'}",
                )
                rehydrated.append(memory)
                logger.info(
                    "Rehydrated %s (%s) — relevance %.3f",
                    memory.get("title", "untitled"),
                    memory["id"],
                    memory["relevance"],
                )
            except ValueError as e:
                logger.warning("Failed to rehydrate %s: %s", memory["id"], e)

        return rehydrated

    def find_rehydration_by_observation(self, observation: str) -> list[dict]:
        """
        Check if a new observation matches any archived memories.

        Called during consolidation after a KEEP decision — if the new
        observation is about a topic that has archived memories, those
        memories should be rehydrated.

        Uses FTS search against archived memories only.

        Args:
            observation: The text of the new observation.

        Returns:
            List of archived memory dicts that match, with 'relevance' key.
        """
        # Extract significant words (4+ chars) for FTS query
        words = [w for w in observation.split() if len(w) >= 4 and w.isalpha()]
        if not words:
            return []

        # Build OR query for FTS
        query = " OR ".join(words[:10])  # cap at 10 terms

        try:
            fts_results = self.store.search_fts(query, limit=REHYDRATION_FTS_LIMIT)
        except Exception:
            return []

        # Filter to archived only, excluding subsumed memories (inhibition)
        matches = []
        for memory in fts_results:
            if memory.get("archived_at") and not memory.get("subsumed_by"):
                relevance = self.compute_relevance(memory)
                memory["relevance"] = relevance
                matches.append(memory)

        return matches

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def run_maintenance(self, project_context: str = None) -> dict:
        """
        Run a full maintenance cycle: archive stale, rehydrate relevant.

        Intended to be called periodically (e.g., during PreCompact or
        on a cron schedule).

        Args:
            project_context: Current project path.

        Returns:
            Summary dict with 'archived' and 'rehydrated' lists.
        """
        archived = self.archive_stale(project_context)
        rehydrated = self.rehydrate_for_context(project_context)

        return {
            "archived": archived,
            "rehydrated": rehydrated,
        }

    def score_all(self, project_context: str = None) -> list[dict]:
        """
        Score all active (non-ephemeral, non-archived) memories.

        Useful for diagnostics and the observability dashboard.

        Args:
            project_context: Current project path.

        Returns:
            List of (memory_id, title, stage, relevance) dicts,
            sorted by relevance descending.
        """
        scored = []
        for stage in ("consolidated", "crystallized", "instinctive"):
            memories = self.store.list_by_stage(stage, include_archived=False)
            for memory in memories:
                relevance = self.compute_relevance(memory, project_context)
                scored.append({
                    "id": memory["id"],
                    "title": memory.get("title"),
                    "stage": stage,
                    "importance": memory.get("importance", 0.5),
                    "relevance": relevance,
                    "days_since_activity": self._days_since_last_activity(memory),
                })

        scored.sort(key=lambda m: m["relevance"], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _days_since_last_activity(memory: dict, now: datetime = None) -> float:
        """
        Calculate days since the memory was last injected or used.

        Uses the most recent of last_injected_at, last_used_at, updated_at,
        and created_at as the activity timestamp.
        """
        if now is None:
            now = datetime.now()

        candidates = []
        for field in ("last_used_at", "last_injected_at", "updated_at", "created_at"):
            val = memory.get(field)
            if val:
                try:
                    candidates.append(datetime.fromisoformat(val))
                except (ValueError, TypeError):
                    pass

        if not candidates:
            return 365.0  # treat as very old if no timestamps at all

        last_activity = max(candidates)
        delta = now - last_activity
        return max(0.0, delta.total_seconds() / 86400)
