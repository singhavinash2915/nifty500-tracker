"""Reversal confirmation, and the structure checks that veto it.

Proximity to support is not a signal. A stock in a downtrend spends its whole
descent near support — each level it reaches looks like the one that will hold,
right up until it doesn't. What separates a reversal from a pause is evidence
that buyers actually showed up: a candle that rejected the low, a divergence
where price made a lower low but momentum did not, a reclaim of the short
moving average.

Everything here returns booleans and small floats over the *last* bar or two,
computed from data available at that bar. Nothing looks forward.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# A lower wick this much larger than the body counts as rejection.
HAMMER_WICK_RATIO = 1.8

# How far back to look for the prior swing low when testing for divergence.
DIVERGENCE_LOOKBACK = {"daily": 60, "weekly": 26}
DIVERGENCE_MIN_SEPARATION = {"daily": 8, "weekly": 3}


@dataclass(frozen=True)
class Confirmation:
    bullish_candle: bool = False
    rsi_divergence: bool = False
    macd_turning_up: bool = False
    reclaimed_short_ma: bool = False
    volume_pattern: bool = False

    @property
    def count(self) -> int:
        return sum(asdict(self).values())

    @property
    def triggered(self) -> bool:
        """One clear signal is enough to move from watching to triggered.

        Requiring several would mean entering halfway up the bounce, which
        destroys the reward-to-risk that makes the setup worth taking.
        """
        return self.count >= 1

    def as_dict(self) -> dict:
        return asdict(self)


def bullish_candle(frame: pd.DataFrame, index: int, *, floor: float, ceil: float) -> bool:
    """Hammer, pin bar or bullish engulfing that interacted with the zone."""
    if index < 1:
        return False

    o, h, l, c = (float(frame[k].iloc[index]) for k in ("open", "high", "low", "close"))
    prev_o, prev_c = float(frame["open"].iloc[index - 1]), float(frame["close"].iloc[index - 1])

    # The candle must have actually reached the zone.
    if l > ceil:
        return False

    body = abs(c - o)
    lower_wick = min(o, c) - l
    span = h - l
    if span <= 0:
        return False

    hammer = lower_wick > HAMMER_WICK_RATIO * max(body, span * 0.05) and c > (l + 0.5 * span)
    engulfing = prev_c < prev_o and c > o and c >= prev_o and o <= prev_c
    # Either way the close must be back above the floor: a close under the
    # band is a break in progress, not a rejection of it.
    return (hammer or engulfing) and c > floor


def rsi_divergence(
    frame: pd.DataFrame, rsi: pd.Series, index: int, *, timeframe: str
) -> bool:
    """Price made a lower low; RSI did not. Selling pressure is easing.

    This is the signal on your HDFCBANK chart, in the direction that matters:
    the *bearish* divergence there already played out. A bullish one at the
    zone would be the trigger.
    """
    lookback = DIVERGENCE_LOOKBACK.get(timeframe, 60)
    separation = DIVERGENCE_MIN_SEPARATION.get(timeframe, 8)

    start = max(0, index - lookback)
    lows = frame["low"].iloc[start : index + 1]
    if len(lows) < separation * 2:
        return False

    current_low = float(lows.iloc[-1])
    current_rsi = rsi.iloc[index]
    if pd.isna(current_rsi):
        return False

    # The previous swing low, far enough back to be a separate event.
    earlier = lows.iloc[: -separation]
    if earlier.empty:
        return False
    prior_pos = int(np.nanargmin(earlier.to_numpy()))
    prior_low = float(earlier.iloc[prior_pos])
    prior_rsi = rsi.iloc[start + prior_pos]
    if pd.isna(prior_rsi):
        return False

    return current_low < prior_low and float(current_rsi) > float(prior_rsi)


def macd_turning_up(macd_hist: pd.Series, index: int) -> bool:
    """Histogram still negative but rising for two bars — momentum bottoming."""
    if index < 2:
        return False
    window = macd_hist.iloc[index - 2 : index + 1]
    if window.isna().any():
        return False
    a, b, c = (float(x) for x in window)
    return c > b > a and a < 0


def reclaimed_short_ma(frame: pd.DataFrame, sma20: pd.Series, index: int) -> bool:
    """Closed back above the 20-period average having been below it."""
    if index < 1 or pd.isna(sma20.iloc[index]) or pd.isna(sma20.iloc[index - 1]):
        return False
    close_now = float(frame["close"].iloc[index])
    close_prev = float(frame["close"].iloc[index - 1])
    return close_prev <= float(sma20.iloc[index - 1]) and close_now > float(sma20.iloc[index])


def volume_pattern(frame: pd.DataFrame, index: int) -> bool:
    """Selling dried up into the low, then buyers showed up on the turn.

    Textbook accumulation: the decline loses participation, the bounce gains it.
    """
    if index < 30 or "volume" not in frame:
        return False
    volumes = frame["volume"].to_numpy(dtype="float64")
    baseline = np.nanmean(volumes[max(0, index - 29) : index - 4])
    into_low = np.nanmean(volumes[index - 4 : index])
    on_turn = volumes[index]
    if not np.isfinite(baseline) or baseline <= 0:
        return False
    # bool() is not decoration: these are numpy scalars, and a np.bool_ serialises
    # to the *string* "False" in JSON — which is truthy, so the UI would show a
    # confirmation that never fired.
    return bool(into_low < baseline * 0.85 and on_turn > baseline * 1.2)


def confirm(
    frame: pd.DataFrame,
    *,
    index: int,
    floor: float,
    ceil: float,
    rsi: pd.Series,
    macd_hist: pd.Series,
    sma20: pd.Series,
    timeframe: str,
) -> Confirmation:
    # Every flag is coerced to a native bool on the way in. Numpy booleans reach
    # JSONB as the strings "True"/"False", both of which are truthy.
    return Confirmation(
        bullish_candle=bool(bullish_candle(frame, index, floor=floor, ceil=ceil)),
        rsi_divergence=bool(rsi_divergence(frame, rsi, index, timeframe=timeframe)),
        macd_turning_up=bool(macd_turning_up(macd_hist, index)),
        reclaimed_short_ma=bool(reclaimed_short_ma(frame, sma20, index)),
        volume_pattern=bool(volume_pattern(frame, index)),
    )


# --- structure vetoes -----------------------------------------------------


def making_lower_highs(frame: pd.DataFrame, index: int, *, bars: int = 60) -> bool:
    """The second half of the window peaked below the first half.

    A stock in this state is in a downtrend, and downtrends break supports.
    """
    start = max(0, index - bars)
    highs = frame["high"].iloc[start : index + 1]
    if len(highs) < 20:
        return False
    half = len(highs) // 2
    return float(highs.iloc[half:].max()) < float(highs.iloc[:half].max())


def falling_knife(frame: pd.DataFrame, atr: pd.Series, index: int, *, bars: int = 3) -> bool:
    """Still dropping hard — no evidence of stabilisation yet.

    Either several straight down bars, or one violent bar. Buying into this is
    how a support setup turns into a catching-a-knife story.
    """
    if index < bars:
        return False
    closes = frame["close"].iloc[index - bars : index + 1].to_numpy(dtype="float64")
    consecutive = all(closes[i] < closes[i - 1] for i in range(1, len(closes)))

    local_atr = atr.iloc[index]
    violent = False
    if not pd.isna(local_atr) and float(local_atr) > 0:
        drop = float(frame["close"].iloc[index - 1]) - float(frame["close"].iloc[index])
        violent = drop > 2.5 * float(local_atr)

    return consecutive or violent


def stabilised(frame: pd.DataFrame, index: int) -> bool:
    """An inside bar or an up close — the minimum sign of a pause."""
    if index < 1:
        return False
    h, l, c = (float(frame[k].iloc[index]) for k in ("high", "low", "close"))
    prev_h = float(frame["high"].iloc[index - 1])
    prev_l = float(frame["low"].iloc[index - 1])
    prev_c = float(frame["close"].iloc[index - 1])
    return (h <= prev_h and l >= prev_l) or c > prev_c


# --- resistance behaviour -------------------------------------------------

# How long a breakout has to hold before it stops being suspect.
FALSE_BREAKOUT_WINDOW = {"daily": 5, "weekly": 3}


def false_breakout(
    frame: pd.DataFrame,
    index: int,
    *,
    ceil: float,
    timeframe: str = "daily",
) -> dict | None:
    """A close above resistance that did not hold.

    The pattern that punishes breakout buyers: price closes through the band,
    everyone who was waiting for confirmation gets in, and then it closes back
    underneath within a few bars. The failure is more informative than the
    breakout was — a level that rejected a genuine attempt is stronger
    afterwards, not weaker, and the trapped buyers above become supply.

    Returns the detail of the most recent one inside the window, or None.
    """
    window = FALSE_BREAKOUT_WINDOW.get(timeframe, 5)
    if index < window + 1:
        return None

    closes = frame["close"]
    highs = frame["high"]
    if float(closes.iloc[index]) >= ceil:
        return None      # still above; nothing has failed yet

    for back in range(1, window + 1):
        at = index - back
        if at < 1:
            break
        if float(closes.iloc[at]) > ceil:
            return {
                "broke_on": str(frame.index[at].date()),
                "bars_held": back,
                "peak": round(float(highs.iloc[at : index + 1].max()), 4),
                "back_below": round(float(closes.iloc[index]), 4),
            }
    return None


def rejected_at_resistance(
    frame: pd.DataFrame, index: int, *, floor: float, ceil: float
) -> bool:
    """A bearish candle that reached into resistance and closed back below.

    The mirror of `bullish_candle`: a shooting star or bearish engulfing where
    a support setup would look for a hammer.
    """
    if index < 1:
        return False

    o, h, l, c = (float(frame[k].iloc[index]) for k in ("open", "high", "low", "close"))
    prev_o = float(frame["open"].iloc[index - 1])
    prev_c = float(frame["close"].iloc[index - 1])

    if h < floor:
        return False

    body = abs(c - o)
    upper_wick = h - max(o, c)
    span = h - l
    if span <= 0:
        return False

    star = upper_wick > HAMMER_WICK_RATIO * max(body, span * 0.05) and c < (l + 0.5 * span)
    engulfing = prev_c > prev_o and c < o and c <= prev_o and o >= prev_c
    return bool((star or engulfing) and c < ceil)
