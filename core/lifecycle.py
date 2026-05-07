"""
Lifecycle state machine for memory promotion/demotion with validation.

Implements D-07 (3+ reinforcements for crystallized), D-08 (cross-project promotion),
and D-09 (demotion for unused memories).
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from peewee import fn

from .database import get_base_dir
from .models import ConsolidationLog, Memory, RetrievalLog, db


class LifecycleManager:
    """
    Manages memory lifecycle transitions with validation and logging.

    Stage progression:
        ephemeral -> consolidated -> crystallized -> instinctive

    Promotion rules:
        - ephemeral -> consolidated (always valid during consolidation)
        - consolidated -> crystallized: requires reinforcement_count >= 3
          with temporal spacing (spacing effect -- brain-inspired)
        - crystallized -> instinctive: requires importance > 0.85 AND usage in 10+ sessions

    Demotion rules:
        - Always valid, can skip stages
        - Triggered by low usage or staleness
    """

    # Stage order for validation
    STAGE_ORDER = ['ephemeral', 'consolidated', 'crystallized', 'instinctive']

    # Spacing effect: minimum distinct calendar days reinforcements must span.
    MIN_REINFORCEMENT_SPAN_DAYS = 2

    def __init__(self):
        pass

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
        memory = Memory.get_by_id(memory_id)
        current_stage = memory.stage

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
            raise ValueError(f"Invalid transition: {current_stage} -> {next_stage}")

        # Perform transition
        memory.stage = next_stage
        memory.save()
        memory.set_expiry()

        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            action='promoted',
            memory_id=memory_id,
            from_stage=current_stage,
            to_stage=next_stage,
            rationale=rationale,
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
        memory = Memory.get_by_id(memory_id)
        current_stage = memory.stage

        # Determine target stage (demote by one level)
        current_idx = self.STAGE_ORDER.index(current_stage)
        if current_idx == 0:
            raise ValueError(f"Memory already at lowest stage: {current_stage}")

        target_stage = self.STAGE_ORDER[current_idx - 1]

        # Perform transition
        memory.stage = target_stage
        memory.save()
        memory.set_expiry()

        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            action='demoted',
            memory_id=memory_id,
            from_stage=current_stage,
            to_stage=target_stage,
            rationale=rationale,
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
        memory = Memory.get_by_id(memory_id)
        current_stage = memory.stage

        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            action='deprecated',
            memory_id=memory_id,
            from_stage=current_stage,
            to_stage='archived',
            rationale=rationale,
        )
        memory.delete_instance()

    def get_promotion_candidates(self) -> list[dict]:
        """
        Get memories eligible for promotion to crystallized stage.

        Returns:
            List of consolidated memories with reinforcement_count >= 3
        """
        consolidated_memories = list(Memory.by_stage('consolidated'))
        candidates = []

        for memory in consolidated_memories:
            mem_dict = {
                'id': memory.id,
                'title': memory.title,
                'reinforcement_count': memory.reinforcement_count,
                'stage': memory.stage,
                'importance': memory.importance,
                'usage_count': memory.usage_count,
            }
            can_promote, reason = self._can_promote_to_crystallized(mem_dict)
            if can_promote:
                mem_dict['reason'] = reason
                candidates.append(mem_dict)

        return candidates

    def get_demotion_candidates(self) -> list[dict]:
        """
        Get memories that should be demoted (injected 10+ times but never used).

        Returns:
            List of memories meeting demotion criteria (D-09)
        """
        memories = (
            Memory.select()
            .where(
                Memory.injection_count >= 10,
                Memory.usage_count == 0,
                Memory.stage.in_(['crystallized', 'instinctive']),
            )
            .order_by(Memory.injection_count.desc())
        )

        candidates = []
        for memory in memories:
            candidates.append({
                'id': memory.id,
                'title': memory.title,
                'stage': memory.stage,
                'injection_count': memory.injection_count,
                'usage_count': memory.usage_count,
                'reason': f"Injected {memory.injection_count} times but never used",
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

        memories = (
            Memory.select()
            .where(
                (Memory.last_injected_at.is_null() | (Memory.last_injected_at < cutoff_date)),
                (Memory.last_used_at.is_null() | (Memory.last_used_at < cutoff_date)),
                Memory.stage == 'ephemeral',
            )
            .order_by(Memory.created_at.asc())
        )

        candidates = []
        for memory in memories:
            last_activity = memory.last_injected_at or memory.last_used_at or memory.created_at
            candidates.append({
                'id': memory.id,
                'title': memory.title,
                'stage': memory.stage,
                'last_activity': last_activity,
                'reason': f"No activity since {last_activity}",
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
        memory = Memory.get_by_id(memory_id)
        current_stage = memory.stage

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
            mem_dict = {
                'id': memory.id,
                'reinforcement_count': memory.reinforcement_count,
                'stage': memory.stage,
            }
            return self._can_promote_to_crystallized(mem_dict)

        if current_stage == 'crystallized' and next_stage == 'instinctive':
            mem_dict = {
                'id': memory.id,
                'importance': memory.importance,
                'usage_count': memory.usage_count,
            }
            return self._can_promote_to_instinctive(mem_dict)

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
        reinforcement_count = memory.get('reinforcement_count', 0) or 0
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
        """
        if min_distinct_days is None:
            min_distinct_days = self.MIN_REINFORCEMENT_SPAN_DAYS

        rows = (
            ConsolidationLog.select(
                fn.DISTINCT(fn.DATE(ConsolidationLog.timestamp)).alias('reinforcement_date')
            )
            .where(
                ConsolidationLog.memory_id == memory_id,
                ConsolidationLog.action == 'promoted',
                ConsolidationLog.from_stage == ConsolidationLog.to_stage,
            )
            .order_by(fn.DATE(ConsolidationLog.timestamp))
        )
        dates = [r.reinforcement_date for r in rows]

        # No log entries -> reinforcement_count was set directly
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
        importance = memory.get('importance', 0.5) or 0.5
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
        result = (
            RetrievalLog.select(fn.COUNT(fn.DISTINCT(RetrievalLog.session_id)))
            .where(
                RetrievalLog.memory_id == memory_id,
                RetrievalLog.was_used == 1,
            )
            .scalar()
        )
        return result or 0

    def get_instinctive_coverage(self) -> dict:
        """
        Compute Zipf-style coverage statistics for instinctive memories.

        Returns a dict showing what fraction of total sessions are covered
        by the top N instinctive memories.  This implements the "Basic English"
        insight from linguistic-compression research: a small core vocabulary
        (memories) should cover the majority of usage.

        Returns:
            {
                "total_sessions": int,
                "instinctive_count": int,
                "coverage_curve": [
                    {"top_n": 1, "sessions_covered": int, "pct": float},
                    {"top_n": 5, "sessions_covered": int, "pct": float},
                    ...
                ],
                "memories": [
                    {"id": str, "title": str, "sessions": int, "pct": float},
                    ...
                ],
            }
        """
        # Total sessions ever recorded
        total_sessions = (
            RetrievalLog.select(fn.COUNT(fn.DISTINCT(RetrievalLog.session_id)))
            .scalar()
        ) or 0

        if total_sessions == 0:
            return {
                "total_sessions": 0,
                "instinctive_count": 0,
                "coverage_curve": [],
                "memories": [],
            }

        # All instinctive memories with their session counts
        instinctive = list(Memory.by_stage("instinctive"))
        memories = []
        for mem in instinctive:
            sessions = self._count_unique_sessions(mem.id)
            memories.append({
                "id": mem.id,
                "title": mem.title or "(untitled)",
                "sessions": sessions,
                "pct": (sessions / total_sessions) * 100,
            })

        # Sort by session count descending (Zipf order)
        memories.sort(key=lambda m: m["sessions"], reverse=True)

        # Build coverage curve for top N = 1, 3, 5, 10, 20
        coverage_curve = []
        cumulative_sessions = set()
        top_n_values = [1, 3, 5, 10, 20]
        for n in top_n_values:
            if n > len(memories):
                break
            # Count unique sessions covered by top N memories
            top_n_ids = [m["id"] for m in memories[:n]]
            sessions_covered = (
                RetrievalLog.select(fn.COUNT(fn.DISTINCT(RetrievalLog.session_id)))
                .where(
                    RetrievalLog.memory_id.in_(top_n_ids),
                    RetrievalLog.was_used == 1,
                )
                .scalar()
            ) or 0
            coverage_curve.append({
                "top_n": n,
                "sessions_covered": sessions_covered,
                "pct": (sessions_covered / total_sessions) * 100,
            })

        return {
            "total_sessions": total_sessions,
            "instinctive_count": len(memories),
            "coverage_curve": coverage_curve,
            "memories": memories,
        }
