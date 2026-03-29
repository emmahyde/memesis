"""Tests for SM-2 spaced injection scheduling."""

from datetime import datetime, timedelta

import pytest

from core.database import init_db, close_db
from core.models import Memory
from core.spaced import update_sm2_schedule, is_injection_eligible


@pytest.fixture
def store(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path / "memory"
    close_db()


@pytest.fixture
def mem(store):
    return Memory.create(
        stage="crystallized",
        title="Test Memory",
        content="Some content",
        importance=0.7,
        injection_count=5,
        usage_count=2,
        created_at=datetime.now().isoformat(),
    )


class TestSM2Schedule:
    """Test SM-2 interval and ease factor updates."""

    def test_used_increases_interval(self, mem):
        old_interval = mem.injection_interval_days or 1.0
        update_sm2_schedule(mem, was_used=True)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_interval_days > old_interval

    def test_used_increases_ease_factor(self, mem):
        old_ef = mem.injection_ease_factor or 2.5
        update_sm2_schedule(mem, was_used=True)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_ease_factor == pytest.approx(old_ef + 0.1)

    def test_not_used_shrinks_interval(self, mem):
        mem.injection_interval_days = 10.0
        mem.save()
        update_sm2_schedule(mem, was_used=False)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_interval_days == pytest.approx(5.0)

    def test_not_used_decreases_ease_factor(self, mem):
        old_ef = mem.injection_ease_factor or 2.5
        update_sm2_schedule(mem, was_used=False)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_ease_factor < old_ef

    def test_ease_factor_never_below_1_3(self, mem):
        mem.injection_ease_factor = 1.3
        mem.save()
        update_sm2_schedule(mem, was_used=False)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_ease_factor >= 1.3

    def test_interval_never_below_1_day(self, mem):
        mem.injection_interval_days = 0.5
        mem.save()
        update_sm2_schedule(mem, was_used=False)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.injection_interval_days >= 1.0

    def test_sets_next_injection_due(self, mem):
        assert mem.next_injection_due is None
        update_sm2_schedule(mem, was_used=True)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.next_injection_due is not None
        due = datetime.fromisoformat(mem_fresh.next_injection_due)
        assert due > datetime.now()

    def test_flag_disabled_no_update(self, mem, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"sm2_spaced_injection": False})
        update_sm2_schedule(mem, was_used=True)
        mem_fresh = Memory.get_by_id(mem.id)
        assert mem_fresh.next_injection_due is None


class TestInjectionEligibility:
    """Test injection suppression logic."""

    def test_no_due_date_is_eligible(self, mem):
        assert mem.next_injection_due is None
        assert is_injection_eligible(mem) is True

    def test_future_due_date_not_eligible(self, mem):
        mem.next_injection_due = (datetime.now() + timedelta(days=7)).isoformat()
        mem.save()
        assert is_injection_eligible(mem) is False

    def test_past_due_date_is_eligible(self, mem):
        mem.next_injection_due = (datetime.now() - timedelta(days=1)).isoformat()
        mem.save()
        assert is_injection_eligible(mem) is True

    def test_flag_disabled_always_eligible(self, mem, monkeypatch):
        import core.flags
        monkeypatch.setattr(core.flags, "_cache", {"sm2_spaced_injection": False})
        mem.next_injection_due = (datetime.now() + timedelta(days=30)).isoformat()
        mem.save()
        assert is_injection_eligible(mem) is True

    def test_corrupt_date_is_eligible(self, mem):
        mem.next_injection_due = "not-a-date"
        mem.save()
        assert is_injection_eligible(mem) is True
