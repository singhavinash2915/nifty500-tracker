"""T-M — the momentum / breakout setup score.

Weights come straight from the build plan:

    momentum        30   12-1 momentum and relative strength vs the index
    trend           22   price > 50DMA > 200DMA, and a rising 200DMA
    52w proximity   16   how close to the 52-week high
    volume          14   20d average against 100d — accumulation
    oscillators     10   RSI band, MACD histogram, ADX
    volatility       8   ATR% and 6-month drawdown, as a guard

Momentum, proximity and volume are ranked against sector peers, because "strong
for a bank" is not "strong for a chemicals name". Trend, oscillators and the
volatility guard are rule-based: they encode absolute conditions that mean the
same thing in every sector, and ranking them would let the best stock in a
falling sector score well while below its 200DMA.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ranking import band_score, clamp, peer_groups, percentile

WEIGHTS = {
    "momentum": 30.0,
    "trend": 22.0,
    "proximity": 16.0,
    "volume": 14.0,
    "oscillators": 10.0,
    "volatility": 8.0,
}

# A stock cannot be a momentum candidate while it is below its own 200DMA,
# however well it ranks on everything else. This is a cap, not a deduction:
# without it the screener hands you falling stocks that are falling slightly
# less than their peers.
BELOW_200DMA_CAP = 40.0


def component_scores(snapshot: pd.DataFrame) -> pd.DataFrame:
    """`snapshot` is one row per symbol for a single date, indexed by symbol.

    Expects the technicals columns plus a `sector` column.
    """
    groups = peer_groups(snapshot["sector"])
    out = pd.DataFrame(index=snapshot.index)

    # --- momentum: 12-1 and relative strength, equally weighted -------------
    mom_rank = percentile(snapshot["mom_12_1"], groups)
    rs_rank = percentile(snapshot["rs_vs_index"], groups)
    out["momentum"] = _blend(mom_rank, rs_rank)

    # --- trend: absolute structure, not relative ---------------------------
    close = snapshot["close"]
    above_50 = close > snapshot["sma50"]
    above_200 = close > snapshot["sma200"]
    stacked = snapshot["sma50"] > snapshot["sma200"]
    rising = snapshot["sma200_slope"] > 0

    trend = (
        above_200.astype("float64") * 40.0
        + above_50.astype("float64") * 20.0
        + stacked.astype("float64") * 20.0
        + rising.astype("float64") * 20.0
    )
    # Any component that could not be computed makes the whole trend unknown.
    unknown = snapshot[["sma50", "sma200", "sma200_slope"]].isna().any(axis=1)
    out["trend"] = trend.where(~unknown)

    # --- proximity to the 52-week high -------------------------------------
    # Ranked low-is-better: dist_52w_high is the fraction *below* the high.
    out["proximity"] = percentile(snapshot["dist_52w_high"], groups, ascending=False)

    # --- volume: accumulation ----------------------------------------------
    out["volume"] = percentile(snapshot["vol_ratio_20_100"], groups)

    # --- oscillators --------------------------------------------------------
    # RSI 55-75 is strength; above 80 is exhaustion, below 40 is weakness.
    rsi = band_score(snapshot["rsi14"], ideal=(55.0, 75.0), zero_below=35.0, zero_above=90.0)
    macd = pd.Series(np.where(snapshot["macd_hist"] > 0, 100.0, 0.0), index=snapshot.index)
    macd = macd.where(snapshot["macd_hist"].notna())
    # ADX under 20 is a trendless market; 25+ is a real trend.
    adx = clamp((snapshot["adx14"] - 15.0) / 20.0 * 100.0)
    out["oscillators"] = _blend(rsi, macd, adx)

    # --- volatility guard ---------------------------------------------------
    # Lower ATR% and shallower drawdowns score higher; this trims the score of
    # names whose "momentum" is really just noise.
    atr_rank = percentile(snapshot["atr_pct"], groups, ascending=False)
    dd_rank = percentile(snapshot["max_dd_6m"], groups, ascending=False)
    out["volatility"] = _blend(atr_rank, dd_rank)

    return out


def score(snapshot: pd.DataFrame) -> pd.Series:
    """Weighted T-M score in 0-100, NaN where too little is computable."""
    components = component_scores(snapshot)

    weighted = pd.Series(0.0, index=snapshot.index)
    weight_used = pd.Series(0.0, index=snapshot.index)
    for name, weight in WEIGHTS.items():
        values = components[name]
        present = values.notna()
        weighted += values.fillna(0.0) * weight
        weight_used += present.astype("float64") * weight

    # Renormalise over the components that were computable, so a stock is not
    # punished for a single missing indicator — but require most of the weight
    # to be present before publishing a score at all.
    total = sum(WEIGHTS.values())
    enough = weight_used >= total * 0.6
    result = (weighted / weight_used.replace(0.0, np.nan)) * 1.0
    result = result.where(enough)

    below_200 = snapshot["close"] < snapshot["sma200"]
    result = result.mask(below_200 & result.notna(), result.clip(upper=BELOW_200DMA_CAP))

    return clamp(result)


def _blend(*parts: pd.Series) -> pd.Series:
    """Mean of the parts that are present, NaN only if all are missing."""
    frame = pd.concat(parts, axis=1)
    return frame.mean(axis=1, skipna=True)
