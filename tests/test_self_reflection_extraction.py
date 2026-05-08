"""
Tests for core/self_reflection_extraction.py — Task 2.2 (Wave 2).

Covers:
  - ExtractionRunStats new field/property/to_dict additions
  - TestLowObsYieldRule
  - TestRepeatedFactsHighRule
  - TestConfirmedRuleNoAction
  - list_rules() includes all three new rule_ids
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.self_reflection_extraction import (
    ExtractionRunStats,
    SelfObservation,
    aggregate_audit,
    list_rules,
    reflect_on_extraction,
    self_model_audit_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stats(**kwargs) -> ExtractionRunStats:
    """Return a minimal valid ExtractionRunStats, overriding fields via kwargs."""
    defaults = dict(
        session_id="test-session-0001",
        session_type="code",
        chunking="stride",
        windows=10,
        productive_windows=8,
        raw_observations=20,
        final_observations=15,
        issue_cards=3,
        orphans=1,
        skipped_windows=2,
        parse_errors=0,
        affect_signals_total=4,
        affect_quotes_used=2,
        nontrivial_user_turn_count=6,
        entry_count=80,
        cost_calls=10,
    )
    defaults.update(kwargs)
    return ExtractionRunStats(**defaults)


# ---------------------------------------------------------------------------
# ExtractionRunStats additions
# ---------------------------------------------------------------------------


class TestExtractionRunStatsAdditions:
    def test_obs_per_cost_call_computed(self):
        stats = _make_stats(raw_observations=20, cost_calls=10)
        assert stats.obs_per_cost_call == pytest.approx(2.0)

    def test_obs_per_cost_call_zero_when_no_calls(self):
        stats = _make_stats(cost_calls=0)
        assert stats.obs_per_cost_call == 0.0

    def test_repeated_fact_hashes_default_empty(self):
        stats = _make_stats()
        assert stats.repeated_fact_hashes == []

    def test_repeated_fact_hashes_settable(self):
        hashes = ["abc123", "def456"]
        stats = _make_stats(repeated_fact_hashes=hashes)
        assert stats.repeated_fact_hashes == hashes

    def test_to_dict_includes_obs_per_cost_call(self):
        stats = _make_stats(raw_observations=30, cost_calls=10)
        d = stats.to_dict()
        assert "obs_per_cost_call" in d
        assert d["obs_per_cost_call"] == pytest.approx(3.0)

    def test_to_dict_includes_repeated_fact_hashes(self):
        hashes = ["aaa", "bbb"]
        stats = _make_stats(repeated_fact_hashes=hashes)
        d = stats.to_dict()
        assert "repeated_fact_hashes" in d
        assert d["repeated_fact_hashes"] == hashes


# ---------------------------------------------------------------------------
# TestLowObsYieldRule
# ---------------------------------------------------------------------------


class TestLowObsYieldRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        results = []
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "low_obs_yield_per_call":
                try:
                    result = rule(stats)
                    if result is not None:
                        results.append(result)
                except Exception:
                    pass
        return results[0] if results else None

    def test_fires_when_yield_below_threshold(self):
        # cost_calls=10, raw_observations=15 → obs_per_cost_call=1.5 < 2.0
        stats = _make_stats(cost_calls=10, raw_observations=15)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "low_obs_yield_per_call"
        assert result.importance == pytest.approx(0.65)
        assert result.kind == "finding"

    def test_no_fire_when_yield_acceptable(self):
        # cost_calls=10, raw_observations=25 → obs_per_cost_call=2.5 >= 2.0
        stats = _make_stats(cost_calls=10, raw_observations=25)
        result = self._run_rule(stats)
        assert result is None

    def test_no_fire_when_cost_calls_low(self):
        # cost_calls=4 < 8 → guard condition fires, no observation
        stats = _make_stats(cost_calls=4, raw_observations=5)
        result = self._run_rule(stats)
        assert result is None

    def test_no_fire_at_exact_threshold(self):
        # obs_per_cost_call == exactly 2.0 → not strictly less than
        stats = _make_stats(cost_calls=10, raw_observations=20)
        result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_text(self):
        stats = _make_stats(cost_calls=10, raw_observations=10)
        result = self._run_rule(stats)
        assert result is not None
        assert "max_windows" in result.proposed_action
        assert "affect_pre_filter" in result.proposed_action


# ---------------------------------------------------------------------------
# TestRepeatedFactsHighRule
# ---------------------------------------------------------------------------


class TestRepeatedFactsHighRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "repeated_facts_high":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def test_fires_when_three_hash_collisions(self, memory_store):
        """Seed DB with memories; use actual computed content_hash values in stats."""
        from datetime import datetime
        from core.models import Memory

        now = datetime.now().isoformat()
        contents = ["obs content alpha", "obs content beta", "obs content gamma"]
        created = []
        for c in contents:
            m = Memory.create(
                stage="ephemeral",
                content=c,
                importance=0.5,
                created_at=now,
                updated_at=now,
            )
            created.append(m)

        # Reload to get the content_hash values actually stored
        hashes = [Memory.get_by_id(m.id).content_hash for m in created]
        assert all(h is not None for h in hashes), "content_hash must be set by save()"

        stats = _make_stats(repeated_fact_hashes=hashes)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "repeated_facts_high"
        assert result.importance == pytest.approx(0.7)
        assert result.kind == "finding"

    def test_no_fire_when_fewer_than_three(self, memory_store):
        """Only 2 collisions → rule does not fire."""
        from datetime import datetime
        from core.models import Memory

        now = datetime.now().isoformat()
        contents = ["obs content delta", "obs content epsilon"]
        created = []
        for c in contents:
            m = Memory.create(
                stage="ephemeral",
                content=c,
                importance=0.5,
                created_at=now,
                updated_at=now,
            )
            created.append(m)

        hashes = [Memory.get_by_id(m.id).content_hash for m in created]
        # Pass 3 hashes but only 2 exist in DB (add a non-existent one)
        non_existent = "0000000000000000000000000000ffff"
        stats = _make_stats(repeated_fact_hashes=hashes + [non_existent])
        result = self._run_rule(stats)
        assert result is None

    def test_graceful_when_db_unavailable(self, tmp_path):
        """DB not initialized → returns None without exception."""
        # tmp_path fixture means no init_db called, DB is uninitialized
        # We need to ensure db is NOT connected here; since memory_store
        # fixture isn't used, the default uninitialized db should apply.
        # But if another test initialized the db and didn't close it, we
        # need to close it first.
        try:
            close_db()
        except Exception:
            pass

        stats = _make_stats(repeated_fact_hashes=["hash_a", "hash_b", "hash_c"])
        result = self._run_rule(stats)
        # Should return None gracefully, no exception raised
        assert result is None

    def test_no_fire_when_empty_hashes(self, memory_store):
        """Empty repeated_fact_hashes → rule does not fire (early return)."""
        stats = _make_stats(repeated_fact_hashes=[])
        result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_text(self, memory_store):
        from datetime import datetime
        from core.models import Memory

        now = datetime.now().isoformat()
        contents = ["obs proposed action alpha", "obs proposed action beta", "obs proposed action gamma"]
        created = []
        for c in contents:
            m = Memory.create(
                stage="ephemeral",
                content=c,
                importance=0.5,
                created_at=now,
                updated_at=now,
            )
            created.append(m)

        hashes = [Memory.get_by_id(m.id).content_hash for m in created]
        stats = _make_stats(repeated_fact_hashes=hashes)
        result = self._run_rule(stats)
        assert result is not None
        assert "deduplication" in result.proposed_action
        assert "importance_gate" in result.proposed_action


# ---------------------------------------------------------------------------
# TestConfirmedRuleNoAction
# ---------------------------------------------------------------------------


class TestConfirmedRuleNoAction:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "confirmed_rule_no_action":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def _make_audit_return(self, rule_id: str, fire_count: int, proposed_action: str) -> dict:
        return {
            rule_id: {
                "fire_count": fire_count,
                "confidence": "confirmed" if fire_count >= 3 else "tentative",
                "latest": {
                    "rule_id": rule_id,
                    "proposed_action": proposed_action,
                    "ts": "2026-01-01T00:00:00",
                    "facts": ["some fact"],
                    "kind": "finding",
                },
                "first_seen": "2026-01-01T00:00:00",
                "last_seen": "2026-01-01T00:00:00",
            }
        }

    def test_fires_for_unwired_confirmed_rule(self):
        """rule_id='unknown_rule' not in RULE_OVERRIDES, fire_count=6 → fires."""
        fake_audit = self._make_audit_return("unknown_rule", 6, "do X")

        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=fake_audit,
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is not None
        assert result.rule_id == "confirmed_rule_no_action"
        assert result.importance == pytest.approx(0.75)
        assert result.kind == "open_question"
        assert "unknown_rule" in result.proposed_action

    def test_no_fire_when_rule_is_wired(self):
        """rule_id in RULE_OVERRIDES → does not fire."""
        # 'low_productive_rate' is in RULE_OVERRIDES
        fake_audit = self._make_audit_return("low_productive_rate", 6, "reduce max_windows")

        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=fake_audit,
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is None

    def test_no_fire_when_fire_count_below_threshold(self):
        """fire_count=4 < 5 → does not fire."""
        fake_audit = self._make_audit_return("unknown_rule", 4, "do X")

        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=fake_audit,
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is None

    def test_no_fire_when_no_proposed_action(self):
        """proposed_action is empty string → does not fire."""
        fake_audit = self._make_audit_return("unknown_rule", 6, "")

        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=fake_audit,
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is None

    def test_no_fire_when_audit_empty(self):
        """Empty audit → no qualifying rules → does not fire."""
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value={},
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is None

    def test_fires_names_qualifying_rule_in_facts(self):
        """Facts must name the qualifying rule_id."""
        fake_audit = self._make_audit_return("my_custom_unwired_rule", 10, "some action")

        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=fake_audit,
        ):
            stats = _make_stats()
            result = self._run_rule(stats)

        assert result is not None
        facts_text = " ".join(result.facts)
        assert "my_custom_unwired_rule" in facts_text


# ---------------------------------------------------------------------------
# list_rules() includes new rule_ids
# ---------------------------------------------------------------------------


class TestListRulesIncludes:
    def test_low_obs_yield_per_call_registered(self):
        assert "low_obs_yield_per_call" in list_rules()

    def test_repeated_facts_high_registered(self):
        assert "repeated_facts_high" in list_rules()

    def test_confirmed_rule_no_action_registered(self):
        assert "confirmed_rule_no_action" in list_rules()

    def test_monotone_knowledge_lens_registered(self):
        assert "monotone_knowledge_lens" in list_rules()

    def test_affect_signal_no_extraction_registered(self):
        assert "affect_signal_no_extraction" in list_rules()

    def test_forced_clustering_low_importance_registered(self):
        assert "forced_clustering_low_importance" in list_rules()

    def test_cards_unused_high_importance_registered(self):
        # Wave 3 adds this rule.
        assert "cards_unused_high_importance" in list_rules()


# ---------------------------------------------------------------------------
# TestStatsFieldAdditions (Tier 2 Wave 1 new fields)
# ---------------------------------------------------------------------------


class TestStatsFieldAdditions:
    def test_unique_knowledge_types_emitted_default(self):
        stats = _make_stats()
        assert stats.unique_knowledge_types_emitted == 0

    def test_repeated_facts_count_default(self):
        stats = _make_stats()
        assert stats.repeated_facts_count == 0

    def test_windows_with_affect_signal_but_no_card_default(self):
        stats = _make_stats()
        assert stats.windows_with_affect_signal_but_no_card == 0

    def test_min_card_importance_default(self):
        stats = _make_stats()
        assert stats.min_card_importance == pytest.approx(1.0)

    def test_fields_settable(self):
        stats = _make_stats(
            unique_knowledge_types_emitted=3,
            repeated_facts_count=7,
            windows_with_affect_signal_but_no_card=4,
            min_card_importance=0.25,
        )
        assert stats.unique_knowledge_types_emitted == 3
        assert stats.repeated_facts_count == 7
        assert stats.windows_with_affect_signal_but_no_card == 4
        assert stats.min_card_importance == pytest.approx(0.25)

    def test_to_dict_includes_unique_knowledge_types_emitted(self):
        stats = _make_stats(unique_knowledge_types_emitted=2)
        d = stats.to_dict()
        assert "unique_knowledge_types_emitted" in d
        assert d["unique_knowledge_types_emitted"] == 2

    def test_to_dict_includes_repeated_facts_count(self):
        stats = _make_stats(repeated_facts_count=5)
        d = stats.to_dict()
        assert "repeated_facts_count" in d
        assert d["repeated_facts_count"] == 5

    def test_to_dict_includes_windows_with_affect_signal_but_no_card(self):
        stats = _make_stats(windows_with_affect_signal_but_no_card=3)
        d = stats.to_dict()
        assert "windows_with_affect_signal_but_no_card" in d
        assert d["windows_with_affect_signal_but_no_card"] == 3

    def test_to_dict_includes_min_card_importance(self):
        stats = _make_stats(min_card_importance=0.35)
        d = stats.to_dict()
        assert "min_card_importance" in d
        assert d["min_card_importance"] == pytest.approx(0.35)


# ---------------------------------------------------------------------------
# TestMonotoneKnowledgeLensRule
# ---------------------------------------------------------------------------


class TestMonotoneKnowledgeLensRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "monotone_knowledge_lens":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def test_fires_when_monotone_and_enough_obs(self):
        # unique_knowledge_types_emitted=1, final_observations=5 → fires
        stats = _make_stats(unique_knowledge_types_emitted=1, final_observations=5)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "monotone_knowledge_lens"
        assert result.importance == pytest.approx(0.6)
        assert result.kind == "finding"

    def test_fires_with_larger_obs_count(self):
        stats = _make_stats(unique_knowledge_types_emitted=1, final_observations=20)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "monotone_knowledge_lens"

    def test_no_fire_when_diverse_types(self):
        # unique_knowledge_types_emitted=3 → does not fire
        stats = _make_stats(unique_knowledge_types_emitted=3, final_observations=10)
        result = self._run_rule(stats)
        assert result is None

    def test_no_fire_when_zero_types(self):
        # unique_knowledge_types_emitted=0 ≠ 1 → does not fire
        stats = _make_stats(unique_knowledge_types_emitted=0, final_observations=10)
        result = self._run_rule(stats)
        assert result is None

    def test_no_fire_when_obs_count_too_low(self):
        # unique_knowledge_types_emitted=1 but final_observations=4 < 5 → does not fire
        stats = _make_stats(unique_knowledge_types_emitted=1, final_observations=4)
        result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_text(self):
        stats = _make_stats(unique_knowledge_types_emitted=1, final_observations=8)
        result = self._run_rule(stats)
        assert result is not None
        assert "monotone" in result.proposed_action.lower()
        assert "monothematic" in result.proposed_action.lower()


# ---------------------------------------------------------------------------
# TestAffectSignalNoExtractionRule
# ---------------------------------------------------------------------------


class TestAffectSignalNoExtractionRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "affect_signal_no_extraction":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def test_fires_at_threshold(self):
        # windows_with_affect_signal_but_no_card=3 → fires
        stats = _make_stats(windows_with_affect_signal_but_no_card=3)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "affect_signal_no_extraction"
        assert result.importance == pytest.approx(0.7)
        assert result.kind == "finding"

    def test_fires_above_threshold(self):
        stats = _make_stats(windows_with_affect_signal_but_no_card=7)
        result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "affect_signal_no_extraction"

    def test_no_fire_below_threshold(self):
        # windows_with_affect_signal_but_no_card=2 < 3 → does not fire
        stats = _make_stats(windows_with_affect_signal_but_no_card=2)
        result = self._run_rule(stats)
        assert result is None

    def test_no_fire_at_zero(self):
        stats = _make_stats(windows_with_affect_signal_but_no_card=0)
        result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_text(self):
        stats = _make_stats(windows_with_affect_signal_but_no_card=5)
        result = self._run_rule(stats)
        assert result is not None
        assert "somatic" in result.proposed_action.lower() or "detector" in result.proposed_action.lower()


# ---------------------------------------------------------------------------
# TestForcedClusteringLowImportanceRule
# ---------------------------------------------------------------------------


class TestForcedClusteringLowImportanceRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "forced_clustering_low_importance":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def _confirmed_audit(self) -> dict:
        return {
            "synthesis_overgreedy": {
                "fire_count": 4,
                "confidence": "confirmed",
                "latest": {
                    "rule_id": "synthesis_overgreedy",
                    "proposed_action": "inspect lowest importance card",
                    "ts": "2026-01-01T00:00:00",
                    "facts": ["all obs clustered into cards"],
                    "kind": "open_question",
                },
                "first_seen": "2026-01-01T00:00:00",
                "last_seen": "2026-01-01T00:00:00",
            }
        }

    def test_fires_when_both_conditions_met(self):
        # synthesis_overgreedy confirmed AND min_card_importance=0.25 < 0.4 → fires
        stats = _make_stats(min_card_importance=0.25)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=self._confirmed_audit(),
        ):
            result = self._run_rule(stats)
        assert result is not None
        assert result.rule_id == "forced_clustering_low_importance"
        assert result.importance == pytest.approx(0.7)
        assert result.kind == "finding"

    def test_no_fire_when_only_overgreedy_confirmed(self):
        # synthesis_overgreedy confirmed but min_card_importance=0.5 >= 0.4 → does not fire
        stats = _make_stats(min_card_importance=0.5)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=self._confirmed_audit(),
        ):
            result = self._run_rule(stats)
        assert result is None

    def test_no_fire_when_only_low_importance(self):
        # min_card_importance=0.1 but synthesis_overgreedy not confirmed → does not fire
        stats = _make_stats(min_card_importance=0.1)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value={},
        ):
            result = self._run_rule(stats)
        assert result is None

    def test_no_fire_when_neither_condition(self):
        # Neither condition met → does not fire
        stats = _make_stats(min_card_importance=0.8)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value={},
        ):
            result = self._run_rule(stats)
        assert result is None

    def test_no_fire_at_importance_boundary(self):
        # min_card_importance exactly 0.4 → not strictly less than, does not fire
        stats = _make_stats(min_card_importance=0.4)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=self._confirmed_audit(),
        ):
            result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_text(self):
        stats = _make_stats(min_card_importance=0.15)
        with patch(
            "core.self_reflection_extraction.aggregate_audit",
            return_value=self._confirmed_audit(),
        ):
            result = self._run_rule(stats)
        assert result is not None
        assert "synthesis_strict" in result.proposed_action or "orphan" in result.proposed_action


# ---------------------------------------------------------------------------
# TestIssueCardCollapseEfficientRemoved (Wave 3)
# ---------------------------------------------------------------------------


class TestIssueCardCollapseEfficientRemoved:
    def test_issue_card_collapse_efficient_not_in_list_rules(self):
        assert "issue_card_collapse_efficient" not in list_rules()

    def test_no_rule_object_with_that_id(self):
        from core.self_reflection_extraction import _RULES
        ids = [getattr(r, "__rule_id__", None) for r in _RULES]
        assert "issue_card_collapse_efficient" not in ids


# ---------------------------------------------------------------------------
# TestCardsUnusedHighImportanceRule (Wave 3)
# ---------------------------------------------------------------------------


class TestCardsUnusedHighImportanceRule:
    def _run_rule(self, stats: ExtractionRunStats) -> SelfObservation | None:
        from core.self_reflection_extraction import _RULES
        for rule in _RULES:
            if getattr(rule, "__rule_id__", None) == "cards_unused_high_importance":
                try:
                    return rule(stats)
                except Exception:
                    return None
        return None

    def test_fires_when_three_or_more_unused(self, memory_store):
        """Rule fires when feedback returns ≥3 unused high-importance memory IDs."""
        unused_ids = ["id-a", "id-b", "id-c"]
        with patch(
            "core.feedback.cards_unused_in_subsequent_sessions",
            return_value=unused_ids,
        ):
            stats = _make_stats(session_id="session-abc-123")
            result = self._run_rule(stats)

        assert result is not None
        assert result.rule_id == "cards_unused_high_importance"
        assert result.importance == pytest.approx(0.7)
        assert result.kind == "finding"
        assert "3" in result.facts[0]
        assert "session-abc-123" in result.facts[0]

    def test_no_fire_when_fewer_than_three(self, memory_store):
        """Rule does not fire when feedback returns < 3 unused IDs."""
        with patch(
            "core.feedback.cards_unused_in_subsequent_sessions",
            return_value=["id-x", "id-y"],
        ):
            stats = _make_stats(session_id="session-abc-123")
            result = self._run_rule(stats)

        assert result is None

    def test_no_fire_when_feedback_returns_empty(self, memory_store):
        with patch(
            "core.feedback.cards_unused_in_subsequent_sessions",
            return_value=[],
        ):
            stats = _make_stats(session_id="session-abc-123")
            result = self._run_rule(stats)

        assert result is None

    def test_graceful_when_feedback_raises(self, memory_store):
        """Rule returns None without exception when feedback function raises."""
        with patch(
            "core.feedback.cards_unused_in_subsequent_sessions",
            side_effect=RuntimeError("db error"),
        ):
            stats = _make_stats(session_id="session-abc-123")
            result = self._run_rule(stats)

        assert result is None

    def test_no_fire_when_session_id_empty(self, memory_store):
        """Rule skips when session_id is falsy."""
        stats = _make_stats(session_id="")
        result = self._run_rule(stats)
        assert result is None

    def test_proposed_action_mentions_importance_calibration(self, memory_store):
        with patch(
            "core.feedback.cards_unused_in_subsequent_sessions",
            return_value=["x", "y", "z", "w"],
        ):
            stats = _make_stats(session_id="session-abc-123")
            result = self._run_rule(stats)

        assert result is not None
        assert "importance" in result.proposed_action.lower()
