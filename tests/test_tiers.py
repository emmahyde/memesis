"""Tests for core/tiers.py — tier policy constants module."""

import pytest

from core.tiers import (
    stage_to_tier,
    tier_activation_floor,
    tier_decay_tau_hours,
    tier_ttl,
)


class TestStageToTier:
    def test_instinctive_maps_to_t1(self):
        assert stage_to_tier("instinctive") == "T1"

    def test_crystallized_maps_to_t2(self):
        assert stage_to_tier("crystallized") == "T2"

    def test_consolidated_maps_to_t3(self):
        assert stage_to_tier("consolidated") == "T3"

    def test_ephemeral_maps_to_t4(self):
        assert stage_to_tier("ephemeral") == "T4"

    def test_unknown_stage_maps_to_t4(self):
        assert stage_to_tier("unknown_stage") == "T4"

    def test_archived_stage_maps_to_t4(self):
        # archived is not a live stage; falls through to T4 catch-all
        assert stage_to_tier("archived") == "T4"

    def test_empty_string_maps_to_t4(self):
        assert stage_to_tier("") == "T4"


class TestTierTtl:
    def test_t1_returns_none(self):
        result = tier_ttl("T1")
        assert result is None

    def test_t2_returns_180_days_in_seconds(self):
        assert tier_ttl("T2") == 180 * 86400

    def test_t3_returns_90_days_in_seconds(self):
        assert tier_ttl("T3") == 90 * 86400

    def test_t4_returns_30_days_in_seconds(self):
        assert tier_ttl("T4") == 30 * 86400

    def test_t2_is_integer(self):
        result = tier_ttl("T2")
        assert isinstance(result, int)

    def test_t3_is_integer(self):
        result = tier_ttl("T3")
        assert isinstance(result, int)

    def test_t4_is_integer(self):
        result = tier_ttl("T4")
        assert isinstance(result, int)

    def test_unknown_tier_falls_through_to_t4(self):
        assert tier_ttl("T9") == 30 * 86400

    def test_t1_none_is_explicit(self):
        # Ensure the None return is intentional, not a dict miss
        result = tier_ttl("T1")
        assert result is None
        assert result != 0


class TestTierActivationFloor:
    def test_t1_returns_float(self):
        result = tier_activation_floor("T1")
        assert isinstance(result, float)

    def test_t2_returns_float(self):
        result = tier_activation_floor("T2")
        assert isinstance(result, float)

    def test_t3_returns_float(self):
        result = tier_activation_floor("T3")
        assert isinstance(result, float)

    def test_t4_returns_float(self):
        result = tier_activation_floor("T4")
        assert isinstance(result, float)

    def test_t1_floor_is_low(self):
        # T1/T2 must be low to protect instinctive memories
        assert tier_activation_floor("T1") <= 0.1

    def test_t2_floor_is_low(self):
        assert tier_activation_floor("T2") <= 0.1

    def test_t3_floor_is_higher_than_t1(self):
        assert tier_activation_floor("T3") > tier_activation_floor("T1")

    def test_t4_floor_is_higher_than_t2(self):
        assert tier_activation_floor("T4") > tier_activation_floor("T2")

    def test_t3_and_t4_are_at_least_0_10(self):
        assert tier_activation_floor("T3") >= 0.10
        assert tier_activation_floor("T4") >= 0.10

    def test_all_floors_are_positive(self):
        for tier in ("T1", "T2", "T3", "T4"):
            assert tier_activation_floor(tier) > 0.0

    def test_unknown_tier_falls_through_to_t4(self):
        assert tier_activation_floor("T9") == tier_activation_floor("T4")


class TestTierDecayTauHours:
    def test_t1_returns_720(self):
        assert tier_decay_tau_hours("T1") == 720

    def test_t2_returns_168(self):
        assert tier_decay_tau_hours("T2") == 168

    def test_t3_returns_48(self):
        assert tier_decay_tau_hours("T3") == 48

    def test_t4_returns_12(self):
        assert tier_decay_tau_hours("T4") == 12

    def test_t1_is_integer(self):
        assert isinstance(tier_decay_tau_hours("T1"), int)

    def test_t2_is_integer(self):
        assert isinstance(tier_decay_tau_hours("T2"), int)

    def test_t3_is_integer(self):
        assert isinstance(tier_decay_tau_hours("T3"), int)

    def test_t4_is_integer(self):
        assert isinstance(tier_decay_tau_hours("T4"), int)

    def test_taus_decrease_by_tier(self):
        # Higher tier number = faster decay = lower tau
        assert tier_decay_tau_hours("T1") > tier_decay_tau_hours("T2")
        assert tier_decay_tau_hours("T2") > tier_decay_tau_hours("T3")
        assert tier_decay_tau_hours("T3") > tier_decay_tau_hours("T4")

    def test_unknown_tier_falls_through_to_t4(self):
        assert tier_decay_tau_hours("T9") == 12
