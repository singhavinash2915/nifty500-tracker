"""Indicator tests.

The RSI case is the classic Wilder worked example from *New Concepts in
Technical Trading Systems* — if this drifts, the smoothing seed is wrong.
"""

import numpy as np
import pandas as pd
import pytest

from n500 import indicators as ind


# Wilder's own 14-period RSI example series.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
    45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
    46.03, 46.41, 46.22, 45.64,
]


def test_rsi_matches_wilders_worked_example():
    rsi = ind.rsi(pd.Series(WILDER_CLOSES), 14)
    # Published values for the first three computable bars.
    assert rsi.iloc[14] == pytest.approx(70.46, abs=0.05)
    assert rsi.iloc[15] == pytest.approx(66.25, abs=0.05)
    assert rsi.iloc[16] == pytest.approx(66.48, abs=0.05)


def test_rsi_is_nan_before_enough_history():
    rsi = ind.rsi(pd.Series(WILDER_CLOSES), 14)
    assert rsi.iloc[:14].isna().all()


def test_rsi_is_100_when_price_only_rises():
    rsi = ind.rsi(pd.Series(np.arange(1.0, 40.0)), 14)
    assert rsi.iloc[-1] == 100.0


def test_rsi_is_zero_when_price_only_falls():
    rsi = ind.rsi(pd.Series(np.arange(40.0, 1.0, -1.0)), 14)
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_wilder_seed_is_the_simple_mean():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ind.wilder(s, 5)
    assert out.iloc[4] == pytest.approx(3.0)
    assert out.iloc[:4].isna().all()


def test_wilder_returns_all_nan_when_too_short():
    assert ind.wilder(pd.Series([1.0, 2.0]), 5).isna().all()


def test_true_range_uses_the_widest_of_the_three():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([9.0, 11.0])
    close = pd.Series([9.5, 11.5])
    tr = ind.true_range(high, low, close)
    assert tr.iloc[0] == pytest.approx(1.0)        # no prior close, high-low
    assert tr.iloc[1] == pytest.approx(2.5)        # high - prev close


def test_atr_on_a_constant_range_equals_that_range():
    n = 40
    high = pd.Series([11.0] * n)
    low = pd.Series([10.0] * n)
    close = pd.Series([10.5] * n)
    assert ind.atr(high, low, close, 14).iloc[-1] == pytest.approx(1.0)


def test_adx_is_high_in_a_clean_trend_and_low_in_a_chop():
    n = 120
    trend_close = pd.Series(np.linspace(100, 200, n))
    trend = ind.adx(trend_close + 1, trend_close - 1, trend_close, 14)

    chop_close = pd.Series([100 + (i % 2) for i in range(n)], dtype="float64")
    chop = ind.adx(chop_close + 1, chop_close - 1, chop_close, 14)

    assert trend.iloc[-1] > 40
    assert chop.iloc[-1] < 25


def test_macd_histogram_is_positive_when_price_accelerates_up():
    close = pd.Series(np.concatenate([np.full(60, 100.0), np.linspace(100, 140, 40)]))
    assert ind.macd_histogram(close).iloc[-5] > 0


def test_momentum_12_1_excludes_the_last_month():
    # Flat for a year, then a spike only in the final month. 12-1 must ignore it.
    close = pd.Series([100.0] * 253 + [200.0] * 21)
    mom = ind.momentum_12_1(close)
    assert mom.iloc[-1] == pytest.approx(0.0, abs=1e-9)

    # Whereas the plain 12-month return does see it.
    assert ind.pct_return(close, ind.TRADING_DAYS_YEAR).iloc[-1] > 0.9


def test_distance_from_high_is_zero_at_the_high():
    close = pd.Series(np.linspace(100, 200, 300))
    dist = ind.distance_from_high(close, close)
    assert dist.iloc[-1] == pytest.approx(0.0, abs=1e-12)


def test_distance_from_high_measures_the_fall():
    close = pd.Series(np.concatenate([np.linspace(100, 200, 200), np.full(60, 150.0)]))
    dist = ind.distance_from_high(close, close)
    assert dist.iloc[-1] == pytest.approx(0.25, abs=1e-9)


def test_max_drawdown_finds_the_worst_fall_in_the_window():
    close = pd.Series(np.concatenate([np.full(60, 100.0), np.full(66, 60.0)]))
    assert ind.max_drawdown(close, 126).iloc[-1] == pytest.approx(0.40, abs=1e-9)


def test_volume_ratio_flags_accumulation():
    volume = pd.Series([1000.0] * 100 + [3000.0] * 20)
    assert ind.volume_ratio(volume).iloc[-1] == pytest.approx(3000 / 1400, rel=1e-6)


def test_slope_is_positive_for_a_rising_average():
    s = pd.Series(np.linspace(100, 130, 100))
    assert ind.slope(s, 21).iloc[-1] > 0
    assert ind.slope(s.iloc[::-1].reset_index(drop=True), 21).iloc[-1] < 0


def test_wilder_seeds_after_period_real_observations_not_positions():
    """Regression: a leading NaN (as produced by .diff()) must not count
    toward the seed window, or every subsequent bar is shifted."""
    s = pd.Series([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0])
    out = ind.wilder(s, 5)
    assert out.iloc[:5].isna().all()
    assert out.iloc[5] == pytest.approx(3.0)
