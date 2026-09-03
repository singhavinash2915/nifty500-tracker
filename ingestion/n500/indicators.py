"""Technical indicators.

Pure functions over pandas Series so they can be unit-tested against hand-worked
numbers with no network and no database. Wilder's smoothing is used wherever the
original indicator specifies it (RSI, ATR, ADX) rather than a plain EMA — the
two differ enough to move a score across a threshold.

Every function returns a Series aligned to the input index, NaN-padded at the
front where there is not yet enough history. Nothing forward-fills: a NaN means
"not computable yet", and the scoring layer must treat it as missing rather than
as zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing, seeded with the simple mean of the first `period`.

    Equivalent to an EMA with alpha = 1/period, but the seed matters: an
    unseeded ewm drifts for the first few hundred bars, which is exactly the
    range a 2-year history lives in.
    """
    values = series.to_numpy(dtype="float64")
    out = np.full(values.shape, np.nan)

    # Count only real observations. Callers pass differenced series whose first
    # element is NaN; seeding at position period-1 would then average period-1
    # values over period and shift every subsequent bar.
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < period:
        return pd.Series(out, index=series.index)

    first = valid[period - 1]
    seed = values[valid[:period]].mean()
    out[first] = seed

    alpha = 1.0 / period
    prev = seed
    for i in range(first + 1, len(values)):
        current = values[i]
        if np.isnan(current):
            out[i] = prev
            continue
        prev = prev + alpha * (current - prev)
        out[i] = prev

    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = wilder(gain, period)
    avg_loss = wilder(loss, period)

    # A run with no losses is RSI 100 by definition, not a divide-by-zero.
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out.where(avg_gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    atr_ = wilder(true_range(high, low, close), period)
    safe_atr = atr_.replace(0.0, np.nan)

    plus_di = 100.0 * wilder(plus_dm, period) / safe_atr
    minus_di = 100.0 * wilder(minus_dm, period) / safe_atr

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return wilder(dx, period)


def macd_histogram(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.Series:
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)


def pct_return(close: pd.Series, periods: int) -> pd.Series:
    return close.pct_change(periods)


def momentum_12_1(close: pd.Series) -> pd.Series:
    """12-month return excluding the most recent month.

    The exclusion is the whole point: the last month carries short-term
    reversal, which drags on a 6-month hold. This is the standard academic
    momentum construction, not a tweak.
    """
    return close.shift(TRADING_DAYS_MONTH) / close.shift(TRADING_DAYS_YEAR) - 1.0


def distance_from_high(close: pd.Series, high: pd.Series, window: int = TRADING_DAYS_YEAR) -> pd.Series:
    """Fraction below the rolling high. 0.0 means at the high; 0.25 means 25% below."""
    rolling_high = high.rolling(window, min_periods=window // 2).max()
    return (rolling_high - close) / rolling_high


def max_drawdown(close: pd.Series, window: int) -> pd.Series:
    """Worst peak-to-trough fall within each trailing window, as a positive fraction."""
    rolling_peak = close.rolling(window, min_periods=window // 2).max()
    drawdown = (rolling_peak - close) / rolling_peak
    return drawdown.rolling(window, min_periods=window // 2).max()


def volume_ratio(volume: pd.Series, short: int = 20, long: int = 100) -> pd.Series:
    short_avg = volume.rolling(short, min_periods=short).mean()
    long_avg = volume.rolling(long, min_periods=long).mean().replace(0.0, np.nan)
    return short_avg / long_avg


def slope(series: pd.Series, periods: int = TRADING_DAYS_MONTH) -> pd.Series:
    """Fractional change of a series over `periods` bars — used for the 200DMA."""
    past = series.shift(periods)
    return (series - past) / past.abs().replace(0.0, np.nan)
