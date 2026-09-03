"""T-S — the support-reversal setup score.

Weights from the build plan:

    zone strength           26
    reversal confirmation   26
    MTF confluence          16
    proximity               16
    reward-to-risk          16

The gates matter more than the weights. Proximity to support must never on its
own produce a buy, because a falling stock is near support the entire way down.

    fundamentals    T-S is only computed when Q >= 60 with no red flag. A weak
                    business at support is a cheaper weak business, and it will
                    keep getting cheaper. (Q arrives in phase 4; until then the
                    gate is configurable and open, and rows are marked as
                    ungated so nothing pretends to have passed a check that
                    has not run.)
    confirmation    without a trigger the row is `watching`, capped at 55, and
                    can never reach the top decile.
    structure       lower highs after a change of character caps it at 45 until
                    a higher low or a bullish divergence prints.
    knife           three straight down bars, or a drop beyond 2.5 ATR, forces
                    a wait for stabilisation.
    zone intact     a close below the floor invalidates the zone; the engine
                    re-anchors to the next one down rather than averaging into
                    a broken level.
    headroom        the next resistance must be at least 25% away, or the stock
                    cannot reach the target this whole tracker is built around.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..zones.build import Zone, live_zones_below
from ..zones.pivots import Pivot, PivotKind, fractal_highs
from ..zones.reversal import (
    Confirmation,
    falling_knife,
    making_lower_highs,
    stabilised,
)

WEIGHTS = {
    "zone_strength": 26.0,
    "confirmation": 26.0,
    "confluence": 16.0,
    "proximity": 16.0,
    "reward_risk": 16.0,
}

WATCHING_CAP = 55.0
DOWNTREND_CAP = 45.0
KNIFE_CAP = 30.0

# Inside the zone, or within half an ATR of it, is the setup. Beyond two ATR it
# is not a setup yet, however good the zone.
PROXIMITY_FULL_ATR = 0.5
PROXIMITY_ZERO_ATR = 2.0

# Below this the trade is not worth taking whatever the chart looks like.
MIN_REWARD_RISK = 2.5
FULL_REWARD_RISK = 5.0

# The stop sits this far under the zone floor, so an ordinary wick through the
# band does not take the position out.
STOP_BUFFER_ATR = 0.5

# ...and never closer to the entry than this. When price sits at the top of a
# tight band the zone floor can be 2% away, which produces a headline 23:1
# reward-to-risk that ordinary noise would stop out within days. A stop that
# cannot survive normal volatility is not a stop.
MIN_STOP_ATR = 1.5

# The tracker exists to find 25% moves; a stock walled in below that cannot
# deliver one.
MIN_HEADROOM = 0.25

QUALITY_GATE = 60.0


@dataclass
class SupportSetup:
    score: float | None = None
    status: str = "none"              # none | watching | triggered
    zone: Zone | None = None
    stop: float | None = None
    target: float | None = None
    reward_risk: float | None = None
    headroom: float | None = None
    components: dict = field(default_factory=dict)
    confirmation: Confirmation | None = None
    caps: list[str] = field(default_factory=list)
    reason: str | None = None


# A swing high has to be this prominent to count as major resistance, and be
# clustered with at least one other to show price was actually turned there.
RESISTANCE_SPAN = 10
RESISTANCE_MIN_TOUCHES = 2
RESISTANCE_CLUSTER_ATR = 0.6


def next_resistance(
    pivots: list[Pivot],
    price: float,
    *,
    at_index: int,
    frame: pd.DataFrame,
    atr_value: float,
) -> float | None:
    """The nearest level that has actually turned price back, not the nearest tick up.

    Using every minor swing high makes this useless: the closest one is
    typically 5-12% away, so a 25% headroom test would reject the entire
    market. What matters is a level price was rejected from more than once —
    so highs are clustered within 0.6 ATR and a cluster needs two members to
    count as major. Failing that, the widest single swing, then the 52-week
    high.
    """
    above = sorted(
        p.price
        for p in fractal_highs(frame, span=RESISTANCE_SPAN)
        if p.confirmed_index <= at_index and p.price > price
    )
    # Confirmed SPH structure counts too, on the same footing.
    above += sorted(
        p.price
        for p in pivots
        if p.kind is PivotKind.SPH and p.confirmed_index <= at_index and p.price > price
    )
    above.sort()

    if above and atr_value > 0:
        clusters: list[list[float]] = []
        for value in above:
            if clusters and value - clusters[-1][-1] <= RESISTANCE_CLUSTER_ATR * atr_value:
                clusters[-1].append(value)
            else:
                clusters.append([value])
        major = [min(c) for c in clusters if len(c) >= RESISTANCE_MIN_TOUCHES]
        if major:
            return float(min(major))
        return float(min(c[0] for c in clusters))

    if above:
        return float(above[0])

    window = frame["high"].iloc[max(0, at_index - 251) : at_index + 1]
    high = float(window.max()) if len(window) else np.nan
    return high if np.isfinite(high) and high > price else None


def _proximity_score(distance_atr: float) -> float:
    if distance_atr <= PROXIMITY_FULL_ATR:
        return 100.0
    if distance_atr >= PROXIMITY_ZERO_ATR:
        return 0.0
    span = PROXIMITY_ZERO_ATR - PROXIMITY_FULL_ATR
    return float(100.0 * (PROXIMITY_ZERO_ATR - distance_atr) / span)


def _reward_risk_score(ratio: float | None) -> float:
    if ratio is None or ratio < MIN_REWARD_RISK:
        return 0.0
    if ratio >= FULL_REWARD_RISK:
        return 100.0
    span = FULL_REWARD_RISK - MIN_REWARD_RISK
    return float(100.0 * (ratio - MIN_REWARD_RISK) / span)


def _confluence_score(zone: Zone, weekly_zones: list[Zone], extras: dict) -> float:
    """Weekly agreement first, then moving averages and Fibonacci."""
    score = 0.0
    overlapping = [
        z for z in weekly_zones if z.floor <= zone.ceil and z.ceil >= zone.floor
    ]
    if overlapping:
        score += 55.0
        zone.confluence["weekly_zone"] = True
    if extras.get("near_long_ma"):
        score += 25.0
        zone.confluence["long_ma"] = True
    if extras.get("near_fib"):
        score += 20.0
        zone.confluence["fib"] = extras["near_fib"]
    return float(min(score, 100.0))


def evaluate(
    *,
    frame: pd.DataFrame,
    index: int,
    price: float,
    atr: pd.Series,
    zones: list[Zone],
    weekly_zones: list[Zone],
    pivots: list[Pivot],
    confirmation: Confirmation,
    extras: dict | None = None,
    quality_score: float | None = None,
    quality_gate: bool = True,
    hard_excluded: bool = False,
) -> SupportSetup:
    """Score one symbol's support setup as of `index`."""
    extras = extras or {}
    setup = SupportSetup()

    if hard_excluded:
        # A red flag removes the business from consideration entirely; no chart
        # pattern rescues promoters selling into a pledge or profit that never
        # becomes cash.
        setup.reason = "excluded by a red flag"
        return setup

    if quality_gate:
        if quality_score is None:
            setup.reason = "quality score not yet available"
            return setup
        if quality_score < QUALITY_GATE:
            setup.reason = f"quality {quality_score:.0f} below the {QUALITY_GATE:.0f} gate"
            return setup

    local_atr = atr.iloc[index] if index < len(atr) else np.nan
    if pd.isna(local_atr) or float(local_atr) <= 0:
        setup.reason = "no ATR"
        return setup
    local_atr = float(local_atr)

    candidates = live_zones_below(zones, price, at_index=index)
    if not candidates:
        setup.reason = "no live zone below price"
        return setup

    zone = candidates[0]
    setup.zone = zone

    # Distance to the band, zero when price is inside it.
    distance = 0.0 if zone.contains(price) else max(price - zone.ceil, 0.0)
    distance_atr = distance / local_atr
    if distance_atr >= PROXIMITY_ZERO_ATR:
        setup.reason = f"{distance_atr:.1f} ATR above the zone — not a setup yet"
        return setup

    stop = min(
        zone.floor - STOP_BUFFER_ATR * local_atr,
        price - MIN_STOP_ATR * local_atr,
    )
    resistance = next_resistance(
        pivots, price, at_index=index, frame=frame, atr_value=local_atr
    )
    setup.stop = round(stop, 4)
    setup.target = None if resistance is None else round(resistance, 4)

    risk = price - stop
    if risk <= 0:
        setup.reason = "price already below the stop"
        return setup

    if resistance is not None:
        setup.reward_risk = round((resistance - price) / risk, 2)
        setup.headroom = round(resistance / price - 1.0, 4)

    if setup.headroom is not None and setup.headroom < MIN_HEADROOM:
        setup.reason = (
            f"only {setup.headroom:.0%} to the next resistance — cannot reach the target"
        )
        return setup

    components = {
        "zone_strength": zone.strength if zone.strength is not None else 50.0,
        "confirmation": _confirmation_score(confirmation),
        "confluence": _confluence_score(zone, weekly_zones, extras),
        "proximity": _proximity_score(distance_atr),
        "reward_risk": _reward_risk_score(setup.reward_risk),
    }
    setup.components = {k: round(float(v), 2) for k, v in components.items()}
    setup.confirmation = confirmation

    raw = sum(components[name] * weight for name, weight in WEIGHTS.items())
    score = raw / sum(WEIGHTS.values())

    # --- caps, applied in order of severity ---------------------------------
    caps: list[str] = []

    if falling_knife(frame, atr, index) and not stabilised(frame, index):
        score = min(score, KNIFE_CAP)
        caps.append("falling knife — waiting for stabilisation")

    if making_lower_highs(frame, index) and not (
        confirmation.rsi_divergence or _higher_low(frame, index)
    ):
        score = min(score, DOWNTREND_CAP)
        caps.append("lower highs — needs a higher low or a bullish divergence")

    if confirmation.triggered:
        setup.status = "triggered"
    else:
        setup.status = "watching"
        score = min(score, WATCHING_CAP)
        caps.append("no reversal confirmation yet")

    setup.caps = caps
    setup.score = round(float(np.clip(score, 0.0, 100.0)), 2)
    return setup


def _confirmation_score(confirmation: Confirmation) -> float:
    """More independent signals agreeing is stronger, with rapid diminishing
    returns — the first is worth far more than the fourth."""
    return float(min(100.0, confirmation.count * 45.0))


def _higher_low(frame: pd.DataFrame, index: int, *, bars: int = 40) -> bool:
    start = max(0, index - bars)
    lows = frame["low"].iloc[start : index + 1]
    if len(lows) < 20:
        return False
    half = len(lows) // 2
    return float(lows.iloc[half:].min()) > float(lows.iloc[:half].min())
