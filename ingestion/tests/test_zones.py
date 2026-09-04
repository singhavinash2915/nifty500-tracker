"""Zone construction, reversal confirmation and the T-S gates."""

import warnings

import numpy as np
import pandas as pd
import pytest

from n500 import indicators as ind
from n500.jobs.compute_zones import to_weekly
from n500.scoring import support
from n500.zones import reversal
from n500.zones.build import (
    Zone,
    ZoneEvent,
    ZoneSource,
    build_zones,
    cluster_supports,
    live_zones_below,
    rate_strength,
    volume_shelves,
    zone_from_cluster,
)
from n500.zones.pivots import Pivot, PivotKind, fractal_highs, fractal_lows


def ohlc(closes, *, highs=None, lows=None, volume=1000.0):
    n = len(closes)
    dates = pd.bdate_range("2024-01-01", periods=n)
    closes = np.asarray(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01 if highs is None else np.asarray(highs, dtype="float64"),
            "low": closes * 0.99 if lows is None else np.asarray(lows, dtype="float64"),
            "close": closes,
            "volume": np.full(n, volume),
        },
        index=dates,
    )


def atr_of(frame):
    return ind.atr(frame["high"], frame["low"], frame["close"], 14)


# --- fractals -------------------------------------------------------------


def test_fractal_low_needs_higher_lows_on_both_sides():
    closes = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10]
    lows = fractal_lows(ohlc(closes), span=3)
    assert len(lows) == 1
    assert lows[0].index == 5
    assert lows[0].price == pytest.approx(5 * 0.99)


def test_a_fractal_is_confirmed_span_bars_later():
    lows = fractal_lows(ohlc([10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10]), span=3)
    pivot = lows[0]
    assert pivot.confirmed_index == pivot.index + 3


def test_fractal_highs_mirror_fractal_lows():
    highs = fractal_highs(ohlc([5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5]), span=3)
    assert len(highs) == 1
    assert highs[0].kind is PivotKind.SPH


def test_a_monotonic_series_has_no_interior_fractals():
    assert fractal_lows(ohlc(list(range(30))), span=3) == []


# --- clustering -----------------------------------------------------------


def pivot(index, price):
    return Pivot(PivotKind.SPL, index, price, index + 1,
                 pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"))


def test_nearby_lows_form_one_zone_and_distant_ones_do_not():
    frame = ohlc(np.full(300, 100.0))
    atr = pd.Series(np.full(300, 2.0), index=frame.index)
    clusters = cluster_supports(
        [pivot(50, 100.0), pivot(80, 100.8), pivot(120, 140.0)], atr
    )
    assert [len(c) for c in clusters] == [2, 1]


def test_clustering_tolerance_scales_with_volatility():
    """A 1.2-point gap is one zone for a stock with ATR 4 and two for ATR 1 —
    which is why the tolerance is in ATR, not percent."""
    frame = ohlc(np.full(300, 100.0))
    pivots = [pivot(50, 100.0), pivot(80, 101.2)]

    calm = cluster_supports(pivots, pd.Series(np.full(300, 1.0), index=frame.index))
    wild = cluster_supports(pivots, pd.Series(np.full(300, 4.0), index=frame.index))
    assert len(calm) == 2
    assert len(wild) == 1


def test_a_single_pivot_still_gets_width():
    """Support is a region. A zero-width zone can never be 'touched'."""
    frame = ohlc(np.full(300, 100.0))
    atr = pd.Series(np.full(300, 2.0), index=frame.index)
    zone = zone_from_cluster([pivot(50, 100.0)], atr)
    assert zone is not None
    assert zone.width > 0


def test_zone_forms_when_its_last_pivot_confirms():
    frame = ohlc(np.full(300, 100.0))
    atr = pd.Series(np.full(300, 2.0), index=frame.index)
    zone = zone_from_cluster([pivot(50, 100.0), pivot(200, 100.4)], atr)
    assert zone.formed_index == 201


# --- volume shelves -------------------------------------------------------


def test_volume_shelves_are_restricted_to_below_price():
    """A high-volume node above price is supply waiting to be sold into —
    resistance, not support. The filter is on the floor, because the shelf a
    stock is currently sitting on straddles the last close and is the support
    case we most want."""
    closes = np.concatenate([np.full(200, 100.0), np.full(100, 60.0)])
    frame = ohlc(closes)
    shelves = volume_shelves(frame, below=60.0)
    assert shelves, "expected at least one shelf below price"
    assert all(floor < 60.0 for floor, _, _ in shelves)
    # The heavy 100.0 shelf is where most volume traded, and it must not appear.
    assert not any(floor > 60.0 for floor, _, _ in shelves)


def test_unfiltered_shelves_can_sit_above_price():
    closes = np.concatenate([np.full(200, 100.0), np.full(100, 60.0)])
    shelves = volume_shelves(ohlc(closes))
    assert any(floor > 60.0 for floor, _, _ in shelves)


# --- strength -------------------------------------------------------------


def make_zone(**kw):
    defaults = dict(
        timeframe="daily", source=ZoneSource.PIVOT, floor=99.0, ceil=101.0,
        formed_index=10, formed_date=pd.Timestamp("2024-01-10"),
    )
    defaults.update(kw)
    return Zone(**defaults)


def test_three_touches_beat_one_and_beat_nine():
    from n500.zones.build import ZoneEvent

    def zone_with(n):
        z = make_zone()
        z.events = [
            ZoneEvent(20 + i * 6, pd.Timestamp("2024-02-01"), "rejection", reaction_atr=2.0)
            for i in range(n)
        ]
        return rate_strength(z, at_index=200, timeframe="daily")

    assert zone_with(3) > zone_with(1)
    assert zone_with(3) > zone_with(9)


def test_weekly_zones_rate_above_the_same_daily_zone():
    daily = rate_strength(make_zone(), at_index=200, timeframe="daily")
    weekly = rate_strength(make_zone(timeframe="weekly"), at_index=200, timeframe="weekly")
    assert weekly > daily


# --- liveness -------------------------------------------------------------


def test_a_broken_zone_stops_being_a_candidate():
    zone = make_zone()
    zone.invalidated_index = 50
    assert zone.is_live(40)
    assert not zone.is_live(60)
    assert live_zones_below([zone], 100.0, at_index=60) == []


def test_zones_above_price_are_not_support():
    zone = make_zone(floor=150.0, ceil=155.0)
    assert live_zones_below([zone], 100.0, at_index=60) == []


def test_a_zone_containing_price_counts_as_support():
    zone = make_zone(floor=99.0, ceil=101.0)
    assert live_zones_below([zone], 100.0, at_index=60) == [zone]


# --- reversal confirmation ------------------------------------------------


def test_hammer_at_the_zone_is_a_bullish_candle():
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.0],
            "high": [101.0, 100.5],
            "low": [99.0, 94.0],
            "close": [100.0, 99.8],
        },
        index=pd.bdate_range("2024-01-01", periods=2),
    )
    assert reversal.bullish_candle(frame, 1, floor=94.0, ceil=97.0)


def test_a_candle_that_never_reached_the_zone_is_not_confirmation():
    frame = pd.DataFrame(
        {"open": [100.0, 100.0], "high": [101.0, 102.0],
         "low": [99.0, 99.5], "close": [100.0, 101.5]},
        index=pd.bdate_range("2024-01-01", periods=2),
    )
    assert not reversal.bullish_candle(frame, 1, floor=80.0, ceil=85.0)


def test_bullish_rsi_divergence_needs_a_lower_low_with_a_higher_rsi():
    n = 60
    lows = np.linspace(100, 80, n)
    frame = ohlc(lows, lows=lows)
    rising_rsi = pd.Series(np.linspace(20, 45, n), index=frame.index)
    falling_rsi = pd.Series(np.linspace(45, 20, n), index=frame.index)

    assert reversal.rsi_divergence(frame, rising_rsi, n - 1, timeframe="daily")
    assert not reversal.rsi_divergence(frame, falling_rsi, n - 1, timeframe="daily")


def test_macd_turning_up_requires_a_rising_negative_histogram():
    rising = pd.Series([-3.0, -2.0, -1.0])
    falling = pd.Series([-1.0, -2.0, -3.0])
    positive = pd.Series([1.0, 2.0, 3.0])
    assert reversal.macd_turning_up(rising, 2)
    assert not reversal.macd_turning_up(falling, 2)
    assert not reversal.macd_turning_up(positive, 2)


def test_confirmation_flags_are_native_bools():
    """A numpy bool reaches JSONB as the string "False", which is truthy — so
    the UI would display a confirmation that never fired."""
    n = 120
    frame = ohlc(np.linspace(120, 100, n))
    conf = reversal.confirm(
        frame,
        index=n - 1,
        floor=99.0,
        ceil=101.0,
        rsi=ind.rsi(frame["close"], 14),
        macd_hist=ind.macd_histogram(frame["close"]),
        sma20=ind.sma(frame["close"], 20),
        timeframe="daily",
    )
    for name, value in conf.as_dict().items():
        assert type(value) is bool, f"{name} is {type(value)}, not bool"


def test_one_signal_is_enough_to_trigger():
    assert reversal.Confirmation(rsi_divergence=True).triggered
    assert not reversal.Confirmation().triggered


# --- structure vetoes -----------------------------------------------------


def test_lower_highs_detected_in_a_downtrend_but_not_an_uptrend():
    down = ohlc(np.linspace(200, 100, 80))
    up = ohlc(np.linspace(100, 200, 80))
    assert reversal.making_lower_highs(down, 79)
    assert not reversal.making_lower_highs(up, 79)


def test_falling_knife_on_consecutive_down_bars():
    frame = ohlc([100, 98, 95, 90, 85])
    atr = pd.Series(np.full(5, 2.0), index=frame.index)
    assert reversal.falling_knife(frame, atr, 4)


def test_an_inside_bar_counts_as_stabilisation():
    frame = pd.DataFrame(
        {"open": [100.0, 99.0], "high": [105.0, 103.0],
         "low": [95.0, 97.0], "close": [96.0, 100.0]},
        index=pd.bdate_range("2024-01-01", periods=2),
    )
    assert reversal.stabilised(frame, 1)


# --- weekly resampling ----------------------------------------------------


def test_weekly_bars_never_lead_the_daily_series():
    """Pandas labels a week by its Friday even when the week is still running,
    which dated a partial bar two days past the last real session."""
    daily = ohlc(np.full(300, 100.0))
    daily = daily.iloc[:-2]        # end mid-week
    weekly = to_weekly(daily)
    assert weekly.index[-1] <= daily.index[-1]


def test_weekly_aggregation_uses_the_extremes_of_the_week():
    daily = ohlc([100, 110, 90, 105, 102])
    weekly = to_weekly(daily)
    assert weekly["high"].iloc[0] == pytest.approx(110 * 1.01)
    assert weekly["low"].iloc[0] == pytest.approx(90 * 0.99)


# --- T-S gates ------------------------------------------------------------


def scenario(closes, **kw):
    frame = ohlc(closes)
    atr = atr_of(frame)
    zones = build_zones(frame, atr, timeframe="daily")
    index = len(frame) - 1
    price = float(frame["close"].iloc[index])
    defaults = dict(
        frame=frame, index=index, price=price, atr=atr, zones=zones,
        weekly_zones=[], pivots=[], confirmation=reversal.Confirmation(),
        quality_gate=False,
    )
    defaults.update(kw)
    return support.evaluate(**defaults)


def test_the_quality_gate_blocks_scoring_when_quality_is_low():
    setup = scenario(np.full(300, 100.0), quality_gate=True, quality_score=40.0)
    assert setup.score is None
    assert "below the 60 gate" in setup.reason


def test_the_quality_gate_blocks_scoring_when_quality_is_missing():
    """Phase 4 has not run, so nothing may pretend to have passed the check."""
    setup = scenario(np.full(300, 100.0), quality_gate=True, quality_score=None)
    assert setup.score is None
    assert "not yet available" in setup.reason


def test_without_confirmation_the_setup_is_capped_at_watching():
    n = 300
    closes = np.concatenate([np.linspace(100, 80, 150), np.linspace(80, 100, 100), np.full(50, 82.0)])
    setup = scenario(closes)
    if setup.score is not None:
        assert setup.status == "watching"
        assert setup.score <= support.WATCHING_CAP


def test_a_stop_is_never_closer_than_the_minimum():
    """A stop 2% away produces a headline 23:1 reward-to-risk that ordinary
    noise stops out within days."""
    n = 300
    closes = np.concatenate([np.linspace(120, 100, 200), np.full(100, 100.0)])
    setup = scenario(closes, confirmation=reversal.Confirmation(rsi_divergence=True))
    if setup.score is not None and setup.stop is not None:
        atr = float(atr_of(ohlc(closes)).iloc[-1])
        assert setup.stop <= 100.0 - support.MIN_STOP_ATR * atr + 1e-6


def test_proximity_scores_full_inside_the_zone_and_zero_far_above():
    assert support._proximity_score(0.0) == 100.0
    assert support._proximity_score(support.PROXIMITY_ZERO_ATR) == 0.0
    assert 0 < support._proximity_score(1.0) < 100


def test_reward_risk_below_the_floor_scores_nothing():
    assert support._reward_risk_score(2.0) == 0.0
    assert support._reward_risk_score(None) == 0.0
    assert support._reward_risk_score(support.FULL_REWARD_RISK) == 100.0


def test_clustering_does_not_chain_into_an_unbounded_band():
    """Single-linkage chaining walked a zone 16% up the chart on UltraTech.
    Each new member is measured against the cluster's floor, not its last
    addition, so width is bounded by construction."""
    frame = ohlc(np.full(300, 100.0))
    atr = pd.Series(np.full(300, 2.0), index=frame.index)

    ladder = [pivot(10 + i, 100.0 + i) for i in range(9)]
    clusters = cluster_supports(ladder, atr, tolerance=0.6)

    assert len(clusters) > 1, "the ladder must not collapse into one zone"
    for cluster in clusters:
        prices = [p.price for p in cluster]
        assert max(prices) - min(prices) <= 0.6 * 2.0 + 1e-9


def test_a_zone_stays_narrow_enough_to_place_a_stop_against():
    frame = ohlc(np.concatenate([np.linspace(120, 100, 200), np.full(100, 100.0)]))
    atr = atr_of(frame)
    zones = build_zones(frame, atr, timeframe="daily")
    last = float(frame["close"].iloc[-1])
    for zone in zones:
        if zone.source is ZoneSource.VOLUME_SHELF:
            continue
        assert zone.width / last < 0.10, f"{zone.floor}-{zone.ceil} spans too much of price"


class TestZoneStatisticsAreAsOfABar:
    """A zone's event list runs to the end of the frame it was built from.

    Every statistic derived from it therefore has to be asked for as of a bar,
    or a backtest reads the future. This is not hypothetical: measured on the
    whole life, zone respect scored an information coefficient of +0.19 at
    t = +17 with the predicted sign on all fourteen rebalances — the signature
    of a feature that already knows the answer.
    """

    def zone(self) -> Zone:
        z = Zone(
            timeframe="daily",
            source=ZoneSource.PIVOT,
            floor=100.0,
            ceil=104.0,
            formed_index=0,
            formed_date=pd.Timestamp("2024-01-01"),
        )
        z.events = [
            ZoneEvent(10, pd.Timestamp("2024-01-11"), "rejection"),
            ZoneEvent(20, pd.Timestamp("2024-01-21"), "rejection"),
            # The break happens later. Anything scored before bar 50 must not
            # be able to see it.
            ZoneEvent(50, pd.Timestamp("2024-02-20"), "break"),
        ]
        return z

    def test_respect_before_the_break_does_not_know_about_it(self):
        z = self.zone()
        assert z.respect_at(30) == pytest.approx(1.0)

    def test_respect_after_the_break_counts_it(self):
        z = self.zone()
        assert z.respect_at(60) == pytest.approx(2 / 3)

    def test_the_whole_life_property_is_the_after_view(self):
        # Kept for the live path, where the last bar in the frame is today.
        assert self.zone().respect == pytest.approx(2 / 3)

    def test_strength_before_the_break_is_higher_than_after(self):
        z = self.zone()
        before = rate_strength(z, at_index=30, timeframe="daily")
        after = rate_strength(z, at_index=60, timeframe="daily")
        assert before > after

    def test_touch_and_break_counts_are_gated_too(self):
        z = self.zone()
        assert len(z.breaks_by(30)) == 0
        assert len(z.breaks_by(60)) == 1
        assert len(z.touches_by(15)) == 1


class TestVolumeShelvesEdgeBars:
    """Bars sitting exactly on the window's high or low.

    Found by running the engine over companies that left the index: a stock
    winding down prints flat bars, and one at the very top of the range put
    both bin indices past the last bin — an empty slice, a divide by zero, and
    that bar's volume quietly discarded.
    """

    def frame(self) -> pd.DataFrame:
        rows = []
        for i in range(60):
            rows.append({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1000.0})
        # A flat bar exactly on the window high, then one exactly on the low.
        rows.append({"open": 101.0, "high": 101.0, "low": 101.0,
                     "close": 101.0, "volume": 50000.0})
        rows.append({"open": 99.0, "high": 99.0, "low": 99.0,
                     "close": 99.0, "volume": 50000.0})
        index = pd.date_range("2024-01-01", periods=len(rows), freq="B")
        return pd.DataFrame(rows, index=index)

    def test_no_warning_is_raised(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            volume_shelves(self.frame())

    def test_a_bar_on_the_high_still_contributes_its_volume(self):
        # 50,000 on one bar against 1,000 on sixty others: if that bar were
        # dropped the top of the range would not register as a shelf at all.
        shelves = volume_shelves(self.frame())
        assert shelves, "the heaviest bar produced no shelf"
