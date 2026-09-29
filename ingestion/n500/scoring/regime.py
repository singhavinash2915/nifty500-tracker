"""The market backdrop, and how much the ranking has earned against each kind.

A stock picker's list means more in some markets than others, and the question
"the market is down, should I trust the list?" deserves a number rather than a
mood. This measures today's backdrop and puts beside it what the conviction
ranking did in past months with the same backdrop.

The study behind the numbers
----------------------------
38 month-ends from January 2023 to March 2026, about 400 stocks each, scored
with the production `conviction.score` and compared with each stock's return
over the following six months. Months were split by breadth, the share of
stocks above their own 200-day average:

    weak   (< 50%)  16 months  top decile +15.6%  average stock  +9.6%  edge +6.0%
    strong (>= 50%) 22 months  top decile +21.4%  average stock +12.0%  edge +9.4%

The ranking ordered stocks usefully (rank IC above zero) in all 38 months,
including February 2025, when 9% of stocks were above their 200-day average.

Read it with its limits. The 16 weak months come from about three separate
sell-offs (early 2023, early 2025, late 2025 into 2026), and six-month windows
starting a month apart overlap. This says how much weight the list has
deserved in markets like this one. It is not a forecast for the market, and
it is why nothing here changes the weights by regime: three episodes are too
few to fit anything to.

Why breadth, and why equal-weighted
-----------------------------------
Of the backdrop measures tried, breadth divided the months most evenly and the
index level said least about stock selection. It is computed across every
symbol in the price mirror, as the study was, so a number shown today means
the same thing as the numbers it is compared with.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

WEAK_BREADTH = 0.50

STUDY = "38 month-ends, Jan 2023 to Mar 2026"

# Per regime: months in the study, six-month return of the conviction top
# decile, of the average stock, and the share of months the ranking helped.
HISTORY = {
    "weak": {"months": 16, "top": 0.156, "avg": 0.096, "ic_positive": 1.0, "episodes": 3},
    "strong": {"months": 22, "top": 0.214, "avg": 0.120, "ic_positive": 1.0, "episodes": None},
}

# Signals whose value fell to about nothing in weak months, from the same study
# (rank IC, weak months vs strong months, signed as conviction uses them):
#   tm_score         +0.014 vs +0.078   helped in 56% of weak months, 91% of strong
#   value_score      +0.022 vs +0.128   44% vs 86%
#   margin_revision  -0.001 vs +0.053   38% vs 86%
# The price-structure signals held up or strengthened (false_breakout +0.155
# vs +0.107, headroom +0.173 vs +0.198). A rank carried mostly by the three
# below is leaning on what has not worked when the market is weak.
FADING_IN_WEAK = ("tm_score", "value_score", "margin_revision")


@dataclass
class Backdrop:
    date: pd.Timestamp
    breadth: float          # share of stocks above their own 200-day average
    drawdown: float         # equal-weighted index vs its 52-week high
    vs_200dma: float        # equal-weighted index vs its own 200-day average
    return_3m: float        # equal-weighted index over 63 sessions

    @property
    def regime(self) -> str:
        return "weak" if self.breadth < WEAK_BREADTH else "strong"

    def to_row(self) -> dict:
        h = HISTORY[self.regime]
        return {
            "date": self.date.date().isoformat(),
            "breadth": round(self.breadth, 4),
            "drawdown": round(self.drawdown, 4),
            "vs_200dma": round(self.vs_200dma, 4),
            "return_3m": round(self.return_3m, 4),
            "regime": self.regime,
            "hist_months": h["months"],
            "hist_top": h["top"],
            "hist_avg": h["avg"],
            "hist_ic_positive": h["ic_positive"],
            "hist_episodes": h["episodes"],
            "study": STUDY,
        }


def measure(prices: pd.DataFrame) -> Backdrop:
    """Today's backdrop from long-format prices (symbol, date, adj_close).

    Same construction as the study: daily returns clipped at +/-50% so one bad
    print cannot move the index, averaged across every symbol with a price that
    day, and breadth counted only among symbols that traded that day.
    """
    wide = (prices.assign(date=pd.to_datetime(prices["date"]),
                          adj_close=pd.to_numeric(prices["adj_close"], errors="coerce"))
            .pivot(index="date", columns="symbol", values="adj_close").sort_index())
    if len(wide) < 252:
        raise ValueError(f"need 252 sessions for a 52-week high, have {len(wide)}")

    returns = wide.pct_change(fill_method=None).clip(-0.5, 0.5)
    index = (1 + returns.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    above = above_200dma(wide)

    return Backdrop(
        date=wide.index[-1],
        breadth=float(above.iloc[-1].mean()),
        drawdown=float(index.iloc[-1] / index.iloc[-252:].max() - 1),
        vs_200dma=float(index.iloc[-1] / index.iloc[-200:].mean() - 1),
        return_3m=float(index.iloc[-1] / index.iloc[-64] - 1),
    )


def above_200dma(wide: pd.DataFrame) -> pd.DataFrame:
    """1.0 above the 200-day average, 0.0 below, NaN where the stock did not trade.

    Float, never bool. Masking a boolean frame with NaN leaves a mix of bool
    and object columns, and averaging a row across them mixes numpy booleans,
    which numpy adds as a logical OR (True + True is True). On real data that
    reported 40.3% breadth on a day with 42.1%: 302 of 717 traded stocks
    above. The exact failure depends on how pandas lays the frame out and did
    not reproduce on synthetic data, so the guard is on the cause, the dtype.
    """
    return (wide > wide.rolling(200, min_periods=150).mean()).astype(float).where(wide.notna())


def fading_share(points: pd.Series) -> float | None:
    """Share of a stock's lift that comes from signals that fade in weak markets."""
    lifting = points.dropna()
    lifting = lifting[lifting > 0]
    total = float(lifting.sum())
    if total <= 0:
        return None
    return round(float(lifting.reindex(FADING_IN_WEAK).fillna(0).sum()) / total, 3)
