"""Ranking helpers and the T-M momentum score."""

import numpy as np
import pandas as pd
import pytest

from n500.scoring import momentum
from n500.scoring.ranking import POOLED, band_score, peer_groups, percentile


# --- peer grouping --------------------------------------------------------


def test_thin_sectors_are_pooled():
    sectors = pd.Series(["Fin"] * 12 + ["Textiles"] * 3 + ["IT"] * 10)
    groups = peer_groups(sectors)
    assert set(groups[:12]) == {"Fin"}
    assert set(groups[12:15]) == {POOLED}   # too few peers to rank inside
    assert set(groups[15:]) == {"IT"}


def test_missing_sector_is_pooled():
    groups = peer_groups(pd.Series(["Fin"] * 10 + [None]))
    assert groups.iloc[-1] == POOLED


# --- percentile -----------------------------------------------------------


def test_percentile_spans_zero_to_one_hundred():
    out = percentile(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert out.iloc[0] == 0.0
    assert out.iloc[-1] == 100.0
    assert out.iloc[2] == 50.0


def test_percentile_descending_rewards_low_values():
    out = percentile(pd.Series([1.0, 5.0]), ascending=False)
    assert out.iloc[0] == 100.0
    assert out.iloc[1] == 0.0


def test_percentile_keeps_nan_as_nan():
    out = percentile(pd.Series([1.0, np.nan, 3.0]))
    assert np.isnan(out.iloc[1])
    assert out.iloc[0] == 0.0


def test_percentile_ranks_within_groups_not_across_them():
    values = pd.Series([1.0, 2.0, 100.0, 200.0])
    groups = pd.Series(["a", "a", "b", "b"])
    out = percentile(values, groups)
    # The smallest value in each group scores 0, the largest 100 — a stock is
    # never rewarded merely for belonging to a high-magnitude sector.
    assert list(out) == [0.0, 100.0, 0.0, 100.0]


def test_a_lone_member_scores_fifty_not_one_hundred():
    out = percentile(pd.Series([7.0]))
    assert out.iloc[0] == 50.0


# --- band score -----------------------------------------------------------


def test_rsi_band_rewards_strength_and_punishes_exhaustion():
    values = pd.Series([30.0, 45.0, 65.0, 80.0, 95.0])
    out = band_score(values, ideal=(55.0, 75.0), zero_below=35.0, zero_above=90.0)
    assert out.iloc[0] == 0.0      # oversold, below the floor
    assert 0 < out.iloc[1] < 100   # climbing into the band
    assert out.iloc[2] == 100.0    # in the band
    assert 0 < out.iloc[3] < 100   # getting extended
    assert out.iloc[4] == 0.0      # exhausted


# --- T-M ------------------------------------------------------------------


def frame(n=20, **overrides):
    """A neutral cross-section, tweakable per test."""
    data = {
        "sector": ["Fin"] * n,
        "close": np.linspace(100, 120, n),
        "sma50": np.full(n, 90.0),
        "sma200": np.full(n, 80.0),
        "sma200_slope": np.full(n, 0.05),
        "mom_12_1": np.linspace(-0.2, 0.5, n),
        "rs_vs_index": np.linspace(-0.1, 0.3, n),
        "dist_52w_high": np.linspace(0.4, 0.0, n),
        "vol_ratio_20_100": np.linspace(0.6, 2.0, n),
        "rsi14": np.full(n, 62.0),
        "macd_hist": np.full(n, 1.0),
        "adx14": np.full(n, 30.0),
        "atr_pct": np.full(n, 0.02),
        "max_dd_6m": np.full(n, 0.1),
    }
    data.update(overrides)
    return pd.DataFrame(data, index=[f"S{i:02d}" for i in range(n)])


def test_score_is_bounded_and_ordered_by_momentum():
    out = momentum.score(frame())
    assert out.min() >= 0 and out.max() <= 100
    # The frame is built so the last row is strongest on every ranked input.
    assert out.iloc[-1] > out.iloc[0]


def test_a_stock_below_its_200dma_is_capped():
    """The rule that stops the screener recommending falling stocks that are
    falling slightly less than their peers."""
    n = 20
    df = frame(n, sma200=np.full(n, 500.0))   # every close is below the 200DMA
    out = momentum.score(df)
    assert out.max() <= momentum.BELOW_200DMA_CAP


def test_the_cap_does_not_apply_above_the_200dma():
    out = momentum.score(frame())
    assert out.max() > momentum.BELOW_200DMA_CAP


def test_trend_component_rewards_the_full_stack():
    df = frame(20)
    strong = momentum.component_scores(df)["trend"].iloc[0]
    weak = momentum.component_scores(
        frame(20, sma50=np.full(20, 500.0), sma200_slope=np.full(20, -0.05))
    )["trend"].iloc[0]
    assert strong == 100.0
    assert weak < strong


def test_score_is_nan_when_almost_everything_is_missing():
    n = 20
    df = frame(n)
    for column in ["mom_12_1", "rs_vs_index", "dist_52w_high",
                   "vol_ratio_20_100", "rsi14", "macd_hist", "adx14",
                   "sma50", "sma200", "sma200_slope"]:
        df[column] = np.nan
    assert momentum.score(df).isna().all()


def test_one_missing_indicator_does_not_void_the_score():
    df = frame(20)
    df["adx14"] = np.nan
    out = momentum.score(df)
    assert out.notna().all()


def test_exhausted_rsi_scores_below_healthy_rsi():
    healthy = momentum.component_scores(frame(20))["oscillators"].iloc[0]
    hot = momentum.component_scores(frame(20, rsi14=np.full(20, 88.0)))["oscillators"].iloc[0]
    assert hot < healthy
