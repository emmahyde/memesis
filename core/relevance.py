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
    usage_signal = clamp(0.3 + 0.7 * (usage_count / 5), 0.0, 1.0)
    context_boost = 1.5 when project matches, 1.0 otherwise

NOTE: usage_signal is decoupled from injection_count (RISK-09). injection_count
is still tracked in the DB for observability, but does not affect scoring.

This produces a smooth decay curve — memories fade gradually, never cliff-edge.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import nltk
from nltk.corpus import stopwords as nltk_stopwords
from nltk.stem import PorterStemmer as NltkStemmer

from .database import get_vec_store
from .models import ConsolidationLog, Memory, db

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

    The engine reads from and writes to Peewee models directly.
    """

    def __init__(
        self,
        archive_threshold: float = ARCHIVE_THRESHOLD,
        rehydrate_threshold: float = REHYDRATE_THRESHOLD,
        half_life_days: float = RECENCY_HALF_LIFE,
    ):
        self.archive_threshold = archive_threshold
        self.rehydrate_threshold = rehydrate_threshold
        self.half_life_days = half_life_days

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def compute_relevance(
        self,
        memory,
        project_context: str = None,
        now: datetime = None,
    ) -> float:
        """
        Compute a relevance score in [0, 1] for a memory.

        Args:
            memory: Memory model instance or dict with metadata fields.
            project_context: Current project path for context matching.
            now: Override current time (for testing).

        Returns:
            Relevance score between 0.0 and 1.0.
        """
        if now is None:
            now = datetime.now()

        # Support both model instances and dicts
        def _get(field, default=None):
            if isinstance(memory, dict):
                return memory.get(field, default)
            return getattr(memory, field, default)

        importance = _get("importance", 0.5) or 0.5

        # Recency: exponential decay from last activity
        days_since = self._days_since_last_activity(memory, now)
        recency = 0.5 ** (days_since / self.half_life_days) if self.half_life_days > 0 else 1.0

        # Usage signal: reflects confirmed utility, not injection frequency.
        # Decoupled from injection_count (RISK-09): using injection_count as the
        # denominator created a feedback loop where frequently-retrieved memories
        # appeared more useful regardless of actual confirmed usage.
        # New formula: saturating function over usage_count alone.
        # A memory used 5+ times earns full signal; zero-usage starts at 0.3.
        usage_count = _get("usage_count", 0) or 0
        _USAGE_SATURATION = 5  # confirmed uses for full usage_signal
        usage_signal = min(1.0, 0.3 + 0.7 * (usage_count / _USAGE_SATURATION))

        # Context boost: memories from the same project are more relevant
        context_boost = 1.0
        if project_context and _get("project_context") == project_context:
            context_boost = 1.5

        # Saturation penalty: memories confirmed as unused in recent injections.
        # Previously used injection_count as the accumulator (RISK-09 coupling).
        # Now uses confirmed usage_count as the baseline: memories with zero
        # confirmed uses accumulate a penalty up to 0.3. This preserves the
        # saturation signal without coupling it to raw injection frequency.
        from .flags import get_flag
        saturation_penalty = 0.0
        if get_flag("saturation_decay"):
            # Penalty scales with how under-used the memory is relative to
            # a low baseline (1 confirmed use clears the first penalty step).
            # injection_count is preserved in the DB for observability (RISK-09),
            # but does not contribute to this calculation.
            unused_count = max(1 - usage_count, 0)  # 0 if used at least once
            saturation_penalty = min(0.3, unused_count * 0.1)

        # Integration factor: isolated memories decay faster
        integration_factor = 1.0
        if get_flag("integration_factor"):
            reinforcement = _get("reinforcement_count", 0) or 0
            # Check thread membership, tag co-occurrence, and causal edges
            has_thread = self._has_thread_membership(memory)
            has_tag_overlap = self._has_tag_overlap(memory)
            has_causal = self._has_causal_edges(memory) if get_flag("causal_edges") else False
            has_contradiction = self._has_contradiction_edges(memory) if get_flag("contradiction_tensors") else False

            connected = has_thread or has_tag_overlap or has_causal or has_contradiction
            if not connected and reinforcement == 0:
                # Fully isolated — significant penalty
                integration_factor = 0.5
            elif not connected:
                # No connections but has reinforcement — mild penalty
                integration_factor = 0.75
            # else: connected — no penalty (1.0)

        # W5 schema-promoted fields (Wave 3a). Wired behind flags so unset
        # columns are no-op for legacy memories.
        # affect_valence: Kensinger prior — friction-bearing memories are
        #   load-bearing in similar future contexts; surface them earlier.
        # temporal_scope: session-local memories should not follow the user
        #   across sessions; cross-session-durable get a small lift.
        # confidence: multiplicative tie-breaker for extraction certainty.
        affect_factor = 1.0
        if get_flag("affect_weighted_retrieval"):
            valence = _get("affect_valence")
            if valence == "friction":
                affect_factor = 1.20
            elif valence == "delight":
                affect_factor = 1.10
            elif valence == "surprise":
                affect_factor = 1.05
            # neutral / mixed / None: no change

        scope_factor = 1.0
        if get_flag("temporal_scope_weighting"):
            scope = _get("temporal_scope")
            if scope == "session-local":
                scope_factor = 0.6
            elif scope == "cross-session-durable":
                scope_factor = 1.10
            elif scope == "permanent":
                scope_factor = 1.20
            # None / unset: no change

        confidence_factor = 1.0
        if get_flag("confidence_weighting"):
            confidence = _get("confidence")
            if confidence is not None:
                # Map [0.0, 1.0] confidence → [0.7, 1.0] factor so low-confidence
                # memories are demoted but never zeroed out.
                confidence_factor = 0.7 + 0.3 * max(0.0, min(1.0, float(confidence)))

        # Weighted geometric mean
        relevance = (
            (importance ** 0.4)
            * (recency ** 0.3)
            * (usage_signal ** 0.2)
            * (context_boost ** 0.1)
            * integration_factor
            * affect_factor
            * scope_factor
            * confidence_factor
        )

        novelty_score = self._compute_novelty_score(memory, now)
        relevance = relevance * novelty_score

        # Apply saturation penalty as subtraction (post-multiply)
        relevance = relevance - saturation_penalty

        return min(1.0, max(0.0, relevance))

    # ------------------------------------------------------------------
    # Integration factor helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_thread_membership(memory) -> bool:
        """Check if memory belongs to any narrative thread."""
        from .models import ThreadMember
        mid = memory.id if hasattr(memory, 'id') else memory.get('id')
        if not mid:
            return False
        return ThreadMember.select().where(ThreadMember.memory_id == mid).exists()

    @staticmethod
    def _has_tag_overlap(memory) -> bool:
        """Check if memory shares any tags with other active memories."""
        if isinstance(memory, dict):
            tags = memory.get('tags', '[]')
            mid = memory.get('id')
        else:
            tags = memory.tags
            mid = memory.id

        import json
        try:
            tag_list = json.loads(tags) if isinstance(tags, str) else (tags or [])
        except (json.JSONDecodeError, TypeError):
            return False

        if not tag_list or not mid:
            return False

        # Check if any other active memory shares at least one tag
        for tag in tag_list:
            # Skip type: and valence: meta-tags — they're universal
            if tag.startswith("type:") or tag.startswith("valence:"):
                continue
            matches = (
                Memory.select()
                .where(
                    Memory.id != mid,
                    Memory.archived_at.is_null(),
                    Memory.tags.contains(tag),
                )
                .limit(1)
            )
            if matches.exists():
                return True
        return False

    @staticmethod
    def _has_causal_edges(memory) -> bool:
        """Check if memory participates in any causal relationship."""
        from .models import MemoryEdge
        mid = memory.id if hasattr(memory, 'id') else memory.get('id')
        if not mid:
            return False
        _CAUSAL_TYPES = ("caused_by", "refined_from", "subsumed_into")
        return MemoryEdge.select().where(
            ((MemoryEdge.source_id == mid) | (MemoryEdge.target_id == mid)),
            MemoryEdge.edge_type.in_(_CAUSAL_TYPES),
        ).exists()

    @staticmethod
    def _has_contradiction_edges(memory) -> bool:
        """Check if memory participates in any contradiction relationship.

        Checks both directions — memory may be the source (it contradicts
        something) or the target (something contradicts it).  Per D-01,
        contradiction edges exist for all resolution types (unresolved,
        resolved, superseded), so the presence of a contradicts edge is
        sufficient to consider the memory connected.
        """
        from .models import MemoryEdge
        mid = memory.id if hasattr(memory, 'id') else memory.get('id')
        if not mid:
            return False
        return MemoryEdge.select().where(
            ((MemoryEdge.source_id == mid) | (MemoryEdge.target_id == mid)),
            MemoryEdge.edge_type == "contradicts",
        ).exists()

    # ------------------------------------------------------------------
    # Archival
    # ------------------------------------------------------------------

    def get_archival_candidates(self, project_context: str = None) -> list:
        """
        Find active memories whose relevance has decayed below the archive threshold.

        Returns memories sorted by relevance ascending (least relevant first),
        each annotated with a 'relevance' attribute.
        """
        candidates = []

        for stage in ("consolidated", "crystallized"):
            memories = list(Memory.by_stage(stage, include_archived=False))
            for memory in memories:
                relevance = self.compute_relevance(memory, project_context)
                if relevance < self.archive_threshold:
                    memory._relevance = relevance
                    candidates.append(memory)

        candidates.sort(key=lambda m: m._relevance)
        return candidates

    def archive_stale(self, project_context: str = None) -> list:
        """
        Archive memories that have decayed below the relevance threshold.

        Returns:
            List of archived Memory instances.
        """
        candidates = self.get_archival_candidates(project_context)
        archived = []

        for memory in candidates:
            try:
                Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == memory.id).execute()
                ConsolidationLog.create(
                    timestamp=datetime.now().isoformat(),
                    action="deprecated",
                    memory_id=memory.id,
                    from_stage=memory.stage,
                    to_stage="archived",
                    rationale=f"Relevance decayed to {memory._relevance:.3f} "
                              f"(threshold: {self.archive_threshold})",
                )
                archived.append(memory)
                logger.info(
                    "Archived %s (%s) — relevance %.3f",
                    memory.title or "untitled",
                    memory.id,
                    memory._relevance,
                )
            except Exception as e:
                logger.warning("Failed to archive %s: %s", memory.id, e)

        return archived

    # ------------------------------------------------------------------
    # Rehydration
    # ------------------------------------------------------------------

    def get_rehydration_candidates(
        self,
        project_context: str = None,
    ) -> list:
        """
        Find archived memories that are relevant to the current context.
        """
        archived = list(
            Memory.select()
            .where(Memory.archived_at.is_null(False))
            .order_by(Memory.archived_at.desc())
        )
        candidates = []

        for memory in archived:
            # Inhibition: memories subsumed into a crystallized insight
            # should not be rehydrated
            if memory.subsumed_by:
                continue

            relevance = self.compute_relevance(memory, project_context)
            if relevance >= self.rehydrate_threshold:
                memory._relevance = relevance
                candidates.append(memory)

        candidates.sort(key=lambda m: m._relevance, reverse=True)
        return candidates

    def rehydrate_for_context(self, project_context: str = None) -> list:
        """
        Unarchive memories that are relevant to the current context.
        """
        candidates = self.get_rehydration_candidates(project_context)
        rehydrated = []

        for memory in candidates:
            try:
                Memory.update(
                    archived_at=None,
                    updated_at=datetime.now().isoformat(),
                ).where(Memory.id == memory.id).execute()
                ConsolidationLog.create(
                    timestamp=datetime.now().isoformat(),
                    action="promoted",
                    memory_id=memory.id,
                    from_stage="archived",
                    to_stage=memory.stage,
                    rationale=f"Rehydrated — relevance {memory._relevance:.3f} "
                              f"exceeds threshold {self.rehydrate_threshold} "
                              f"in context {project_context or 'global'}",
                )
                rehydrated.append(memory)
                logger.info(
                    "Rehydrated %s (%s) — relevance %.3f",
                    memory.title or "untitled",
                    memory.id,
                    memory._relevance,
                )
            except Exception as e:
                logger.warning("Failed to rehydrate %s: %s", memory.id, e)

        return rehydrated

    def find_rehydration_by_observation(self, observation: str) -> list:
        """
        Check if a new observation matches any archived memories.

        Uses FTS search against archived memories first, then supplements with
        semantic similarity matching via stored vector search.
        """
        # Extract significant words for FTS query
        try:
            nltk.data.find('corpora/stopwords')
            stop = set(nltk_stopwords.words('english'))
            stemmer = NltkStemmer()
            raw_words = [w.lower() for w in observation.split() if len(w) >= 4 and w.isalpha()]
            words = list({stemmer.stem(w) for w in raw_words if w not in stop})
        except Exception:
            words = [w.lower() for w in observation.split() if len(w) >= 4 and w.isalpha()]
        if not words:
            return []

        # Build OR query for FTS
        query = " OR ".join(Memory.sanitize_fts_term(w) for w in words[:10])

        try:
            fts_results = Memory.search_fts(query, limit=REHYDRATION_FTS_LIMIT)
        except Exception:
            fts_results = []

        # Filter to archived only, excluding subsumed memories
        matches = []
        for memory in fts_results:
            if memory.archived_at and not memory.subsumed_by:
                memory._relevance = self.compute_relevance(memory)
                matches.append(memory)

        # Supplement with semantic matches
        seen_ids = {m.id for m in matches}
        archived_pool = list(
            Memory.select()
            .where(
                Memory.archived_at.is_null(False),
                Memory.subsumed_by.is_null(),
                ~Memory.id.in_(list(seen_ids)) if seen_ids else True,
            )
        )
        semantic = self._find_semantic_matches(observation, archived_pool)
        for m in semantic:
            if m.id not in seen_ids:
                m._relevance = self.compute_relevance(m)
                matches.append(m)
                seen_ids.add(m.id)

        return matches

    def _find_semantic_matches(
        self,
        observation: str,
        archived_memories: list,
    ) -> list:
        """Find archived memories semantically similar to the observation using stored vectors."""
        from .embeddings import embed_text

        query_embedding = embed_text(observation)
        if query_embedding is None:
            return []

        vec_store = get_vec_store()
        if vec_store is None or not vec_store.available:
            return []

        # Use KNN search
        results = vec_store.search_vector(query_embedding, k=20)

        # Filter to only archived + not subsumed
        archived_ids = {m.id for m in archived_memories}
        matched = []
        for result in results:
            memory_id = result["memory_id"]
            distance = result["distance"]
            if memory_id in archived_ids:
                try:
                    mem = Memory.get_by_id(memory_id)
                    mem.distance = distance
                    matched.append(mem)
                except Memory.DoesNotExist:
                    continue
        return matched

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def run_maintenance(self, project_context: str = None) -> dict:
        """
        Run a full maintenance cycle: archive stale, rehydrate relevant.
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
        """
        scored = []
        for stage in ("consolidated", "crystallized", "instinctive"):
            memories = list(Memory.by_stage(stage, include_archived=False))
            for memory in memories:
                relevance = self.compute_relevance(memory, project_context)
                scored.append({
                    "id": memory.id,
                    "title": memory.title,
                    "stage": stage,
                    "importance": memory.importance or 0.5,
                    "relevance": relevance,
                    "days_since_activity": self._days_since_last_activity(memory),
                })

        scored.sort(key=lambda m: m["relevance"], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _days_since_last_activity(memory, now: datetime = None) -> float:
        """Calculate days since the memory was last injected or used."""
        if now is None:
            now = datetime.now()

        def _get(field):
            if isinstance(memory, dict):
                return memory.get(field)
            return getattr(memory, field, None)

        candidates = []
        for field in ("last_used_at", "last_injected_at", "updated_at", "created_at"):
            val = _get(field)
            if val:
                try:
                    candidates.append(datetime.fromisoformat(val))
                except (ValueError, TypeError):
                    pass

        if not candidates:
            return 365.0

        last_activity = max(candidates)
        delta = now - last_activity
        return max(0.0, delta.total_seconds() / 86400)

    def _compute_novelty_score(self, memory, now: datetime = None) -> float:
        from .flags import get_flag

        if not get_flag("continuous_novelty"):
            return 1.0

        if now is None:
            now = datetime.now()

        def _get(field, default=None):
            if isinstance(memory, dict):
                return memory.get(field, default)
            return getattr(memory, field, default)

        next_due_str = _get("next_injection_due")
        interval = _get("injection_interval_days") or 1.0

        if next_due_str is None:
            sm2_component = 1.0
        else:
            try:
                due = datetime.fromisoformat(next_due_str)
                if now < due:
                    sm2_component = 0.0
                else:
                    days_past_due = (now - due).total_seconds() / 86400.0
                    sm2_component = min(1.0, days_past_due / interval)
            except (ValueError, TypeError):
                sm2_component = 1.0

        habituation_component = 1.0
        try:
            from .database import get_base_dir
            from .habituation import HabituationModel

            base_dir = get_base_dir()
            if base_dir is not None:
                model = HabituationModel(base_dir)
                tags = _get("tag_list") or _get("tags") or []
                if isinstance(tags, str):
                    try:
                        import json as _json
                        tags = _json.loads(tags)
                    except (ValueError, TypeError):
                        tags = []
                type_tags = [t[5:] for t in tags if isinstance(t, str) and t.startswith("type:")]
                event_key = type_tags[0].lower() if type_tags else "untyped"
                habituation_component = model.get_factor(event_key)
        except Exception:
            habituation_component = 1.0

        last_injected_str = _get("last_injected_at")
        if last_injected_str is not None:
            try:
                last_injected = datetime.fromisoformat(last_injected_str)
                days_since = (now - last_injected).total_seconds() / 86400.0
                recency_component = 0.5 ** (days_since / 7.0)
            except (ValueError, TypeError):
                recency_component = 1.0
        else:
            recency_component = 1.0

        return (sm2_component + habituation_component + recency_component) / 3.0
