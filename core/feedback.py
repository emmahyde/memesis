"""
Feedback loop for memory lifecycle: usage tracking, importance scoring,
and promotion/demotion signals.

Implements D-08 (cross-project promotion: 3+ distinct projects) and
D-09 (demotion: injected 10+ times, never used).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .lifecycle import LifecycleManager
from .storage import MemoryStore


class FeedbackLoop:
    """
    Tracks memory usage within sessions and updates importance scores.

    Drives promotion/demotion signals based on usage patterns per D-08 and D-09.
    """

    def __init__(self, store: MemoryStore, lifecycle: LifecycleManager):
        self.store = store
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

    # Usage score threshold — calibrated so 2 title-keyword hits still trigger
    # (3+3 = 6 ≥ 4) while requiring 4+ short content-only hits to trigger.
    _USAGE_THRESHOLD = 4.0

    def track_usage(self, session_id: str, injected_ids: list[str], response_text: str) -> dict:
        """
        Determine which injected memories were actually used in the response.

        Uses content-aware scoring: keywords from title, summary, AND content
        are weighted by source (title > summary > content) and by term
        specificity (longer words carry more weight). This catches domain-specific
        terms in memory content that the old title-only heuristic missed.

        Side effects:
        - Calls store.record_usage() for each memory marked as used.
        - Logs a 'memory_used' event for each used memory.

        Returns:
            {memory_id: was_used (bool)}
        """
        response_lower = response_text.lower()
        usage_map: dict[str, bool] = {}

        for memory_id in injected_ids:
            try:
                memory = self.store.get(memory_id)
            except ValueError:
                usage_map[memory_id] = False
                continue

            score = self._compute_usage_score(
                title=memory.get('title') or '',
                summary=memory.get('summary') or '',
                content=memory.get('content') or '',
                response_lower=response_lower,
            )
            was_used = score >= self._USAGE_THRESHOLD

            usage_map[memory_id] = was_used

            if was_used:
                self.store.record_usage(memory_id, session_id)
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
            source_weight × term_specificity(word)

        Returns:
            Additive score (higher = stronger evidence of usage).
        """
        score = 0.0

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
                    if w in response_lower:
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

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT m.id, m.importance, m.usage_count,
                       COUNT(rl.id) as recent_injections,
                       SUM(rl.was_used) as recent_usages
                FROM memories m
                LEFT JOIN retrieval_log rl ON rl.memory_id = m.id
                    AND rl.retrieval_type = 'injected'
                    AND rl.timestamp >= datetime('now', '-30 days')
                WHERE m.stage != 'ephemeral'
                GROUP BY m.id
            """)
            rows = cursor.fetchall()

        for row in rows:
            memory_id = row['id']
            old_importance = row['importance']
            new_importance = old_importance

            # Rule 1: used this session → bump up
            if session_map.get(memory_id) is True:
                new_importance = min(1.0, old_importance + 0.05)

            # Rule 2: 3+ consecutive injections with no usage → nudge down
            elif self._has_three_consecutive_unused(memory_id):
                new_importance = max(0.1, old_importance - 0.1)

            if new_importance != old_importance:
                self.store.update(memory_id, metadata={'importance': new_importance})
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
        with sqlite3.connect(self.store.db_path) as conn:
            cursor = conn.execute("""
                SELECT memory_id
                FROM retrieval_log
                WHERE retrieval_type = 'injected'
                  AND project_context IS NOT NULL
                GROUP BY memory_id
                HAVING COUNT(DISTINCT project_context) >= 3
            """)
            return [row[0] for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    def log_event(self, event_type: str, data: dict) -> None:
        """
        Append a single JSON event line to meta/retrieval-log.jsonl.

        Format:
            {"event": "<type>", "timestamp": "<iso>", ...data fields}
        """
        log_path = self.store.base_dir / 'meta' / 'retrieval-log.jsonl'
        log_path.parent.mkdir(parents=True, exist_ok=True)

        record = {'event': event_type, 'timestamp': datetime.now().isoformat()}
        record.update(data)

        with log_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record) + '\n')

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(title: str, summary: str) -> list[str]:
        """
        Split title and summary into lowercased words, keeping only len >= 4.
        """
        words = (title + ' ' + summary).lower().split()
        return [w for w in words if len(w) >= 4]

    def _has_three_consecutive_unused(self, memory_id: str) -> bool:
        """
        Return True if the last 3 injections for this memory all have was_used=0.
        """
        with sqlite3.connect(self.store.db_path) as conn:
            cursor = conn.execute("""
                SELECT was_used
                FROM retrieval_log
                WHERE memory_id = ?
                  AND retrieval_type = 'injected'
                ORDER BY timestamp DESC
                LIMIT 3
            """, (memory_id,))
            rows = cursor.fetchall()

        if len(rows) < 3:
            return False

        return all(row[0] == 0 for row in rows)
