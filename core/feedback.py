"""
Feedback loop for memory lifecycle: usage tracking, importance scoring,
and promotion/demotion signals.

Implements D-08 (cross-project promotion: 3+ distinct projects) and
D-09 (demotion: injected 10+ times, never used).
"""

import json
import re
from datetime import datetime
from pathlib import Path

import nltk
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer
from peewee import fn

from .database import get_base_dir
from .lifecycle import LifecycleManager
from .models import Memory, RetrievalLog, db


def _ensure_nltk_data():
    """Download NLTK stopwords corpus on first use if not present."""
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        try:
            nltk.download('stopwords', quiet=True)
        except Exception:
            pass  # Network unavailable — fall back to no stopword filtering


_STOPWORDS: set | None = None
_STEMMER: PorterStemmer | None = None


def _get_nltk_tools() -> tuple:
    """Return (stopwords_set, stemmer), initializing lazily."""
    global _STOPWORDS, _STEMMER
    if _STOPWORDS is None:
        _ensure_nltk_data()
        try:
            _STOPWORDS = set(stopwords.words('english'))
            _STEMMER = PorterStemmer()
        except Exception:
            _STOPWORDS = set()
            _STEMMER = None
    return _STOPWORDS, _STEMMER


class FeedbackLoop:
    """
    Tracks memory usage within sessions and updates importance scores.

    Drives promotion/demotion signals based on usage patterns per D-08 and D-09.
    """

    def __init__(self, lifecycle: LifecycleManager):
        self.lifecycle = lifecycle
        # Accumulates {session_id: {memory_id: was_used}} across track_usage calls
        self._session_usage: dict[str, dict[str, bool]] = {}

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    # Source weights for usage scoring — title terms are the strongest signal,
    # content terms are weakest (diluted by volume but capture domain specifics).
    _TITLE_WEIGHT = 3.0
    _SUMMARY_WEIGHT = 2.0
    _CONTENT_WEIGHT = 1.0

    # Usage score threshold — lowered from 4.0 to 2.5. The original 4.0 required
    # 2 title-keyword hits for short words (3+3=6), but in practice this meant
    # memories with short titles ("Self-Model", "Compaction Guidance") were NEVER
    # marked as used — production showed 0% usage across 40 injections. A single
    # title keyword match with a specific term (3.0 * 1.5 = 4.5) should trigger;
    # a single short title keyword (3.0 * 1.0 = 3.0) should also trigger.
    _USAGE_THRESHOLD = 2.5

    def track_usage(self, session_id: str, injected_ids: list[str], response_text: str) -> dict:
        """
        Determine which injected memories were actually used in the response.

        Uses content-aware scoring: keywords from title, summary, AND content
        are weighted by source (title > summary > content) and by term
        specificity (longer words carry more weight). This catches domain-specific
        terms in memory content that the old title-only heuristic missed.

        Side effects:
        - Calls record_usage for each memory marked as used.
        - Logs a 'memory_used' event for each used memory.

        Returns:
            {memory_id: was_used (bool)}
        """
        response_lower = response_text.lower()
        usage_map: dict[str, bool] = {}

        for memory_id in injected_ids:
            try:
                memory = Memory.get_by_id(memory_id)
            except Memory.DoesNotExist:
                usage_map[memory_id] = False
                continue

            score = self._compute_usage_score(
                title=memory.title or '',
                summary=memory.summary or '',
                content=memory.content or '',
                response_lower=response_lower,
            )
            was_used = score >= self._USAGE_THRESHOLD

            usage_map[memory_id] = was_used

            # Update SM-2 spaced injection schedule
            from .spaced import update_sm2_schedule
            update_sm2_schedule(memory, was_used)

            if was_used:
                _record_usage(memory_id, session_id)
                # Normalize confidence to [0, 1] — score of 4 = 0 confidence,
                # score of 20+ = 1.0 confidence.
                confidence = min(1.0, max(0.0, (score - self._USAGE_THRESHOLD) / 16))
                self.log_event('memory_used', {
                    'session_id': session_id,
                    'memory_id': memory_id,
                    'confidence': confidence,
                })

        if session_id not in self._session_usage:
            self._session_usage[session_id] = {}
        self._session_usage[session_id].update(usage_map)

        return usage_map

    @staticmethod
    def _term_specificity(word: str) -> float:
        """
        Weight a term by length as a proxy for specificity.

        Short words (4-5 chars) are common and generic (e.g., "code", "test").
        Longer words are more domain-specific (e.g., "authentication", "idempotency").
        """
        n = len(word)
        if n >= 8:
            return 2.0
        if n >= 6:
            return 1.5
        return 1.0

    @classmethod
    def _compute_usage_score(
        cls,
        title: str,
        summary: str,
        content: str,
        response_lower: str,
    ) -> float:
        """
        Compute a weighted usage score for a memory against a response.

        Extracts keywords from title, summary, and content with decreasing
        source weight. Each matched keyword contributes:
            source_weight x term_specificity(word)

        Returns:
            Additive score (higher = stronger evidence of usage).
        """
        score = 0.0

        # Pre-stem all response tokens once so we can do O(S + R) stem
        # lookups rather than O(S x R) individual stem comparisons.
        stop_words, stemmer = _get_nltk_tools()
        if stemmer:
            response_words = re.findall(r'\b[a-z]{4,}\b', response_lower)
            stemmed_response = {stemmer.stem(w) for w in response_words if w not in stop_words}
        else:
            stemmed_response = None

        # Content is noisier than title/summary — require longer (5+ char)
        # keywords to avoid false positives from common short words like
        # "code", "file", "data", "list", "uses".
        for source_text, source_weight, min_len in (
            (title, cls._TITLE_WEIGHT, 4),
            (summary, cls._SUMMARY_WEIGHT, 4),
            (content, cls._CONTENT_WEIGHT, 5),
        ):
            words = source_text.lower().split()
            seen = set()  # deduplicate within each source
            for w in words:
                if len(w) >= min_len and w not in seen:
                    seen.add(w)
                    # Primary match: word-boundary regex (exact form match)
                    matched = bool(re.search(rf'\b{re.escape(w)}\b', response_lower))
                    # Fallback: stem match — catches inflected forms like
                    # "authentication" matching "authenticating"
                    if not matched and stemmer and w not in stop_words and stemmed_response is not None:
                        matched = stemmer.stem(w) in stemmed_response
                    if matched:
                        score += source_weight * cls._term_specificity(w)

        return score

    # ------------------------------------------------------------------
    # Importance score updates
    # ------------------------------------------------------------------

    def update_importance_scores(self, session_id: str) -> None:
        """
        Update importance scores for memories based on session usage.

        Rules:
        - Used this session: importance += 0.05, capped at 1.0
        - Injected in 3+ consecutive sessions but never used: importance -= 0.1,
          floored at 0.1

        Emits 'importance_updated' log events for every change.
        """
        session_map = self._session_usage.get(session_id, {})

        # Query non-ephemeral memories
        memories = Memory.select().where(Memory.stage != 'ephemeral')

        for row in memories:
            memory_id = row.id
            old_importance = row.importance or 0.5
            new_importance = old_importance

            # Rule 1: used this session -> bump up
            if session_map.get(memory_id) is True:
                new_importance = min(1.0, old_importance + 0.05)

            # Rule 2: 3+ consecutive injections with no usage -> nudge down
            elif self._has_three_consecutive_unused(memory_id):
                new_importance = max(0.1, old_importance - 0.1)

            if new_importance != old_importance:
                Memory.update(importance=new_importance).where(Memory.id == memory_id).execute()
                self.log_event('importance_updated', {
                    'memory_id': memory_id,
                    'old': old_importance,
                    'new': new_importance,
                })

    # ------------------------------------------------------------------
    # Signal queries
    # ------------------------------------------------------------------

    def get_promotion_signals(self) -> list[str]:
        """
        Return memory IDs ready for promotion (consolidated with 3+ reinforcements).
        """
        candidates = self.lifecycle.get_promotion_candidates()
        return [c['id'] for c in candidates]

    def get_demotion_signals(self) -> list[str]:
        """
        Return memory IDs ready for demotion (D-09: injected 10+ times, never used).
        """
        candidates = self.lifecycle.get_demotion_candidates()
        return [c['id'] for c in candidates]

    def get_cross_project_candidates(self) -> list[str]:
        """
        Return memory IDs injected in 3+ distinct project contexts (D-08).

        Queries retrieval_log for memories that have been injected across
        at least 3 different project_context values — the correct implementation
        of the "3+ distinct projects" criterion from D-08.
        """
        rows = (
            RetrievalLog.select(RetrievalLog.memory_id)
            .where(
                RetrievalLog.retrieval_type == 'injected',
                RetrievalLog.project_context.is_null(False),
            )
            .group_by(RetrievalLog.memory_id)
            .having(fn.COUNT(fn.DISTINCT(RetrievalLog.project_context)) >= 3)
        )
        return [row.memory_id for row in rows]

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    def log_event(self, event_type: str, data: dict) -> None:
        """
        Append a single JSON event line to meta/retrieval-log.jsonl.

        Format:
            {"event": "<type>", "timestamp": "<iso>", ...data fields}
        """
        base_dir = get_base_dir()
        log_path = base_dir / 'meta' / 'retrieval-log.jsonl'
        log_path.parent.mkdir(parents=True, exist_ok=True)

        record = {'event': event_type, 'timestamp': datetime.now().isoformat()}
        record.update(data)

        with log_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record) + '\n')

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Bridge to extraction-side self-reflection (Stage 3 closed-loop)
    # ------------------------------------------------------------------
    #
    # FeedbackLoop owns retrieval-signal data (was_used, importance trends);
    # `core/self_reflection_extraction.py` owns extraction-time process data
    # (productive_rate, parse_errors, etc.). Historically these were fully
    # disjoint — extraction-side rules could not be confirmed or refuted by
    # whether their outputs ever got retrieved.
    #
    # `reconcile_extraction_rules()` writes meta-rule findings into the same
    # `self_observations.jsonl` that `aggregate_audit()` already consumes, so
    # `core/rule_registry.RULE_OVERRIDES` can act on them uniformly.

    def reconcile_extraction_rules(
        self,
        *,
        lookback_days: int = 14,
        importance_threshold: float = 0.7,
        unused_count_threshold: int = 3,
    ) -> list[str]:
        """Emit meta-rules that judge extraction quality from retrieval data.

        Currently fires:

          - `cards_unused_high_importance` — at least N cards with
            `importance >= threshold` in the lookback window have never been
            retrieved (no RetrievalLog entry with `was_used=1`). Refutes the
            celebratory `issue_card_collapse_efficient` rule when the cards
            it celebrates aren't actually useful.

          - `extraction_under_retrieval_pressure` — there are repeated
            `injected` retrievals (≥3) that resulted in no `was_used=1`
            event AND fewer than 5 distinct memory_ids were retrieved across
            those injections (signal: same small pool re-served, store likely
            missing topic coverage).

        Returns the list of rule_ids that fired this run.
        """
        # Late import: avoid pulling self_reflection_extraction at module-load
        # time (keeps test isolation simple and breaks an otherwise circular
        # import potential if extraction-side ever needs FeedbackLoop).
        from datetime import timedelta

        from core.self_reflection_extraction import (
            SelfObservation,
            _append_audit,
        )

        cutoff = datetime.now() - timedelta(days=lookback_days)
        fired: list[str] = []
        now_iso = datetime.now().isoformat()

        # Rule A: high-importance memories with no successful retrieval
        recent_high = (
            Memory
            .select(Memory.id, Memory.importance, Memory.source_session)
            .where(
                Memory.importance >= importance_threshold,
                Memory.created_at >= cutoff,
            )
        )
        unused_ids: list[str] = []
        for row in recent_high:
            used = (
                RetrievalLog
                .select()
                .where(
                    RetrievalLog.memory_id == row.id,
                    RetrievalLog.was_used == 1,
                )
                .exists()
            )
            if not used:
                unused_ids.append(row.id)
        if len(unused_ids) >= unused_count_threshold:
            obs = SelfObservation(
                facts=[
                    f"{len(unused_ids)} memories with importance>="
                    f"{importance_threshold} created in the last "
                    f"{lookback_days} days have zero successful retrievals. "
                    f"Extraction is over-confident on importance — these "
                    f"cards looked load-bearing at write time but the "
                    f"retrieval pipeline never surfaced them as useful."
                ],
                kind="correction",
                importance=0.7,
                proposed_action=(
                    "Tighten Stage 1 importance gate (current default 0.3); "
                    "consider raising the synthesis threshold for what counts "
                    "as a 0.7+ card."
                ),
                evidence={
                    "unused_count": len(unused_ids),
                    "importance_threshold": importance_threshold,
                    "lookback_days": lookback_days,
                    "sample_ids": unused_ids[:5],
                },
                rule_id="cards_unused_high_importance",
                ts=now_iso,
            )
            _append_audit([obs])
            fired.append("cards_unused_high_importance")

        # Rule B: retrieval pressure with low diversity
        recent_injections = (
            RetrievalLog
            .select(RetrievalLog.memory_id, RetrievalLog.was_used)
            .where(
                RetrievalLog.retrieval_type == 'injected',
                RetrievalLog.timestamp >= cutoff,
            )
        )
        rows = list(recent_injections)
        if len(rows) >= 3:
            distinct = {r.memory_id for r in rows}
            any_used = any(r.was_used == 1 for r in rows)
            if not any_used and len(distinct) < 5:
                obs = SelfObservation(
                    facts=[
                        f"{len(rows)} injection events across only "
                        f"{len(distinct)} distinct memories in the last "
                        f"{lookback_days} days, with zero was_used=1. The "
                        f"retrieval layer is re-serving a small pool that "
                        f"isn't matching response context — extraction is "
                        f"likely missing whole topic regions the user is "
                        f"actually working in."
                    ],
                    kind="open_question",
                    importance=0.65,
                    proposed_action=(
                        "Audit the last N user prompts against the "
                        "consolidated memory store; if recurring topics have "
                        "no matching cards, expand session_type coverage in "
                        "OBSERVATION_EXTRACT_PROMPT or revisit synthesis "
                        "clustering."
                    ),
                    evidence={
                        "injection_count": len(rows),
                        "distinct_memories": len(distinct),
                        "lookback_days": lookback_days,
                    },
                    rule_id="extraction_under_retrieval_pressure",
                    ts=now_iso,
                )
                _append_audit([obs])
                fired.append("extraction_under_retrieval_pressure")

        return fired

    def _has_three_consecutive_unused(self, memory_id: str) -> bool:
        """
        Return True if the last 3 injections for this memory all have was_used=0.
        """
        rows = (
            RetrievalLog.select(RetrievalLog.was_used)
            .where(
                RetrievalLog.memory_id == memory_id,
                RetrievalLog.retrieval_type == 'injected',
            )
            .order_by(RetrievalLog.timestamp.desc())
            .limit(3)
        )
        usage_vals = [r.was_used for r in rows]

        if len(usage_vals) < 3:
            return False

        return all(v == 0 for v in usage_vals)


def _record_usage(memory_id: str, session_id: str) -> None:
    """Record that a memory was actively used (not just injected)."""
    now = datetime.now().isoformat()
    Memory.update(
        last_used_at=now,
        usage_count=Memory.usage_count + 1,
    ).where(Memory.id == memory_id).execute()

    # Update most recent retrieval log entry
    subq = (
        RetrievalLog.select(fn.MAX(RetrievalLog.timestamp))
        .where(
            RetrievalLog.memory_id == memory_id,
            RetrievalLog.session_id == session_id,
        )
    )
    RetrievalLog.update(was_used=1).where(
        RetrievalLog.memory_id == memory_id,
        RetrievalLog.session_id == session_id,
        RetrievalLog.timestamp == subq,
    ).execute()
