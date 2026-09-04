"""Stop placement, targets and risk-based sizing."""

from __future__ import annotations

import pytest

from n500.scoring import plan


class TestStopPlacement:
    def test_a_support_zone_below_is_preferred_over_volatility(self):
        stop, basis, _ = plan.suggest_stop(100.0, 2.0, zone_floor=94.0)
        assert basis == "support zone"
        assert stop == pytest.approx(94.0 - 0.5 * 2.0)

    def test_the_stop_sits_under_the_floor_not_on_it(self):
        stop, _, _ = plan.suggest_stop(100.0, 2.0, zone_floor=94.0)
        assert stop < 94.0, "a stop at the floor is where everyone else's stop is"

    def test_a_swing_low_is_used_when_there_is_no_zone(self):
        stop, basis, _ = plan.suggest_stop(100.0, 2.0, swing_low=95.0)
        assert basis == "swing low"

    def test_volatility_is_the_fallback(self):
        stop, basis, _ = plan.suggest_stop(100.0, 2.0)
        assert basis == "volatility"
        assert stop == pytest.approx(100.0 - 2.5 * 2.0)

    def test_a_zone_above_the_price_is_ignored(self):
        # A resistance band passed in by mistake must not become the stop.
        stop, basis, _ = plan.suggest_stop(100.0, 2.0, zone_floor=110.0)
        assert basis == "volatility"
        assert stop < 100.0

    def test_a_stop_inside_daily_noise_is_widened(self):
        # Zone floor 1% away with a 2% ATR: tempting reward-to-risk, certain
        # stop-out.
        stop, _, note = plan.suggest_stop(100.0, 2.0, zone_floor=99.0)
        assert stop <= 100.0 - plan.MIN_STOP_ATR * 2.0
        assert note and "noise" in note

    def test_an_absurdly_wide_zone_stop_is_capped(self):
        stop, basis, note = plan.suggest_stop(100.0, 2.0, zone_floor=50.0)
        assert stop >= 100.0 * (1 - plan.MAX_STOP_PCT)
        assert basis == "volatility", "a zone 50% away is not a stop, it is a hope"

    def test_zero_volatility_is_rejected_rather_than_dividing_by_it(self):
        with pytest.raises(ValueError):
            plan.suggest_stop(100.0, 0.0)


class TestPlan:
    def test_reward_to_risk_uses_the_resistance_band(self):
        p = plan.build(100.0, 2.0, zone_floor=94.0, resistance=120.0)
        # risk = 100 - 93 = 7; reward = 20.
        assert p.reward_risk == pytest.approx(20.0 / 7.0, rel=1e-3)
        assert p.headroom == pytest.approx(0.20)

    def test_no_resistance_means_no_target_rather_than_a_guess(self):
        p = plan.build(100.0, 2.0, zone_floor=94.0)
        assert p.target is None and p.reward_risk is None

    def test_resistance_below_the_price_is_not_a_target(self):
        p = plan.build(100.0, 2.0, resistance=90.0)
        assert p.target is None

    def test_the_r_multiple_is_measured_against_the_risk_taken_at_entry(self):
        # Entry 90, stop 93... no: stop must be below entry for R to mean
        # anything. Entry 100, now 114, stop 93 -> 14 gained on 7 risked.
        p = plan.build(114.0, 2.0, zone_floor=94.0, entry_price=100.0)
        assert p.r_multiple == pytest.approx(2.0, rel=0.05)

    def test_breakeven_is_suggested_once_the_position_is_up_one_r(self):
        p = plan.build(108.0, 2.0, zone_floor=94.0, entry_price=100.0)
        assert p.move_stop_to == pytest.approx(100.0)

    def test_breakeven_is_not_suggested_too_early(self):
        p = plan.build(102.0, 2.0, zone_floor=94.0, entry_price=100.0)
        assert p.move_stop_to is None

    def test_a_position_underwater_has_a_negative_r_multiple(self):
        p = plan.build(96.0, 2.0, zone_floor=94.0, entry_price=100.0)
        assert p.r_multiple is not None and p.r_multiple < 0


class TestSizing:
    def test_size_falls_as_the_stop_widens(self):
        tight = plan.quantity_for(1_000_000, entry=100.0, stop=95.0)
        wide = plan.quantity_for(1_000_000, entry=100.0, stop=80.0)
        assert tight > wide
        # 1% of 10 lakh is 10,000; risking 5 a share buys 2,000 shares.
        assert tight == 2000
        assert wide == 500

    def test_the_rupee_risk_is_the_same_either_way(self):
        # The whole point: different position sizes, identical money at stake.
        for stop in (95.0, 90.0, 80.0):
            qty = plan.quantity_for(1_000_000, entry=100.0, stop=stop)
            assert abs(qty * (100.0 - stop) - 10_000) <= 100.0

    def test_a_stop_above_the_entry_buys_nothing(self):
        assert plan.quantity_for(1_000_000, entry=100.0, stop=105.0) == 0
