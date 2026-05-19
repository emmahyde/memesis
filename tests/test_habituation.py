"""Tests for the HabituationModel — time-decayed event frequency model."""

import json
import math
import time

import pytest

from core.habituation import HabituationModel


@pytest.fixture(autouse=True)
def _enable_habituation(monkeypatch):
    """Habituation defaults off in flags.py; force enable for these tests."""
    import core.flags
    monkeypatch.setattr(core.flags, "_cache", {"habituation_baseline": True})


def _seed(model: HabituationModel, event_type: str, count: float):
    """Seed an undecayed count with current timestamp."""
    model._state[event_type] = {"count": float(count), "ts": time.time()}


class TestHabituationFactor:
    def test_novel_event_factor_is_1(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model.get_factor("correction") == 1.0

    def test_event_seen_10_times_factor_below_0_5(self, tmp_path):
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 10)
        factor = model.get_factor("test_run")
        assert factor < 0.5
        expected = 1.0 / (1.0 + math.log(10))
        assert abs(factor - expected) < 1e-6

    def test_event_seen_100_times_factor_below_0_22(self, tmp_path):
        model = HabituationModel(tmp_path)
        _seed(model, "file_save", 100)
        assert model.get_factor("file_save") < 0.22

    def test_unknown_event_returns_1(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model.get_factor("never_seen_before") == 1.0

    def test_flag_disabled_returns_1_for_all(self, tmp_path, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"habituation_baseline": False})
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 100)
        assert model.get_factor("test_run") == 1.0


class TestDecay:
    """Counts decay exponentially over wall-clock time."""

    def test_decay_after_half_life(self, tmp_path, monkeypatch):
        # 1s half-life so the test can sleep briefly.
        monkeypatch.setenv("MEMESIS_HABITUATION_HALF_LIFE_SEC", "1")
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 100)
        # Backdate stamp 1 half-life ago.
        model._state["test_run"]["ts"] = time.time() - 1.0
        decayed = model._decayed_count("test_run", time.time())
        assert 45 < decayed < 55  # ~50 after one half-life

    def test_decay_restores_novelty_after_long_silence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEMESIS_HABITUATION_HALF_LIFE_SEC", "1")
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 10_000)
        # 30 half-lives ago: count decays by 2^-30, effectively zero.
        model._state["test_run"]["ts"] = time.time() - 30.0
        assert model.get_factor("test_run") == pytest.approx(1.0, abs=1e-6)


class TestRecordAndPersistence:
    def test_record_event_increments_count(self, tmp_path):
        model = HabituationModel(tmp_path)
        model.record_event("correction")
        model.record_event("correction")
        assert model._state["correction"]["count"] == pytest.approx(2.0, abs=1e-3)

    def test_record_event_persists_to_json(self, tmp_path):
        model = HabituationModel(tmp_path)
        model.record_event("test_run")
        data = json.loads((tmp_path / "habituation.json").read_text())
        assert data["test_run"]["count"] == pytest.approx(1.0)
        assert "ts" in data["test_run"]

    def test_load_migrates_legacy_int_format(self, tmp_path):
        (tmp_path / "habituation.json").write_text(json.dumps({"git_commit": 15}))
        model = HabituationModel(tmp_path)
        assert model._state["git_commit"]["count"] == 15
        assert model.get_factor("git_commit") < 0.5

    def test_missing_json_starts_empty(self, tmp_path):
        model = HabituationModel(tmp_path)
        assert model._state == {}

    def test_corrupt_json_starts_empty(self, tmp_path):
        (tmp_path / "habituation.json").write_text("not json{{{")
        model = HabituationModel(tmp_path)
        assert model._state == {}


class TestExtractEventSignature:
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
    def _make_content(self, *obs_types):
        lines = ["# Session Observations — 2026-03-29\n\n"]
        for i, otype in enumerate(obs_types):
            lines.append(
                f"## [2026-03-29T12:{i:02d}:00] {otype}\nObservation about {otype}\n\n"
            )
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
        # Seed high count so factor << threshold 0.15.
        _seed(model, "test_run", 1_000)
        content = self._make_content("test_run", "correction")
        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 1
        assert "test_run" not in filtered
        assert "correction" in filtered

    def test_filter_preserves_session_header(self, tmp_path):
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 1_000)
        content = self._make_content("test_run")
        filtered, _ = model.filter_observations(content)
        assert "# Session Observations" in filtered

    def test_filter_returns_count(self, tmp_path):
        model = HabituationModel(tmp_path)
        _seed(model, "a", 1_000)
        _seed(model, "b", 1_000)
        content = self._make_content("a", "b", "c")
        _, suppressed = model.filter_observations(content)
        assert suppressed == 2

    def test_filter_disabled_flag_passes_everything(self, tmp_path, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"habituation_baseline": False})
        model = HabituationModel(tmp_path)
        _seed(model, "test_run", 100)
        content = self._make_content("test_run")
        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 0
        assert "test_run" in filtered

    def test_filter_records_events(self, tmp_path):
        model = HabituationModel(tmp_path)
        content = self._make_content("correction", "correction")
        model.filter_observations(content)
        assert model._state["correction"]["count"] == pytest.approx(2.0, abs=1e-3)

    def test_filter_records_after_decisions(self, tmp_path):
        """A high-count batch shouldn't have its first block's record contaminate
        the factor for the second block — record happens after all decisions."""
        model = HabituationModel(tmp_path)
        # Seed just under the threshold; if record_event ran inline, the second
        # occurrence's factor would drop further. Verify both are kept.
        _seed(model, "borderline", 5)  # factor ≈ 0.383 > 0.15
        content = self._make_content("borderline", "borderline")
        _, suppressed = model.filter_observations(content)
        assert suppressed == 0


class TestNeverSuppress:
    """Regression: certain event types must never be suppressed."""

    @pytest.mark.parametrize("event_type", [
        "correction",
        "preference_signal",
        "self_observation",
        "decision_context",
        "shared_insight",
        "untyped",
    ])
    def test_never_suppressed_at_high_count(self, tmp_path, event_type):
        model = HabituationModel(tmp_path)
        _seed(model, event_type, 1_000)
        assert model.get_factor(event_type) == 1.0

    def test_never_suppress_types_pass_through_filter(self, tmp_path):
        model = HabituationModel(tmp_path)
        for t in ("correction", "preference_signal", "untyped"):
            _seed(model, t, 200)

        lines = ["# Session\n\n"]
        for i, t in enumerate(["correction", "preference_signal", "untyped"]):
            lines.append(f"## [2026-03-29T12:{i:02d}:00] {t}\nContent about {t}\n\n")
        content = "".join(lines)

        filtered, suppressed = model.filter_observations(content)
        assert suppressed == 0
        for t in ("correction", "preference_signal"):
            assert t in filtered


class TestObsHeaderRegex:
    """Markdown bullets ('-', '*') must not be misclassified as event types."""

    def test_dash_not_extracted_as_event_type(self, tmp_path):
        model = HabituationModel(tmp_path)
        block = "## [2026-03-29T12:00:00] - this is a bullet point\n- item 1"
        result = model.extract_event_signature(block)
        assert result != "-"
        assert result == "untyped"

    def test_star_not_extracted_as_event_type(self, tmp_path):
        model = HabituationModel(tmp_path)
        block = "## [2026-03-29T12:00:00] * bold item\nContent"
        result = model.extract_event_signature(block)
        assert result != "*"
        assert result == "untyped"

    def test_word_event_type_still_extracted(self, tmp_path):
        model = HabituationModel(tmp_path)
        for etype in (
            "correction", "domain_knowledge", "decision_context", "preference_signal"
        ):
            block = f"## [2026-03-29T12:00:00] {etype}\nContent"
            assert model.extract_event_signature(block) == etype
