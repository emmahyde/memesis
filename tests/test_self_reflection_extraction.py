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
