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
        # Stops chosen so the position cap does not bind — a 5% stop used to be
        # the example here and now gets capped, which is the point of the cap
        # rather than a problem with it.
        tight = plan.quantity_for(1_000_000, entry=100.0, stop=85.0)
        wide = plan.quantity_for(1_000_000, entry=100.0, stop=75.0)
        assert tight > wide
        # 1% of 10 lakh is 10,000; risking 15 a share buys 666 shares.
        assert tight == 666
        assert wide == 400

    def test_the_rupee_risk_is_the_same_either_way(self):
        # The whole point: different position sizes, identical money at stake —
        # for as long as the position cap leaves room for a full unit.
        for stop in (85.0, 80.0, 75.0):
            qty = plan.quantity_for(1_000_000, entry=100.0, stop=stop)
            assert abs(qty * (100.0 - stop) - 10_000) <= 100.0

    def test_a_stop_above_the_entry_buys_nothing(self):
        assert plan.quantity_for(1_000_000, entry=100.0, stop=105.0) == 0

    def test_a_tight_stop_is_capped_by_position_size(self):
        """The failure mode that only shows up on a ranked list.

        A stop 1% away wants 100% of the account for one risk unit. Risk-unit
        sizing says yes; the cap says 10%, and it is the cap that is right —
        the stop is not the only way to lose, and a gap straight through it
        does not care how the position was sized.
        """
        shares = plan.quantity_for(1_000_000, entry=100.0, stop=99.0)
        assert shares * 100.0 == pytest.approx(1_000_000 * plan.MAX_POSITION_PCT)

    def test_the_cap_binds_hardest_where_the_signal_is_strongest(self):
        # A stock pressed against a level has a tight stop by construction, so
        # exactly the names the model likes are the ones naive sizing oversizes.
        tight = plan.quantity_for(1_000_000, entry=100.0, stop=99.0) * 100.0
        loose = plan.quantity_for(1_000_000, entry=100.0, stop=80.0) * 100.0
        assert tight == pytest.approx(100_000)      # capped
        assert loose == pytest.approx(50_000)       # risk-limited, well inside

    def test_risk_taken_falls_below_a_unit_when_capped(self):
        shares = plan.quantity_for(1_000_000, entry=100.0, stop=99.0)
        assert shares * (100.0 - 99.0) < 10_000, "a capped position risks less than a unit"

    def test_the_cap_can_be_relaxed_deliberately(self):
        wide = plan.quantity_for(1_000_000, entry=100.0, stop=99.0, max_position_pct=1.0)
        assert wide * (100.0 - 99.0) == pytest.approx(10_000)

    def test_a_zero_entry_price_buys_nothing_rather_than_dividing_by_it(self):
        assert plan.quantity_for(1_000_000, entry=0.0, stop=-1.0) == 0
