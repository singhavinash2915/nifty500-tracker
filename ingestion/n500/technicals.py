"""Builds the technicals_daily frame for one symbol.

Takes a price frame and the benchmark's close series, returns one row per date
with every indicator the momentum score reads. Pure computation — no network,
no database — so it can be tested against synthetic price paths.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

MONTH = ind.TRADING_DAYS_MONTH
YEAR = ind.TRADING_DAYS_YEAR
HALF_YEAR = YEAR // 2

TECHNICAL_COLUMNS = [
    "sma20", "sma50", "sma200", "wma50", "sma200_slope",
    "rsi14", "macd_hist", "adx14", "atr14", "atr_pct",
    "ret_1m", "ret_3m", "ret_6m", "ret_12m", "mom_12_1",
    "rs_vs_index", "dist_52w_high", "vol_ratio_20_100", "max_dd_6m",
]


def compute(prices: pd.DataFrame, *, index_close: pd.Series | None = None) -> pd.DataFrame:
    """`prices` must be indexed by date and hold open/high/low/close/volume."""
    if prices.empty:
        return pd.DataFrame(columns=TECHNICAL_COLUMNS)

    prices = prices.sort_index()
    close = prices["close"].astype("float64")
    high = prices["high"].astype("float64")
    low = prices["low"].astype("float64")
    volume = prices["volume"].astype("float64")

    out = pd.DataFrame(index=prices.index)

    out["sma20"] = ind.sma(close, 20)
    out["sma50"] = ind.sma(close, 50)
    out["sma200"] = ind.sma(close, 200)
    # The weekly 50 MA that swing traders watch, expressed in daily bars.
    out["wma50"] = ind.sma(close, 50 * 5)
    out["sma200_slope"] = ind.slope(out["sma200"], MONTH)

    out["rsi14"] = ind.rsi(close, 14)
    out["macd_hist"] = ind.macd_histogram(close)
    out["adx14"] = ind.adx(high, low, close, 14)

    atr14 = ind.atr(high, low, close, 14)
    out["atr14"] = atr14
    out["atr_pct"] = atr14 / close

    out["ret_1m"] = ind.pct_return(close, MONTH)
    out["ret_3m"] = ind.pct_return(close, MONTH * 3)
    out["ret_6m"] = ind.pct_return(close, HALF_YEAR)
    out["ret_12m"] = ind.pct_return(close, YEAR)
    out["mom_12_1"] = ind.momentum_12_1(close)

    out["rs_vs_index"] = _relative_strength(close, index_close)

    out["dist_52w_high"] = ind.distance_from_high(close, high, YEAR)
    out["vol_ratio_20_100"] = ind.volume_ratio(volume, 20, 100)
    out["max_dd_6m"] = ind.max_drawdown(close, HALF_YEAR)

    return out[TECHNICAL_COLUMNS]


def _relative_strength(close: pd.Series, index_close: pd.Series | None) -> pd.Series:
    """Six-month outperformance of the benchmark, as a fraction.

    0.10 means the stock returned ten percentage points more than the index
    over six months, compounded rather than subtracted.
    """
    if index_close is None or index_close.empty:
        return pd.Series(np.nan, index=close.index, dtype="float64")

    # The benchmark trades on the same calendar, but a stock can be suspended
    # for a day. Align on the stock's dates and forward-fill the index across
    # any gap rather than dropping the stock's bar.
    aligned = index_close.reindex(close.index).ffill()

    stock = ind.pct_return(close, HALF_YEAR)
    bench = ind.pct_return(aligned, HALF_YEAR)
    return (1.0 + stock) / (1.0 + bench) - 1.0


def to_rows(symbol: str, frame: pd.DataFrame) -> list[dict]:
    """Serialise for Supabase, dropping rows where nothing is computable yet."""
    frame = frame.dropna(how="all")
    rows: list[dict] = []
    for when, row in frame.iterrows():
        record: dict = {"symbol": symbol, "date": pd.Timestamp(when).date().isoformat()}
        for column in TECHNICAL_COLUMNS:
            value = row[column]
            record[column] = None if pd.isna(value) else float(value)
        rows.append(record)
    return rows
