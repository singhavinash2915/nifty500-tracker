"""RSI divergence, both directions, on any timeframe.

Price makes a lower low and momentum does not: the selling that drove the first
low did not drive the second as hard. The mirror — a higher high on weaker
momentum — is the bearish case. It is the oldest idea in technical analysis and
one of the least carefully measured, which is why this returns a feature rather
than a verdict.

What this adds over `reversal.rsi_divergence`
---------------------------------------------
That function answers a narrower question, deliberately: is *this bar* a lower
low with a higher RSI? It is a trigger for a support setup and it is right to be
strict. Two things make it unusable as a screening feature:

  * it only fires on the bar that makes the low, so a divergence that completed
    three sessions ago is invisible;
  * there is no bearish case at all.

Here the comparison is between the last two *confirmed swing points* rather than
between today and the window minimum. That is the textbook construction and it
is far less jumpy: a swing low is a low with bars either side of it, so it does
not move every time a new bar prints.

Confirmation, and why the index matters
---------------------------------------
A swing is only known to be a swing once the bars after it exist. `Pivot` carries
`confirmed_index` for exactly that reason, and this filters on it rather than on
`index`. Using the pivot's own position would let a backtest see a low several
bars before the market could have — the same look-ahead that made zone respect
score an impossible t of +17 earlier in this project.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .pivots import Pivot, PivotKind, find_pivots

__all__ = ["Divergence", "find", "active", "find_pivots"]

# How far back to look for the pair of swings. A divergence between points six
# months apart is not the same phenomenon as one between points three weeks
# apart, and the second is what a six-month entry cares about.
LOOKBACK = {"daily": 120, "weekly": 52}

# The two swings must be separated by at least this many bars, or a jagged base
# produces a "divergence" between two halves of the same move.
MIN_SEPARATION = {"daily": 5, "weekly": 2}

# Momentum has to differ by more than a rounding error. RSI is a 0-100 scale, so
# two points is a real difference and half a point is noise.
MIN_RSI_GAP = 2.0

# How long a completed divergence stays interesting. Like the other one-bar
# events in this model, treating it as instantaneous makes it useless: a
# divergence that completed last week describes the same situation as one that
# completed today.
MEMORY_BARS = {"daily": 20, "weekly": 4}


@dataclass(frozen=True)
class Divergence:
    kind: str                 # "bullish" | "bearish"
    prior_index: int
    prior_price: float
    prior_rsi: float
    recent_index: int
    recent_price: float
    recent_rsi: float
    confirmed_index: int

    @property
    def rsi_gap(self) -> float:
        """How far momentum diverged, in RSI points. Signed by direction."""
        return abs(self.recent_rsi - self.prior_rsi)


def _swings(
    pivots: list[Pivot], kind: PivotKind, *, index: int, lookback: int
) -> list[Pivot]:
    """Swings of one kind, confirmed by `index` and inside the window."""
    start = index - lookback
    return [
        p for p in pivots
        if p.kind is kind and p.confirmed_index <= index and p.index >= start
    ]


def _at(series: pd.Series, position: int) -> float | None:
    if position < 0 or position >= len(series):
        return None
    value = series.iloc[position]
    return None if pd.isna(value) else float(value)


def find(
    frame: pd.DataFrame,
    rsi: pd.Series,
    index: int,
    *,
    kind: str,
    timeframe: str = "daily",
    pivots: list[Pivot] | None = None,
) -> Divergence | None:
    """The most recent divergence of `kind` completed by bar `index`.

    Bullish compares swing lows: price lower, RSI higher. Bearish compares swing
    highs: price higher, RSI lower. Returns the pair, or None.
    """
    if kind not in ("bullish", "bearish"):
        raise ValueError(f"kind must be bullish or bearish, not {kind!r}")

    pivots = pivots if pivots is not None else find_pivots(frame)
    lookback = LOOKBACK.get(timeframe, 120)
    separation = MIN_SEPARATION.get(timeframe, 5)

    wanted = PivotKind.SPL if kind == "bullish" else PivotKind.SPH
    swings = _swings(pivots, wanted, index=index, lookback=lookback)
    if len(swings) < 2:
        return None

    recent = swings[-1]
    # Walk backwards for the first swing far enough away to be a separate event.
    for prior in reversed(swings[:-1]):
        if recent.index - prior.index < separation:
            continue

        prior_rsi = _at(rsi, prior.index)
        recent_rsi = _at(rsi, recent.index)
        if prior_rsi is None or recent_rsi is None:
            return None
        if abs(recent_rsi - prior_rsi) < MIN_RSI_GAP:
            return None

        if kind == "bullish":
            diverged = recent.price < prior.price and recent_rsi > prior_rsi
        else:
            diverged = recent.price > prior.price and recent_rsi < prior_rsi
        if not diverged:
            return None

        return Divergence(
            kind=kind,
            prior_index=prior.index, prior_price=prior.price, prior_rsi=prior_rsi,
            recent_index=recent.index, recent_price=recent.price, recent_rsi=recent_rsi,
            confirmed_index=recent.confirmed_index,
        )
    return None


def active(
    frame: pd.DataFrame,
    rsi: pd.Series,
    index: int,
    *,
    kind: str,
    timeframe: str = "daily",
    pivots: list[Pivot] | None = None,
) -> bool:
    """Whether a divergence of `kind` completed recently enough to still matter.

    The memory window is the point. A divergence is a two-swing pattern that
    completes on one bar, and scoring only that bar would make it another of the
    one-day flags that churned this model's buy list nine names out of ten a
    month.
    """
    found = find(frame, rsi, index, kind=kind, timeframe=timeframe, pivots=pivots)
    if found is None:
        return False
    return index - found.confirmed_index <= MEMORY_BARS.get(timeframe, 20)
