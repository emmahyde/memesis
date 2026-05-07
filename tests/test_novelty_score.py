"""Tests for the continuous novelty score in RelevanceEngine."""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.database import init_db, close_db, get_base_dir
from core.habituation import HabituationModel
from core.models import Memory, db
from core.relevance import RelevanceEngine


@pytest.fixture(autouse=True)
def _disable_formula_flags(monkeypatch):
    """Disable saturation_decay and integration_factor for isolation."""
    import core.flags
    monkeypatch.setattr(core.flags, "_cache", {
        "saturation_decay": False,
        "integration_factor": False,
        "continuous_novelty": True,
    })


@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


@pytest.fixture
def engine(base):
    return RelevanceEngine()


def _create_memory(title, stage="consolidated", importance=0.5,
                   days_ago=0, usage_count=0, injection_count=0,
                   project_context=None, next_injection_due=None,
                   injection_interval_days=None, last_injected_at=None):
    now = datetime.now()
    past = (now - timedelta(days=days_ago)).isoformat()

    mem = Memory.create(
        stage=stage,
        title=title,
        summary=f"Summary of {title}",
        content=f"Content about {title}",
        tags=json.dumps([title.split()[0].lower()]),
        importance=importance,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )

    updates = {"updated_at": past, "created_at": past}
    if days_ago > 0:
        updates["last_injected_at"] = past
        if usage_count > 0:
            updates["last_used_at"] = past
    if usage_count > 0:
        updates["usage_count"] = usage_count
    if injection_count > 0:
        updates["injection_count"] = injection_count
    if project_context:
        updates["project_context"] = project_context
    if next_injection_due is not None:
        updates["next_injection_due"] = next_injection_due
    if injection_interval_days is not None:
        updates["injection_interval_days"] = injection_interval_days
    if last_injected_at is not None:
        updates["last_injected_at"] = last_injected_at

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [mem.id]
    db.execute_sql(f"UPDATE memories SET {set_clause} WHERE id = ?", values)

    return mem.id


# ---------------------------------------------------------------------------
# SM-2 component
# ---------------------------------------------------------------------------

class TestSM2Component:
    """Test SM-2 interval-based novelty component."""

    def test_no_due_date_returns_1(self, engine, base):
        mid = _create_memory("No Due Date")
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(1.0)

    def test_future_due_date_returns_0(self, engine, base):
        future_due = (datetime.now() + timedelta(days=7)).isoformat()
        mid = _create_memory("Future Due", next_injection_due=future_due,
                             injection_interval_days=7.0)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(2.0 / 3.0)

    def test_past_due_interpolates(self, engine, base):
        now = datetime.now()
        due = now - timedelta(days=3)
        mid = _create_memory("Past Due", next_injection_due=due.isoformat(),
                             injection_interval_days=6.0)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # sm2 = 3/6 = 0.5, habituation = 1.0, recency = 1.0 (no last_injected_at)
        expected = (0.5 + 1.0 + 1.0) / 3.0
        assert score == pytest.approx(expected)

    def test_past_due_clamps_at_1(self, engine, base):
        now = datetime.now()
        due = now - timedelta(days=20)
        mid = _create_memory("Way Past Due", next_injection_due=due.isoformat(),
                             injection_interval_days=5.0)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # sm2 = min(1.0, 20/5) = 1.0
        expected = (1.0 + 1.0 + 1.0) / 3.0
        assert score == pytest.approx(expected)

    def test_exactly_at_due_returns_0_sm2(self, engine, base):
        now = datetime.now()
        due = now
        mid = _create_memory("Exactly Due", next_injection_due=due.isoformat(),
                             injection_interval_days=5.0)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # sm2 = 0/5 = 0.0
        expected = (0.0 + 1.0 + 1.0) / 3.0
        assert score == pytest.approx(expected)

    def test_corrupt_due_date_defaults_to_1(self, engine, base):
        mid = _create_memory("Corrupt Due", next_injection_due="not-a-date")
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Habituation component
# ---------------------------------------------------------------------------

class TestHabituationComponent:
    """Test habituation-based novelty component."""

    def test_novel_event_habituation_is_1(self, engine, base):
        mid = _create_memory("Novel Event")
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # habituation should be 1.0 for novel event
        assert score == pytest.approx(1.0)

    def test_habituated_event_reduces_score(self, engine, base, tmp_path):
        mid = _create_memory("Habituated Event")
        memory = Memory.get_by_id(mid)

        # Seed habituation counts for this memory's title
        base_dir = get_base_dir()
        if base_dir is not None:
            model = HabituationModel(base_dir)
            model._counts["habituated event"] = 50
            model._save()

        score = engine._compute_novelty_score(memory)
        # habituation for count=50: 1/(1+ln(50)) ≈ 0.24
        habituation = 1.0 / (1.0 + math.log(50))
        expected = (1.0 + habituation + 1.0) / 3.0
        assert score == pytest.approx(expected, abs=0.01)
        assert score < 1.0

    def test_untyped_fallback_when_no_title(self, engine, base):
        mem = Memory.create(
            stage="consolidated",
            title=None,
            content="Some content",
            importance=0.5,
        )
        score = engine._compute_novelty_score(mem)
        # title is None → event_key="untyped" → habituation=1.0
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Recency component
# ---------------------------------------------------------------------------

class TestRecencyComponent:
    """Test recency-based novelty component with 7-day half-life."""

    def test_no_last_injected_defaults_to_1(self, engine, base):
        mid = _create_memory("Never Injected")
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(1.0)

    def test_fresh_injection_high_recency(self, engine, base):
        now = datetime.now()
        last_injected = (now - timedelta(hours=1)).isoformat()
        mid = _create_memory("Fresh Injection", last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # recency ≈ 0.5^(1/168) ≈ 0.996
        recency = 0.5 ** ((1.0 / 24.0) / 7.0)
        expected = (1.0 + 1.0 + recency) / 3.0
        assert score == pytest.approx(expected, abs=0.01)
        assert score > 0.99

    def test_week_old_injection_half_recency(self, engine, base):
        now = datetime.now()
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("Week Old", last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # recency = 0.5^(7/7) = 0.5
        expected = (1.0 + 1.0 + 0.5) / 3.0
        assert score == pytest.approx(expected)

    def test_two_weeks_old_quarter_recency(self, engine, base):
        now = datetime.now()
        last_injected = (now - timedelta(days=14)).isoformat()
        mid = _create_memory("Two Weeks Old", last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # recency = 0.5^(14/7) = 0.25
        expected = (1.0 + 1.0 + 0.25) / 3.0
        assert score == pytest.approx(expected)

    def test_corrupt_last_injected_defaults_to_1(self, engine, base):
        mid = _create_memory("Corrupt Injected", last_injected_at="not-a-date")
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Additive composition
# ---------------------------------------------------------------------------

class TestAdditiveComposition:
    """Test that components are composed additively, not multiplicatively."""

    def test_all_components_blend_additively(self, engine, base):
        now = datetime.now()
        due = now - timedelta(days=3)
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("All Components",
                             next_injection_due=due.isoformat(),
                             injection_interval_days=6.0,
                             last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # sm2 = 3/6 = 0.5, habituation = 1.0, recency = 0.5
        expected = (0.5 + 1.0 + 0.5) / 3.0
        assert score == pytest.approx(expected)

    def test_not_multiplicative(self, engine, base):
        now = datetime.now()
        due = now - timedelta(days=3)
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("Not Multiplicative",
                             next_injection_due=due.isoformat(),
                             injection_interval_days=6.0,
                             last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        # If multiplicative: 0.5 * 1.0 * 0.5 = 0.25
        # Additive: (0.5 + 1.0 + 0.5) / 3 = 0.667
        assert score == pytest.approx(0.667, abs=0.01)
        assert score > 0.25


# ---------------------------------------------------------------------------
# Feature flag gate
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    """Test that the feature flag gates the novelty score."""

    def test_flag_disabled_returns_1(self, engine, base, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"continuous_novelty": False})

        now = datetime.now()
        due = now - timedelta(days=3)
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("Flag Off",
                             next_injection_due=due.isoformat(),
                             injection_interval_days=6.0,
                             last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score == pytest.approx(1.0)

    def test_flag_enabled_computes_score(self, engine, base, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"continuous_novelty": True})

        now = datetime.now()
        due = now - timedelta(days=3)
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("Flag On",
                             next_injection_due=due.isoformat(),
                             injection_interval_days=6.0,
                             last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)
        score = engine._compute_novelty_score(memory)
        assert score < 1.0


# ---------------------------------------------------------------------------
# Integration with compute_relevance
# ---------------------------------------------------------------------------

class TestIntegration:
    """Test novelty score wired into compute_relevance as multiplicative factor."""

    def test_novelty_multiplies_relevance(self, engine, base):
        now = datetime.now()
        due = now - timedelta(days=3)
        last_injected = (now - timedelta(days=7)).isoformat()
        mid = _create_memory("Integrated",
                             importance=0.5,
                             next_injection_due=due.isoformat(),
                             injection_interval_days=6.0,
                             last_injected_at=last_injected)
        memory = Memory.get_by_id(mid)

        relevance_with_novelty = engine.compute_relevance(memory)

        # Compute expected: base relevance * novelty_score
        novelty = engine._compute_novelty_score(memory, now)
        # Base relevance without novelty (approximate check)
        assert relevance_with_novelty > 0.0
        assert relevance_with_novelty <= 1.0
        # The novelty score should have reduced the relevance
        assert novelty < 1.0

    def test_no_due_date_no_penalty(self, engine, base):
        mid = _create_memory("No Penalty", importance=0.5)
        memory = Memory.get_by_id(mid)
        score = engine.compute_relevance(memory)
        novelty = engine._compute_novelty_score(memory)
        assert novelty == pytest.approx(1.0)
        # Relevance should be unchanged by novelty
        assert score > 0.0

    def test_dict_memory_support(self, engine, base):
        """Novelty score works with dict memories (not just model instances)."""
        now = datetime.now()
        due = now - timedelta(days=3)
        memory_dict = {
            "importance": 0.5,
            "next_injection_due": due.isoformat(),
            "injection_interval_days": 6.0,
            "last_injected_at": (now - timedelta(days=7)).isoformat(),
            "title": "Dict Memory",
        }
        score = engine._compute_novelty_score(memory_dict, now)
        expected = (0.5 + 1.0 + 0.5) / 3.0
        assert score == pytest.approx(expected)
