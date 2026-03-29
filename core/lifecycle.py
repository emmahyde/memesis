"""
Lifecycle state machine for memory promotion/demotion with validation.

Implements D-07 (3+ reinforcements for crystallized), D-08 (cross-project promotion),
and D-09 (demotion for unused memories).
"""

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .storage import MemoryStore


class LifecycleManager:
    """
    Manages memory lifecycle transitions with validation and logging.

    Stage progression:
        ephemeral → consolidated → crystallized → instinctive

    Promotion rules:
        - ephemeral → consolidated (always valid during consolidation)
        - consolidated → crystallized: requires reinforcement_count >= 3
          with temporal spacing (spacing effect — brain-inspired)
        - crystallized → instinctive: requires importance > 0.85 AND usage in 10+ sessions

    Demotion rules:
        - Always valid, can skip stages
        - Triggered by low usage or staleness
    """

    # Stage order for validation
    STAGE_ORDER = ['ephemeral', 'consolidated', 'crystallized', 'instinctive']

    # Spacing effect: minimum distinct calendar days reinforcements must span.
    # The brain forms stronger memories when reinforcement is distributed over
    # time rather than massed in a single session (Ebbinghaus, 1885; Cepeda
    # et al., 2006). A value of 2 catches the worst case (all-in-one-day burst)
    # while allowing rapid but genuine multi-day reinforcement.
    MIN_REINFORCEMENT_SPAN_DAYS = 2

    def __init__(self, store: MemoryStore):
        """
        Initialize lifecycle manager.

        Args:
            store: MemoryStore instance for persistence
        """
        self.store = store

    def promote(self, memory_id: str, rationale: str) -> str:
        """
        Promote a memory to the next stage.

        Args:
            memory_id: Memory UUID
            rationale: Explanation for promotion

        Returns:
            New stage name

        Raises:
            ValueError: If promotion is not allowed
        """
        memory = self.store.get(memory_id)
        current_stage = memory['stage']

        # Determine next stage
        current_idx = self.STAGE_ORDER.index(current_stage)
        if current_idx == len(self.STAGE_ORDER) - 1:
            raise ValueError(f"Memory already at highest stage: {current_stage}")

        next_stage = self.STAGE_ORDER[current_idx + 1]

        # Validate transition
        can_promote, reason = self.can_promote(memory_id)
        if not can_promote:
            raise ValueError(f"Cannot promote memory: {reason}")

        if not self.validate_transition(current_stage, next_stage):
            raise ValueError(f"Invalid transition: {current_stage} → {next_stage}")

        # Perform transition
        self.store.update(memory_id, metadata={'stage': next_stage})
        self.store.log_consolidation(
            action='promoted',
            memory_id=memory_id,
            from_stage=current_stage,
            to_stage=next_stage,
            rationale=rationale
        )

        return next_stage

    def demote(self, memory_id: str, rationale: str) -> str:
        """
        Demote a memory to a lower stage (can skip stages).

        Args:
            memory_id: Memory UUID
            rationale: Explanation for demotion

        Returns:
            New stage name

        Raises:
            ValueError: If memory is already at lowest stage
        """
        memory = self.store.get(memory_id)
        current_stage = memory['stage']

        # Determine target stage (demote by one level)
        current_idx = self.STAGE_ORDER.index(current_stage)
        if current_idx == 0:
            raise ValueError(f"Memory already at lowest stage: {current_stage}")

        target_stage = self.STAGE_ORDER[current_idx - 1]

        # Perform transition
        self.store.update(memory_id, metadata={'stage': target_stage})
        self.store.log_consolidation(
            action='demoted',
            memory_id=memory_id,
            from_stage=current_stage,
            to_stage=target_stage,
            rationale=rationale
        )

        return target_stage

    def deprecate(self, memory_id: str, rationale: str) -> None:
        """
        Deprecate a memory by moving it to archived/ directory.

        Args:
            memory_id: Memory UUID
            rationale: Explanation for deprecation

        Raises:
            ValueError: If memory not found
        """
        memory = self.store.get(memory_id)
        current_stage = memory['stage']
        file_path = Path(memory['file_path'])

        archived_path = self.store.base_dir / 'archived' / file_path.name
        archived_path.parent.mkdir(parents=True, exist_ok=True)

        # Move file first; store.delete() skips unlink if file is absent from original path
        if file_path.exists():
            shutil.move(str(file_path), str(archived_path))

        # DB operations — roll back file move on failure so state stays consistent
        try:
            self.store.log_consolidation(
                action='deprecated',
                memory_id=memory_id,
                from_stage=current_stage,
                to_stage='archived',
                rationale=rationale
            )
            self.store.delete(memory_id)
        except Exception:
            if archived_path.exists():
                shutil.move(str(archived_path), str(file_path))
            raise

    def get_promotion_candidates(self) -> list[dict]:
        """
        Get memories eligible for promotion to crystallized stage.

        Returns:
            List of consolidated memories with reinforcement_count >= 3
        """
        consolidated_memories = self.store.list_by_stage('consolidated')
        candidates = []

        for memory in consolidated_memories:
            can_promote, reason = self._can_promote_to_crystallized(memory)
            if can_promote:
                candidates.append({
                    'id': memory['id'],
                    'title': memory['title'],
                    'reinforcement_count': memory['reinforcement_count'],
                    'stage': memory['stage'],
                    'reason': reason
                })

        return candidates

    def get_demotion_candidates(self) -> list[dict]:
        """
        Get memories that should be demoted (injected 10+ times but never used).

        Returns:
            List of memories meeting demotion criteria (D-09)
        """
        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Find memories with high injection count but zero usage
            cursor.execute('''
                SELECT *
                FROM memories
                WHERE injection_count >= 10
                  AND usage_count = 0
                  AND stage IN ('crystallized', 'instinctive')
                ORDER BY injection_count DESC
            ''')

            candidates = []
            for row in cursor.fetchall():
                memory = dict(row)
                candidates.append({
                    'id': memory['id'],
                    'title': memory['title'],
                    'stage': memory['stage'],
                    'injection_count': memory['injection_count'],
                    'usage_count': memory['usage_count'],
                    'reason': f"Injected {memory['injection_count']} times but never used"
                })

            return candidates

    def get_deprecation_candidates(self, stale_sessions: int = 30) -> list[dict]:
        """
        Get memories that haven't been used or injected recently.

        Args:
            stale_sessions: Number of days to consider stale (default: 30)

        Returns:
            List of stale memories
        """
        cutoff_date = (datetime.now() - timedelta(days=stale_sessions)).isoformat()

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Find memories not injected or used since cutoff
            cursor.execute('''
                SELECT *
                FROM memories
                WHERE (last_injected_at IS NULL OR last_injected_at < ?)
                  AND (last_used_at IS NULL OR last_used_at < ?)
                  AND stage = 'ephemeral'
                ORDER BY created_at ASC
            ''', (cutoff_date, cutoff_date))

            candidates = []
            for row in cursor.fetchall():
                memory = dict(row)
                last_activity = memory['last_injected_at'] or memory['last_used_at'] or memory['created_at']
                candidates.append({
                    'id': memory['id'],
                    'title': memory['title'],
                    'stage': memory['stage'],
                    'last_activity': last_activity,
                    'reason': f"No activity since {last_activity}"
                })

            return candidates

    def can_promote(self, memory_id: str) -> tuple[bool, str]:
        """
        Check if a memory can be promoted to the next stage.

        Args:
            memory_id: Memory UUID

        Returns:
            (eligible, reason) tuple
        """
        memory = self.store.get(memory_id)
        current_stage = memory['stage']

        # Check if already at highest stage
        if current_stage == 'instinctive':
            return False, "Already at highest stage (instinctive)"

        current_idx = self.STAGE_ORDER.index(current_stage)
        next_stage = self.STAGE_ORDER[current_idx + 1]

        # Validate specific promotion rules
        if current_stage == 'ephemeral' and next_stage == 'consolidated':
            # Always valid during consolidation
            return True, "Eligible for consolidation"

        if current_stage == 'consolidated' and next_stage == 'crystallized':
            return self._can_promote_to_crystallized(memory)

        if current_stage == 'crystallized' and next_stage == 'instinctive':
            return self._can_promote_to_instinctive(memory)

        return True, "Transition allowed"

    def validate_transition(self, from_stage: str, to_stage: str) -> bool:
        """
        Validate that a stage transition is legal.

        Args:
            from_stage: Source stage
            to_stage: Target stage

        Returns:
            True if transition is valid
        """
        if from_stage not in self.STAGE_ORDER or to_stage not in self.STAGE_ORDER:
            return False

        from_idx = self.STAGE_ORDER.index(from_stage)
        to_idx = self.STAGE_ORDER.index(to_stage)

        # Promotion: can only advance one stage at a time
        if to_idx > from_idx:
            return to_idx == from_idx + 1

        # Demotion: can skip stages
        if to_idx < from_idx:
            return True

        # Same stage
        return False

    def _can_promote_to_crystallized(self, memory: dict) -> tuple[bool, str]:
        """
        Check if memory meets criteria for promotion to crystallized.

        D-07: Requires 3+ independent reinforcements.
        Spacing effect: reinforcements must span multiple distinct days.
        """
        reinforcement_count = memory.get('reinforcement_count', 0)
        if reinforcement_count < 3:
            return False, f"Only {reinforcement_count} reinforcements (need 3+)"

        # Spacing effect: check temporal distribution
        spaced, spacing_reason = self._has_spaced_reinforcement(memory['id'])
        if not spaced:
            return False, spacing_reason

        return True, f"Has {reinforcement_count} reinforcements with adequate spacing"

    def _has_spaced_reinforcement(
        self,
        memory_id: str,
        min_distinct_days: int = None,
    ) -> tuple[bool, str]:
        """
        Check if reinforcements are temporally spaced (spacing effect).

        The brain forms stronger memories when reinforcement is distributed
        across time rather than concentrated in a single burst. This method
        checks that consolidation log entries for a memory span at least
        MIN_REINFORCEMENT_SPAN_DAYS distinct calendar days.

        If no consolidation log entries exist (legacy/manual reinforcement_count),
        the check passes — the count was set deliberately outside the normal
        consolidation path.

        Args:
            memory_id: Memory UUID.
            min_distinct_days: Override for MIN_REINFORCEMENT_SPAN_DAYS.

        Returns:
            (spaced, reason) tuple.
        """
        if min_distinct_days is None:
            min_distinct_days = self.MIN_REINFORCEMENT_SPAN_DAYS

        with sqlite3.connect(self.store.db_path) as conn:
            # Reinforcement events have from_stage == to_stage (memory stays
            # in its current stage but gets reinforced). Stage transitions
            # have from_stage != to_stage — we exclude those.
            cursor = conn.execute(
                """
                SELECT DISTINCT DATE(timestamp) AS reinforcement_date
                FROM consolidation_log
                WHERE memory_id = ? AND action = 'promoted'
                  AND from_stage = to_stage
                ORDER BY reinforcement_date
                """,
                (memory_id,),
            )
            dates = [row[0] for row in cursor.fetchall()]

        # No log entries → reinforcement_count was set directly
        # (backfill, manual, or legacy). Trust the count.
        if not dates:
            return True, "No consolidation log entries (manual/legacy reinforcement)"

        if len(dates) < min_distinct_days:
            return False, (
                f"Reinforced on {len(dates)} distinct day(s) "
                f"(need {min_distinct_days}+). "
                f"Spacing effect: spaced reinforcement produces more durable memories."
            )

        return True, f"Reinforced across {len(dates)} distinct days"

    def _can_promote_to_instinctive(self, memory: dict) -> tuple[bool, str]:
        """
        Check if memory meets criteria for promotion to instinctive.

        Requires: importance > 0.85 AND usage in 10+ sessions.
        """
        importance = memory.get('importance', 0.5)
        usage_count = memory.get('usage_count', 0)

        # Count unique sessions from retrieval log
        session_count = self._count_unique_sessions(memory['id'])

        if importance <= 0.85:
            return False, f"Importance {importance:.2f} too low (need >0.85)"

        if session_count < 10:
            return False, f"Used in {session_count} sessions (need 10+)"

        return True, f"High importance ({importance:.2f}) and used in {session_count} sessions"

    def _count_unique_sessions(self, memory_id: str) -> int:
        """
        Count unique sessions where memory was used.

        Args:
            memory_id: Memory UUID

        Returns:
            Number of unique sessions
        """
        with sqlite3.connect(self.store.db_path) as conn:
            cursor = conn.execute('''
                SELECT COUNT(DISTINCT session_id)
                FROM retrieval_log
                WHERE memory_id = ? AND was_used = 1
            ''', (memory_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
