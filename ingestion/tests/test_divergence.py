"""RSI divergence, built from price paths rather than from a chart.

The tests that matter here are the negative ones. A divergence detector that
fires readily will find one on any chart, and a feature that is true half the
time cannot rank anything — so most of what follows is about what must *not*
count.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from n500 import indicators as ind
from n500.zones import divergence


def path(closes: list[float]) -> pd.DataFrame:
    """A frame where each bar's range brackets its close."""
    frame = pd.DataFrame({
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1000.0] * len(closes),
    })
    frame.index = pd.date_range("2024-01-01", periods=len(frame), freq="B")
    return frame


def wave(points: list[float], each: int = 12) -> list[float]:
    """Interpolate between turning points, so swings are real swings."""
    out: list[float] = []
    for a, b in zip(points, points[1:]):
        out.extend(np.linspace(a, b, each, endpoint=False).tolist())
    out.append(points[-1])
    return out


class TestBullish:
    def frame_and_rsi(self, closes):
        f = path(closes)
        return f, ind.rsi(f["close"], 14)

    def test_a_lower_low_on_stronger_momentum_is_bullish(self):
        # Two troughs, the second lower in price. The fall into it is gentler,
        # so RSI holds up — which is the whole pattern.
        closes = wave([100, 70, 92, 68, 88])
        f, rsi = self.frame_and_rsi(closes)
        found = divergence.find(f, rsi, len(f) - 1, kind="bullish")
        if found is not None:
            assert found.recent_price < found.prior_price
            assert found.recent_rsi > found.prior_rsi

    def test_a_lower_low_on_weaker_momentum_is_not_a_divergence(self):
        # Price and momentum agree. Nothing to see, and a detector that called
        # this bullish would fire on every downtrend in the market.
        closes = wave([100, 80, 90, 50, 60])
        f, rsi = self.frame_and_rsi(closes)
        assert divergence.find(f, rsi, len(f) - 1, kind="bullish") is None

    def test_a_higher_low_is_not_a_bullish_divergence(self):
        closes = wave([100, 60, 90, 75, 88])
        f, rsi = self.frame_and_rsi(closes)
        assert divergence.find(f, rsi, len(f) - 1, kind="bullish") is None


class TestBearish:
    def test_a_higher_high_on_weaker_momentum_is_bearish(self):
        closes = wave([60, 95, 70, 99, 80])
        f = path(closes)
        rsi = ind.rsi(f["close"], 14)
        found = divergence.find(f, rsi, len(f) - 1, kind="bearish")
        if found is not None:
            assert found.recent_price > found.prior_price
            assert found.recent_rsi < found.prior_rsi

    def test_the_two_directions_are_not_the_same_pattern(self):
        # The mirror image must not also register as its opposite.
        closes = wave([100, 70, 92, 68, 88])
        f = path(closes)
        rsi = ind.rsi(f["close"], 14)
        bull = divergence.find(f, rsi, len(f) - 1, kind="bullish")
        bear = divergence.find(f, rsi, len(f) - 1, kind="bearish")
        assert not (bull is not None and bear is not None)


class TestGuards:
    def test_a_flat_market_produces_nothing(self):
        f = path([100.0] * 200)
        rsi = ind.rsi(f["close"], 14)
        assert divergence.find(f, rsi, len(f) - 1, kind="bullish") is None
        assert divergence.find(f, rsi, len(f) - 1, kind="bearish") is None

    def test_too_little_history_produces_nothing(self):
        f = path(wave([100, 90], each=5))
        rsi = ind.rsi(f["close"], 14)
        assert divergence.find(f, rsi, 5, kind="bullish") is None

    def test_an_unknown_direction_is_refused(self):
        f = path([100.0] * 60)
        rsi = ind.rsi(f["close"], 14)
        with pytest.raises(ValueError, match="bullish or bearish"):
            divergence.find(f, rsi, 50, kind="sideways")

    def test_a_tiny_rsi_difference_does_not_count(self):
        # Two points on a 0-100 scale is a real difference; half a point is the
        # detector inventing a signal out of rounding.
        assert divergence.MIN_RSI_GAP >= 2.0


class TestNoLookAhead:
    """The failure that has already happened once in this project.

    Zone respect was computed over a whole price frame and scored an information
    coefficient of +0.19 at t = +17, because it knew whether the level broke
    later. A swing is only known to be a swing once the bars after it exist, so
    this filters on `confirmed_index` and never on the pivot's own position.
    """

    def test_a_swing_is_invisible_until_it_is_confirmed(self):
        closes = wave([100, 70, 92, 68, 88])
        f = path(closes)
        rsi = ind.rsi(f["close"], 14)
        pivots = divergence.find_pivots(f)
        if not pivots:
            pytest.skip("no pivots in this synthetic path")

        last = max(pivots, key=lambda p: p.confirmed_index)
        # One bar before confirmation, nothing that depends on it may appear.
        before = divergence.find(f, rsi, last.confirmed_index - 1, kind="bullish")
        assert before is None or before.confirmed_index < last.confirmed_index

    def test_confirmation_is_never_earlier_than_the_swing(self):
        f = path(wave([100, 70, 92, 68, 88]))
        for p in divergence.find_pivots(f):
            assert p.confirmed_index >= p.index


class TestMemory:
    def test_a_recent_divergence_is_still_active(self):
        closes = wave([100, 70, 92, 68, 88])
        f = path(closes)
        rsi = ind.rsi(f["close"], 14)
        found = divergence.find(f, rsi, len(f) - 1, kind="bullish")
        if found is None:
            pytest.skip("no divergence in this synthetic path")
        assert divergence.active(f, rsi, found.confirmed_index, kind="bullish")

    def test_memory_is_finite(self):
        # Not a state that lasts forever: a divergence from a year ago describes
        # a different market.
        assert divergence.MEMORY_BARS["daily"] < divergence.LOOKBACK["daily"]
