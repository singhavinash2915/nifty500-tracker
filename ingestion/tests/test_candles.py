"""Candle shapes, built bar by bar from numbers rather than from a chart.

The tests worth having here are the ones that pin the module's central claim:
a hammer and a hanging man are the same candle, and only location separates
them. If that ever stops being true the abstraction has leaked.
"""

from __future__ import annotations

import pandas as pd

from n500.zones import candles


def bars(*rows: tuple[float, float, float, float]) -> pd.DataFrame:
    """Each row is (open, high, low, close)."""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame.index = pd.date_range("2024-01-01", periods=len(frame), freq="B")
    frame["volume"] = 1000.0
    return frame


HAMMER = (104.0, 105.0, 95.0, 104.5)          # long lower wick, body at the top
INVERTED = (95.5, 105.0, 95.0, 96.0)          # long upper wick, body at the bottom
DOJI = (100.0, 105.0, 95.0, 100.2)
MARUBOZU = (95.2, 105.0, 95.0, 104.8)
# Symmetric wicks either side of a modest body: a spinning top, which is the
# genuinely neutral bar. The first attempt here used (100, 102, 98, 101), which
# has a 2:1 lower wick and duly registered as a hammer — correctly.
PLAIN = (99.5, 102.0, 98.0, 100.5)


class TestShape:
    def test_a_hammer_is_recognised(self):
        s = candles.shape(bars(HAMMER), 0)
        assert s["hammer_shape"]
        assert not s["inverted_hammer_shape"]

    def test_an_inverted_hammer_is_recognised(self):
        s = candles.shape(bars(INVERTED), 0)
        assert s["inverted_hammer_shape"]
        assert not s["hammer_shape"]

    def test_a_doji_has_almost_no_body(self):
        assert candles.shape(bars(DOJI), 0)["doji"]
        assert not candles.shape(bars(MARUBOZU), 0)["doji"]

    def test_a_marubozu_is_almost_all_body(self):
        assert candles.shape(bars(MARUBOZU), 0)["marubozu"]

    def test_an_ordinary_bar_is_neither(self):
        s = candles.shape(bars(PLAIN), 0)
        assert not s["hammer_shape"]
        assert not s["inverted_hammer_shape"]

    def test_a_zero_range_bar_returns_nothing(self):
        assert candles.shape(bars((100.0, 100.0, 100.0, 100.0)), 0) == {}

    def test_an_impossible_bar_returns_nothing(self):
        # close above the high: corrupt data, not a pattern.
        assert candles.shape(bars((100.0, 101.0, 99.0, 105.0)), 0) == {}


class TestSameCandleDifferentName:
    """The module's whole reason for splitting shape from location."""

    def test_a_hammer_at_a_high_is_a_hanging_man(self):
        frame = bars(HAMMER)
        support = candles.at_support(frame, 0, floor=94.0, ceil=99.0)
        resistance = candles.at_resistance(frame, 0, floor=104.6, ceil=110.0)
        assert support["hammer_at_support"]
        assert resistance["hanging_man_at_resistance"]

    def test_an_inverted_hammer_at_a_high_is_a_shooting_star(self):
        frame = bars(INVERTED)
        support = candles.at_support(frame, 0, floor=94.0, ceil=97.0)
        resistance = candles.at_resistance(frame, 0, floor=104.0, ceil=110.0)
        assert support["inverted_hammer_at_support"]
        assert resistance["shooting_star_at_resistance"]


class TestLocation:
    def test_a_hammer_that_never_reached_the_zone_does_not_count(self):
        # Textbook shape, but the low stayed three points above the band.
        frame = bars(HAMMER)
        assert candles.at_support(frame, 0, floor=80.0, ceil=92.0) == {}

    def test_a_close_below_support_is_a_break_not_a_rejection(self):
        # Reached the band and closed under it.
        frame = bars((99.0, 100.0, 90.0, 91.0))
        assert candles.at_support(frame, 0, floor=95.0, ceil=100.0) == {}

    def test_a_close_above_resistance_is_a_breakout_not_a_rejection(self):
        frame = bars((100.0, 112.0, 99.0, 111.0))
        assert candles.at_resistance(frame, 0, floor=105.0, ceil=110.0) == {}


class TestEngulfing:
    def test_bullish_engulfing(self):
        frame = bars((105.0, 106.0, 100.0, 101.0), (100.0, 107.0, 99.5, 106.0))
        assert candles.engulfing(frame, 1)["bullish_engulfing"]

    def test_bearish_engulfing(self):
        frame = bars((101.0, 106.0, 100.0, 105.0), (106.0, 106.5, 99.0, 100.0))
        assert candles.engulfing(frame, 1)["bearish_engulfing"]

    def test_a_body_inside_the_previous_one_is_not_engulfing(self):
        frame = bars((105.0, 106.0, 100.0, 101.0), (102.0, 105.0, 101.5, 104.0))
        assert not candles.engulfing(frame, 1)["bullish_engulfing"]

    def test_the_first_bar_cannot_engulf_anything(self):
        assert not candles.engulfing(bars(PLAIN), 0)["bullish_engulfing"]


class TestPiercing:
    def test_piercing_line_reclaims_past_the_midpoint(self):
        # Down bar 110 -> 100, then up from 99 to 106: past the 105 midpoint,
        # but not above the 110 open, so not an engulfing.
        frame = bars((110.0, 111.0, 99.0, 100.0), (99.0, 107.0, 98.0, 106.0))
        assert candles.piercing(frame, 1)["piercing_line"]
        assert not candles.engulfing(frame, 1)["bullish_engulfing"]

    def test_a_shallow_reclaim_is_not_piercing(self):
        frame = bars((110.0, 111.0, 99.0, 100.0), (99.0, 103.0, 98.0, 102.0))
        assert not candles.piercing(frame, 1)["piercing_line"]

    def test_dark_cloud_is_the_mirror(self):
        frame = bars((100.0, 111.0, 99.0, 110.0), (111.0, 112.0, 103.0, 104.0))
        assert candles.piercing(frame, 1)["dark_cloud"]
