"""Where the stop goes, where the target goes, and why.

The gap this fills
------------------
`support.evaluate` already sets a stop and a target, but only for a stock that
is currently *at* a support zone with a confirmed reversal — two of 494 on a
typical day. Everything else, including both current holdings, gets nothing. So
the question "what stop should I use on this?" had no answer for 492 stocks and
for every position already open, which is most of the times it gets asked.

The stop
--------
Three candidates, in order of how much they actually know:

1. **Below a live support zone.** The best stop, when one exists below the
   price: it is a level the market has already defended, and a close under it
   says the thesis is wrong rather than that the stock wobbled. Set half an ATR
   *below* the floor, never at it — the floor is where everybody else's stop
   sits, and that is exactly the liquidity a move down goes looking for.

2. **Below the last swing low.** Structure without a zone. Weaker, because one
   low is one observation, but it is still a place where something happened.

3. **Volatility.** `price - 2.5 ATR`. Knows nothing about the chart, which is
   the point: it is always available and it cannot be fooled by a level that
   isn't there.

Whichever is chosen, it is never closer than `MIN_STOP_ATR` — a stop inside
ordinary daily noise is not a stop, it is a promise to be taken out for no
reason — and never further than `MAX_STOP_PCT`, past which the position is
carrying so much risk per share that correct sizing makes it too small to matter.

The target, and an honest problem with it
------------------------------------------
The obvious target is the next resistance band, and that is what this returns.
But the held-out test says something awkward about it: stocks pressed *under*
overhead resistance went on to beat the market, and stocks that broke a level
and fell back beat it by more. Out of sample, `headroom` scored +0.167 and
`false_breakout` +0.149. The straightforward reading is that selling into the
first resistance is selling exactly the setups that worked.

So the target here is deliberately labelled a *scaling* point rather than an
exit. Take something off where the level is, keep the rest with a trailing stop,
and let the position prove the evidence right or wrong. A single fixed target
would cap precisely the trades that pay for all the others — with a 17% hit rate
for a 25% move, the arithmetic only works if the winners are allowed to run.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

# A stop inside normal daily movement will be hit by normal daily movement.
MIN_STOP_ATR = 1.5

# Set under the zone floor rather than at it: the floor is where the crowd's
# stops sit, and a move down goes looking for them.
ZONE_BUFFER_ATR = 0.5

# The fallback when the chart offers nothing. Wide enough to survive a bad week,
# tight enough that risk-based sizing still leaves a position worth holding.
VOLATILITY_STOP_ATR = 2.5

# Past this the stock is too volatile to hold at a sensible size for this
# strategy — 1% of the book risked over a 30% stop is a 3% position, which
# cannot move the portfolio even when it works.
MAX_STOP_PCT = 0.30

# No single position gets more of the account than this, whatever the stop says.
#
# Risk-unit sizing has a failure mode that only appears once it is run down a
# ranked list rather than applied to one idea. Shares = unit / (price - stop),
# so a stop 4% away buys a position worth 25% of capital for the same 1% of
# risk; the first three names on a shortlist then consume the whole account.
#
# The arithmetic is right and the conclusion is wrong, because it assumes the
# stop is the only way to lose. A gap through it on bad news does not respect
# the level, and a 25% position gapping 15% loses 3.75% of capital on a trade
# sized to risk 1%. So the position is capped and the risk taken falls below a
# full unit, which is the correct trade-off rather than a compromise.
MAX_POSITION_PCT = 0.10

# Move the stop to breakeven once the position is up this many multiples of its
# initial risk. Standard practice and the one adjustment that is nearly always
# right: a position that can no longer lose money changes what you can do with
# the rest of the book.
BREAKEVEN_AT_R = 1.0


@dataclass(frozen=True)
class Plan:
    stop: float
    stop_basis: str
    stop_pct: float                  # distance from the current price, as a fraction
    target: float | None = None
    target_basis: str | None = None
    reward_risk: float | None = None
    headroom: float | None = None
    # Only meaningful for a position already open.
    r_multiple: float | None = None
    move_stop_to: float | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _clamp(stop: float, price: float, atr: float) -> tuple[float, str | None]:
    """Pull a stop back inside the sane band, saying so when it moves."""
    floor = price * (1.0 - MAX_STOP_PCT)
    ceiling = price - MIN_STOP_ATR * atr

    if stop > ceiling:
        return ceiling, f"widened to {MIN_STOP_ATR} ATR — anything tighter is daily noise"
    if stop < floor:
        return floor, f"capped at {MAX_STOP_PCT:.0%} — a wider stop makes the position too small to matter"
    return stop, None


def suggest_stop(
    price: float,
    atr: float,
    *,
    zone_floor: float | None = None,
    swing_low: float | None = None,
) -> tuple[float, str, str | None]:
    """The stop, what it is based on, and any note about being adjusted."""
    if atr <= 0 or price <= 0:
        raise ValueError("price and atr must be positive")

    basis = "volatility"
    stop = price - VOLATILITY_STOP_ATR * atr

    # A zone only helps if it is actually below the price. One above is a
    # resistance band the caller has handed over by mistake.
    if zone_floor is not None and zone_floor < price:
        candidate = zone_floor - ZONE_BUFFER_ATR * atr
        if candidate > price * (1.0 - MAX_STOP_PCT):
            stop, basis = candidate, "support zone"

    elif swing_low is not None and swing_low < price:
        candidate = swing_low - ZONE_BUFFER_ATR * atr
        if candidate > price * (1.0 - MAX_STOP_PCT):
            stop, basis = candidate, "swing low"

    stop, note = _clamp(stop, price, atr)
    return round(stop, 4), basis, note


def build(
    price: float,
    atr: float,
    *,
    zone_floor: float | None = None,
    swing_low: float | None = None,
    resistance: float | None = None,
    entry_price: float | None = None,
) -> Plan:
    """A complete plan for one stock, held or merely considered.

    `entry_price` turns it from a proposal into a report on a live position:
    the R multiple and the breakeven trigger only mean something once there is
    a cost basis to measure against.
    """
    stop, basis, note = suggest_stop(
        price, atr, zone_floor=zone_floor, swing_low=swing_low
    )
    risk = price - stop
    if risk <= 0:
        return Plan(stop=stop, stop_basis=basis, stop_pct=0.0,
                    note="price is already at or below any sensible stop")

    # A band below the price is not overhead and not a target — the caller
    # has handed over a support zone, or price has already traded through it.
    above = resistance if resistance is not None and resistance > price else None

    reward_risk = headroom = None
    if above is not None:
        reward_risk = round((above - price) / risk, 2)
        headroom = round(above / price - 1.0, 4)

    r_multiple = move_stop_to = None
    if entry_price and entry_price > 0:
        # Measured against the risk taken at entry, not the risk from here —
        # "up 2R" has to mean two of the units you actually committed.
        entry_risk = entry_price - stop
        if entry_risk > 0:
            r_multiple = round((price - entry_price) / entry_risk, 2)
            if r_multiple >= BREAKEVEN_AT_R and stop < entry_price:
                move_stop_to = round(entry_price, 4)

    return Plan(
        stop=stop,
        stop_basis=basis,
        stop_pct=round(risk / price, 4),
        target=None if above is None else round(above, 4),
        target_basis=None if above is None else "next resistance band",
        reward_risk=reward_risk,
        headroom=headroom,
        r_multiple=r_multiple,
        move_stop_to=move_stop_to,
        note=note,
    )


def quantity_for(
    portfolio_value: float,
    entry: float,
    stop: float,
    *,
    risk_pct: float = 0.01,
    max_position_pct: float = MAX_POSITION_PCT,
) -> int:
    """Shares to buy so that being wrong costs `risk_pct` of capital.

    The single most useful line in this module, and the one most often skipped.
    Sizing by rupees makes every position an equal *bet size* and a wildly
    unequal *bet*: two of the current holdings are within a third of each other
    in value while one risks five times as much to its stop. Sizing by risk makes
    a wide stop produce a small position automatically, which is the behaviour
    you want and will not reliably do by hand.

    Two limits, and whichever binds first wins. `max_position_pct` is the one
    that is easy to forget and it matters most exactly where the signal is
    strongest — a tight stop is what a stock pressed against support or
    resistance produces, so the names the model likes are the ones risk-unit
    sizing wants to buy enormous amounts of.
    """
    risk_per_share = entry - stop
    if risk_per_share <= 0 or portfolio_value <= 0 or entry <= 0:
        return 0
    by_risk = (portfolio_value * risk_pct) // risk_per_share
    by_cap = (portfolio_value * max_position_pct) // entry
    return int(min(by_risk, by_cap))
