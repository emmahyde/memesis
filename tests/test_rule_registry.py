"""
Tests for core/rule_registry.py — Task 2.2 (Wave 2).

Covers:
  - TestNewRuleOverrides: verifies 3 new Wave 1 rule overrides + cards_unused_high_importance
  - TestResolveOverrides: resolve_overrides() defaults and confirmed-rule application
  - TestRegistryStatusCLI: dormant_confirmed and active classification via build_rows()
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.rule_registry import (
    RULE_METADATA,
    RULE_OVERRIDES,
    ParameterOverrides,
    resolve_overrides,
)


# ---------------------------------------------------------------------------
# TestNewRuleOverrides
# ---------------------------------------------------------------------------


class TestNewRuleOverrides:
    def test_monotone_knowledge_lens_override_present(self):
        assert "monotone_knowledge_lens" in RULE_OVERRIDES

    def test_affect_signal_no_extraction_override_present(self):
        assert "affect_signal_no_extraction" in RULE_OVERRIDES

    def test_forced_clustering_low_importance_override_present(self):
        assert "forced_clustering_low_importance" in RULE_OVERRIDES

    def test_cards_unused_high_importance_override_present(self):
        """Pre-wired override (planner pre-condition) must still be intact."""
        assert "cards_unused_high_importance" in RULE_OVERRIDES

    # Spot-check all pre-existing entries still present
    @pytest.mark.parametrize(
        "rule_id",
        [
            "chunking_suboptimal",
            "dedup_inert",
            "synthesis_overgreedy",
            "low_productive_rate",
            "affect_blind_spot",
            "parse_errors_present",
            "cards_unused_high_importance",
        ],
    )
    def test_pre_existing_overrides_preserved(self, rule_id: str):
        assert rule_id in RULE_OVERRIDES

    def test_rule_metadata_present_for_new_rules(self):
        for rule_id in (
            "monotone_knowledge_lens",
            "affect_signal_no_extraction",
            "forced_clustering_low_importance",
        ):
            assert rule_id in RULE_METADATA, f"RULE_METADATA missing entry for {rule_id}"

    def test_new_overrides_are_callable(self):
        dummy_rec: dict = {"fire_count": 5, "confidence": "confirmed"}
        base = ParameterOverrides()
        for rule_id in (
            "monotone_knowledge_lens",
            "affect_signal_no_extraction",
            "forced_clustering_low_importance",
        ):
            fn = RULE_OVERRIDES[rule_id]
            result = fn(dummy_rec, base)
            assert isinstance(result, ParameterOverrides), (
                f"Override fn for {rule_id} did not return ParameterOverrides"
            )


# ---------------------------------------------------------------------------
# TestResolveOverrides
# ---------------------------------------------------------------------------


class TestResolveOverrides:
    def test_empty_audit_returns_defaults(self):
        result = resolve_overrides({})
        defaults = ParameterOverrides()
        # Core fields should be at default values
        assert result.importance_gate == defaults.importance_gate
        assert result.max_windows == defaults.max_windows
        assert result.synthesis_strict == defaults.synthesis_strict
        assert result.affect_pre_filter == defaults.affect_pre_filter

    def test_unconfirmed_rule_has_no_effect(self):
        audit = {
            "synthesis_overgreedy": {
                "fire_count": 2,
                "confidence": "tentative",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.synthesis_strict is False

    def test_confirmed_synthesis_overgreedy_sets_strict(self):
        audit = {
            "synthesis_overgreedy": {
                "fire_count": 5,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.synthesis_strict is True

    def test_confirmed_parse_errors_bumps_tokens(self):
        audit = {
            "parse_errors_present": {
                "fire_count": 4,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.max_tokens_stage1 >= 12288

    def test_confirmed_cards_unused_raises_importance_gate(self):
        audit = {
            "cards_unused_high_importance": {
                "fire_count": 3,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.importance_gate >= 0.45

    def test_confirmed_low_productive_rate_caps_windows(self):
        audit = {
            "low_productive_rate": {
                "fire_count": 6,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.max_windows <= 6
        assert result.affect_pre_filter is True

    def test_notes_accumulate_across_multiple_confirmed_rules(self):
        audit = {
            "synthesis_overgreedy": {
                "fire_count": 3,
                "confidence": "confirmed",
                "latest": {},
            },
            "parse_errors_present": {
                "fire_count": 3,
                "confidence": "confirmed",
                "latest": {},
            },
        }
        result = resolve_overrides(audit)
        assert len(result.notes) >= 2

    def test_forced_clustering_low_importance_sets_synthesis_strict(self):
        audit = {
            "forced_clustering_low_importance": {
                "fire_count": 4,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert result.synthesis_strict is True
        assert any("forced_clustering" in n for n in result.notes)

    def test_monotone_knowledge_lens_confirmed_adds_note(self):
        audit = {
            "monotone_knowledge_lens": {
                "fire_count": 5,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        # Informational stub — should not crash and should add a note
        assert any("monotone_knowledge_lens" in n for n in result.notes)

    def test_affect_signal_no_extraction_confirmed_adds_note(self):
        audit = {
            "affect_signal_no_extraction": {
                "fire_count": 5,
                "confidence": "confirmed",
                "latest": {},
            }
        }
        result = resolve_overrides(audit)
        assert any("affect_signal_no_extraction" in n for n in result.notes)


# ---------------------------------------------------------------------------
# TestRegistryStatusCLI
# ---------------------------------------------------------------------------


class TestRegistryStatusCLI:
    """Tests for scripts/registry_status.py build_rows() classification logic.

    Uses import-and-call pattern (not subprocess) to avoid filesystem dependency
    on audit JSONL files.
    """

    def _make_audit(self, rule_id: str, fire_count: int) -> dict:
        confidence = "confirmed" if fire_count >= 3 else "tentative"
        return {
            rule_id: {
                "fire_count": fire_count,
                "confidence": confidence,
                "latest": {"rule_id": rule_id},
            }
        }

    def test_dormant_confirmed_classification(self, monkeypatch, tmp_path):
        """A confirmed rule with no override entry should be classified dormant_confirmed."""
        from unittest.mock import patch

        fake_audit = self._make_audit("low_obs_yield_per_call", fire_count=5)
        # low_obs_yield_per_call has no entry in RULE_OVERRIDES (confirmed, no knob)
        assert "low_obs_yield_per_call" not in RULE_OVERRIDES

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from scripts.registry_status import build_rows

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value=fake_audit,
        ):
            rows = build_rows(root=tmp_path)

        target = next((r for r in rows if r["rule_id"] == "low_obs_yield_per_call"), None)
        assert target is not None, "low_obs_yield_per_call not in rows"
        assert target["state"] == "dormant_confirmed"
        assert target["confirmed"] is True
        assert target["has_override"] is False

    def test_active_rule_classification(self, monkeypatch, tmp_path):
        """A confirmed rule with an override entry should be classified active."""
        from unittest.mock import patch

        fake_audit = self._make_audit("synthesis_overgreedy", fire_count=5)
        assert "synthesis_overgreedy" in RULE_OVERRIDES

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from scripts.registry_status import build_rows

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value=fake_audit,
        ):
            rows = build_rows(root=tmp_path)

        target = next((r for r in rows if r["rule_id"] == "synthesis_overgreedy"), None)
        assert target is not None
        assert target["state"] == "active"
        assert target["confirmed"] is True
        assert target["has_override"] is True

    def test_dormant_unconfirmed_classification(self, tmp_path):
        """An override with zero fires should be classified dormant_unconfirmed."""
        from unittest.mock import patch

        # monotone_knowledge_lens has an override but zero fires (new rule)
        fake_audit: dict = {}  # nothing fired

        from scripts.registry_status import build_rows

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value=fake_audit,
        ):
            rows = build_rows(root=tmp_path)

        target = next((r for r in rows if r["rule_id"] == "monotone_knowledge_lens"), None)
        assert target is not None
        assert target["state"] == "dormant_unconfirmed"
        assert target["confirmed"] is False
        assert target["has_override"] is True

    def test_rows_sorted_dormant_confirmed_first(self, tmp_path):
        """dormant_confirmed rows must come before active rows in output."""
        from unittest.mock import patch

        # Make low_obs_yield_per_call (no override) confirmed; synthesis_overgreedy (has override) confirmed.
        fake_audit = {
            "low_obs_yield_per_call": {
                "fire_count": 5,
                "confidence": "confirmed",
                "latest": {"rule_id": "low_obs_yield_per_call"},
            },
            "synthesis_overgreedy": {
                "fire_count": 5,
                "confidence": "confirmed",
                "latest": {"rule_id": "synthesis_overgreedy"},
            },
        }

        from scripts.registry_status import build_rows

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value=fake_audit,
        ):
            rows = build_rows(root=tmp_path)

        states = [r["state"] for r in rows]
        # All dormant_confirmed must precede all active entries
        dc_indices = [i for i, s in enumerate(states) if s == "dormant_confirmed"]
        active_indices = [i for i, s in enumerate(states) if s == "active"]
        if dc_indices and active_indices:
            assert max(dc_indices) < min(active_indices), (
                "dormant_confirmed rows should sort before active rows"
            )

    def test_all_list_rules_present_in_output(self, tmp_path):
        """Every rule from list_rules() must appear in build_rows() output."""
        from unittest.mock import patch
        from core.self_reflection_extraction import list_rules
        from scripts.registry_status import build_rows

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value={},
        ):
            rows = build_rows(root=tmp_path)

        output_ids = {r["rule_id"] for r in rows}
        for rule_id in list_rules():
            assert rule_id in output_ids, f"{rule_id} from list_rules() missing in build_rows() output"

    def test_render_table_runs_without_error(self, tmp_path):
        """render_table() should produce a non-empty string given any rows."""
        from unittest.mock import patch
        from scripts.registry_status import build_rows, render_table

        with patch(
            "scripts.registry_status.aggregate_audit",
            return_value={},
        ):
            rows = build_rows(root=tmp_path)

        table = render_table(rows)
        assert isinstance(table, str)
        assert len(table) > 0
        assert "rule_id" in table
