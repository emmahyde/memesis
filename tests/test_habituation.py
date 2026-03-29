"""Tests for the HabituationModel — event frequency tracking and observation filtering."""

import json
import math

import pytest

from core.habituation import HabituationModel


class TestHabituationFactor:
    """Test habituation factor computation."""

    def test_novel_event_factor_is_1(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model.get_factor("correction") == 1.0

    def test_event_seen_10_times_factor_below_0_5(self, tmp_path):
        model = HabituationModel(tmp_path)
        model._counts["test_run"] = 10
        factor = model.get_factor("test_run")
        assert factor < 0.5
        expected = 1.0 / (1.0 + math.log(10))
        assert abs(factor - expected) < 1e-9

    def test_event_seen_100_times_factor_below_0_22(self, tmp_path):
        model = HabituationModel(tmp_path)
        model._counts["file_save"] = 100
        factor = model.get_factor("file_save")
        assert factor < 0.22

    def test_unknown_event_returns_1(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model.get_factor("never_seen_before") == 1.0

    def test_flag_disabled_returns_1_for_all(self, tmp_path, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"habituation_baseline": False})
        model = HabituationModel(tmp_path)
        model._counts["test_run"] = 100
        assert model.get_factor("test_run") == 1.0


class TestRecordAndPersistence:
    """Test event recording and JSON round-trip."""

    def test_record_event_increments_count(self, tmp_path):
        model = HabituationModel(tmp_path)
        model.record_event("correction")
        model.record_event("correction")
        assert model._counts["correction"] == 2

    def test_record_event_persists_to_json(self, tmp_path):
        model = HabituationModel(tmp_path)
        model.record_event("test_run")
        data = json.loads((tmp_path / "habituation.json").read_text())
        assert data["test_run"] == 1

    def test_load_restores_counts(self, tmp_path):
        (tmp_path / "habituation.json").write_text(json.dumps({"git_commit": 15}))
        model = HabituationModel(tmp_path)
        assert model._counts["git_commit"] == 15
        assert model.get_factor("git_commit") < 0.5

    def test_missing_json_starts_empty(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model._counts == {}

    def test_corrupt_json_starts_empty(self, tmp_path):
        (tmp_path / "habituation.json").write_text("not json{{{")
        model = HabituationModel(tmp_path)
        assert model._counts == {}


class TestExtractEventSignature:
    """Test observation header parsing."""

    def test_parses_correction(self, tmp_path):
        model = HabituationModel(tmp_path)
        block = "## [2026-03-29T12:00:00] correction\nSome correction text"
        assert model.extract_event_signature(block) == "correction"

    def test_parses_domain_knowledge(self, tmp_path):
        model = HabituationModel(tmp_path)
        block = "## [2026-03-29T12:00:00] domain_knowledge\nSome knowledge"
        assert model.extract_event_signature(block) == "domain_knowledge"

    def test_untyped_for_no_header(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model.extract_event_signature("Just some text") == "untyped"

    def test_normalizes_to_lowercase(self, tmp_path):
        model = HabituationModel(tmp_path)
        block = "## [2026-03-29T12:00:00] CORRECTION\nText"
        assert model.extract_event_signature(block) == "correction"


class TestFilterObservations:
    """Test observation block filtering."""

    def _make_content(self, *obs_types, counts=None):
        """Build ephemeral content with observation blocks."""
        lines = ["# Session Observations — 2026-03-29\n\n"]
        for i, otype in enumerate(obs_types):
            lines.append(f"## [2026-03-29T12:{i:02d}:00] {otype}\nObservation about {otype}\n\n")
        return "".join(lines)

    def test_filter_keeps_novel_observations(self, tmp_path):
        model = HabituationModel(tmp_path)
        content = self._make_content("correction", "preference_signal")
        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 0
        assert "correction" in filtered
        assert "preference_signal" in filtered

    def test_filter_removes_routine_observations(self, tmp_path):
        model = HabituationModel(tmp_path)
        # Pre-seed high counts so the factor is below threshold
        model._counts["test_run"] = 50
        model._save()
        content = self._make_content("test_run", "correction")
        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 1
        assert "test_run" not in filtered
        assert "correction" in filtered

    def test_filter_preserves_session_header(self, tmp_path):
        model = HabituationModel(tmp_path)
        model._counts["test_run"] = 50
        content = self._make_content("test_run")
        filtered, _ = model.filter_observations(content)
        assert "# Session Observations" in filtered

    def test_filter_returns_count(self, tmp_path):
        model = HabituationModel(tmp_path)
        model._counts["a"] = 50
        model._counts["b"] = 50
        content = self._make_content("a", "b", "c")
        _, suppressed = model.filter_observations(content)
        assert suppressed == 2

    def test_filter_disabled_flag_passes_everything(self, tmp_path, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"habituation_baseline": False})
        model = HabituationModel(tmp_path)
        model._counts["test_run"] = 100
        content = self._make_content("test_run")
        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 0
        assert "test_run" in filtered

    def test_filter_records_events(self, tmp_path):
        model = HabituationModel(tmp_path)
        content = self._make_content("correction", "correction")
        model.filter_observations(content)
        assert model._counts["correction"] == 2
