"""SPL / SPH structural pivots — a Python port of structural_poc.pine.

Ported deliberately rather than reinvented, so the zones this screener draws
are the same ones the TradingView chart shows. A second, subtly different
definition of "support" would make every disagreement between screen and chart
impossible to debug.

The algorithm, from the Pine source:

  * Start at an anchor bar, looking for an SPL.
  * A bar *qualifies* for the SPL search when `high > anchor.high` and
    `close > anchor.close`. Qualifying bars need not be consecutive, and the
    counter does not reset on non-qualifying bars.
  * On the second qualifying bar (Bar2) an SPL is marked at the lowest low of
    the running range. Bar2 becomes the new anchor, the range resets, and the
    search flips to SPH.
  * SPH is the mirror image: `low < anchor.low` and `close < anchor.close`,
    marked at the highest high of the range.
  * SPL and SPH strictly alternate.

Point-in-time honesty
---------------------
A pivot sits at an *earlier* bar than the one that confirms it. The SPL low
happened days before Bar2 proved it was a pivot. Every pivot therefore records
`confirmed_index` alongside its own position, and anything historical — the
backtest above all — must filter on the confirmation, never on the pivot date.
Using the pivot date is exactly the look-ahead bias that `filed_on` guards
against on the fundamentals side.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class PivotKind(str, Enum):
    SPL = "spl"      # structural low — support
    SPH = "sph"      # structural high — resistance


@dataclass(frozen=True)
class Pivot:
    kind: PivotKind
    index: int              # bar position of the pivot itself
    price: float
    confirmed_index: int    # bar position of Bar2, which proved it
    date: pd.Timestamp | None = None
    confirmed_date: pd.Timestamp | None = None

    @property
    def bars_to_confirm(self) -> int:
        return self.confirmed_index - self.index


QUALIFYING_BARS = 2

# The Pine indicator anchors at a user-chosen recent time and runs forward on a
# 1-hour chart. Pointed at two years of daily bars from bar zero it stalls: once
# the anchor lands on an extreme bar, nothing satisfies `high > anchor.high and
# close > anchor.close` again and the machine emits nothing for hundreds of
# bars. Measured on real data it produced 0-6 pivots over 517 sessions, and
# AMBUJACEM produced none at all.
#
# Re-anchoring after a stall keeps the local semantics intact while letting the
# machine cover a long history. This is a deliberate departure from the Pine,
# recorded here because it is the one place the port is not literal.
DEFAULT_RESTART_AFTER = 60


def find_pivots(
    frame: pd.DataFrame,
    *,
    start: int = 0,
    restart_after: int | None = DEFAULT_RESTART_AFTER,
) -> list[Pivot]:
    """Run the SPL/SPH state machine over an OHLC frame.

    `frame` must carry high/low/close and be sorted ascending. `start` is the
    anchor bar. `restart_after` re-anchors when no pivot has confirmed for that
    many bars; pass None for the literal Pine behaviour.
    """
    required = {"high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"frame needs {sorted(required)}")
    if len(frame) <= start + 1:
        return []

    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    closes = frame["close"].to_numpy(dtype="float64")
    dates = frame.index

    anchor_high = highs[start]
    anchor_low = lows[start]
    anchor_close = closes[start]

    looking_for_spl = True
    qualifying = 0

    # The Pine initialises the range from the anchor bar, then on every later
    # pivot resets it to na — so subsequent ranges begin at anchor+1. That
    # asymmetry is reproduced rather than tidied away: it is what the chart on
    # screen actually draws.
    range_low, range_low_index = lows[start], start
    range_high, range_high_index = highs[start], start

    pivots: list[Pivot] = []
    last_progress = start

    for i in range(start + 1, len(frame)):
        if restart_after is not None and i - last_progress > restart_after:
            # Stalled. Re-anchor here and start looking again from scratch.
            anchor_high, anchor_low, anchor_close = highs[i], lows[i], closes[i]
            qualifying = 0
            range_low, range_low_index = lows[i], i
            range_high, range_high_index = highs[i], i
            last_progress = i
            continue

        # Running extremes update before the state machine, on every bar.
        if range_low is None or lows[i] < range_low:
            range_low, range_low_index = lows[i], i
        if range_high is None or highs[i] > range_high:
            range_high, range_high_index = highs[i], i

        if looking_for_spl:
            qualifies = highs[i] > anchor_high and closes[i] > anchor_close
        else:
            qualifies = lows[i] < anchor_low and closes[i] < anchor_close

        if not qualifies:
            continue

        qualifying += 1
        if qualifying < QUALIFYING_BARS:
            continue

        if looking_for_spl:
            pivot_index, pivot_price = range_low_index, range_low
            kind = PivotKind.SPL
        else:
            pivot_index, pivot_price = range_high_index, range_high
            kind = PivotKind.SPH

        pivots.append(
            Pivot(
                kind=kind,
                index=pivot_index,
                price=float(pivot_price),
                confirmed_index=i,
                date=dates[pivot_index],
                confirmed_date=dates[i],
            )
        )

        # Bar2 becomes the new anchor; the range restarts from the next bar.
        anchor_high, anchor_low, anchor_close = highs[i], lows[i], closes[i]
        last_progress = i
        qualifying = 0
        range_low = range_low_index = None
        range_high = range_high_index = None
        looking_for_spl = not looking_for_spl

    return pivots


def support_pivots(pivots: list[Pivot]) -> list[Pivot]:
    return [p for p in pivots if p.kind is PivotKind.SPL]


def resistance_pivots(pivots: list[Pivot]) -> list[Pivot]:
    return [p for p in pivots if p.kind is PivotKind.SPH]


def visible_at(pivots: list[Pivot], bar_index: int) -> list[Pivot]:
    """Pivots that were already confirmed as of `bar_index`.

    The filter every historical query must go through.
    """
    return [p for p in pivots if p.confirmed_index <= bar_index]


# --- conventional swing fractals ------------------------------------------

def fractal_lows(frame: pd.DataFrame, *, span: int = 5) -> list[Pivot]:
    """Lows with `span` higher lows on each side.

    The SPL/SPH machine tracks recent structure and is sparse by design. Zone
    clustering needs a dense, unconditional set of swing points across the whole
    history, which is what a fractal gives. The two are complementary: fractals
    say where price has turned, SPL/SPH says which of those turns the current
    structure is built on.

    Confirmation lags by `span` bars — a low is not a fractal until `span` bars
    have printed above it — and that lag is recorded, not glossed over.
    """
    return _fractals(frame, span=span, kind=PivotKind.SPL)


def fractal_highs(frame: pd.DataFrame, *, span: int = 5) -> list[Pivot]:
    return _fractals(frame, span=span, kind=PivotKind.SPH)


def _fractals(frame: pd.DataFrame, *, span: int, kind: PivotKind) -> list[Pivot]:
    column = "low" if kind is PivotKind.SPL else "high"
    values = frame[column].to_numpy(dtype="float64")
    dates = frame.index
    out: list[Pivot] = []

    for i in range(span, len(values) - span):
        window = values[i - span : i + span + 1]
        centre = values[i]
        if kind is PivotKind.SPL:
            is_pivot = centre == window.min() and (window > centre).sum() >= span
        else:
            is_pivot = centre == window.max() and (window < centre).sum() >= span
        if not is_pivot:
            continue
        confirmed = i + span
        out.append(
            Pivot(
                kind=kind,
                index=i,
                price=float(centre),
                confirmed_index=confirmed,
                date=dates[i],
                confirmed_date=dates[confirmed],
            )
        )
    return out
