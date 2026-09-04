"""Turn pivots and volume into rated support zones.

A zone is a *band*, not a line. Price does not turn at 700.00; it turns in the
region around 700, and every touch lands somewhere slightly different. Treating
support as a line makes proximity meaningless and produces zones that are never
quite touched.

Strength comes from what the zone has actually done, not from how it was drawn:

    touches            three or four clean rejections is the sweet spot. One is
                       a guess; seven means the level is being worn down.
    reaction size      average bounce out of the band, in ATR. A level that
                       produced 3% bounces is not the same asset as one that
                       produced 20% bounces.
    rejection quality  long lower wicks and closes back above the band beat
                       flat closes at the low.
    volume             expansion on the bounces, dry-up into the retest.
    freshness          a zone untested since it formed scores highest; one
                       older than three years fades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from .pivots import (
    Pivot, PivotKind, find_pivots, fractal_highs, fractal_lows,
    resistance_pivots, support_pivots,
)


class ZoneSource(str, Enum):
    PIVOT = "pivot"
    CLUSTER = "cluster"
    VOLUME_SHELF = "volume_shelf"


class ZoneKind(str, Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


# How far apart two pivot lows may sit and still be the same zone, in ATR.
CLUSTER_TOLERANCE_ATR = 0.6

# A single pivot still needs width to be a band rather than a line.
MIN_ZONE_WIDTH_ATR = 0.35

# Bars allowed for the bounce out of a zone to develop.
REACTION_WINDOW = {"daily": 20, "weekly": 8}

# A close this far below the floor, in ATR, breaks the zone. Some slippage is
# allowed: a wick through the floor that closes back inside is a rejection,
# which is the opposite of a break.
BREAK_BUFFER_ATR = 0.25

# Touches closer together than this are the same visit, not two tests.
MIN_BARS_BETWEEN_TOUCHES = {"daily": 5, "weekly": 2}

AGE_FADE_BARS = {"daily": 750, "weekly": 156}   # roughly three years


@dataclass
class ZoneEvent:
    index: int
    date: pd.Timestamp
    kind: str                 # touch | rejection | break | reclaim
    reaction_atr: float | None = None
    volume_ratio: float | None = None


@dataclass
class Zone:
    timeframe: str
    source: ZoneSource
    floor: float
    ceil: float
    formed_index: int
    formed_date: pd.Timestamp
    kind: ZoneKind = ZoneKind.SUPPORT
    events: list[ZoneEvent] = field(default_factory=list)
    invalidated_index: int | None = None
    invalidated_date: pd.Timestamp | None = None
    confluence: dict = field(default_factory=dict)
    strength: float | None = None

    @property
    def mid(self) -> float:
        return (self.floor + self.ceil) / 2.0

    @property
    def width(self) -> float:
        return self.ceil - self.floor

    def _upto(self, at_index: int | None) -> list[ZoneEvent]:
        return self.events if at_index is None else [
            e for e in self.events if e.index <= at_index
        ]

    def touches_by(self, at_index: int | None = None) -> list[ZoneEvent]:
        return [e for e in self._upto(at_index) if e.kind in ("touch", "rejection")]

    def rejections_by(self, at_index: int | None = None) -> list[ZoneEvent]:
        return [e for e in self._upto(at_index) if e.kind == "rejection"]

    def breaks_by(self, at_index: int | None = None) -> list[ZoneEvent]:
        return [e for e in self._upto(at_index) if e.kind == "break"]

    def respect_at(self, at_index: int | None = None) -> float | None:
        """Rejections as a share of every decisive test, as of one bar.

        Borrowed from the TradingView script's bounces/(bounces+breaks): a
        level tested five times and held five times is a different proposition
        from one tested five times and broken twice, even though both show
        five touches. Counting only touches flatters the second.

        `at_index` is not optional in spirit. A zone's event list runs to the
        end of the frame it was built from, so the whole-life ratio answers
        "was this level ever broken, including later" — which in a backtest is
        the answer rather than a signal. Measured that way it scored an
        information coefficient of +0.19 at t = +17 with the predicted sign on
        every single rebalance, a result no honest price feature produces.
        Pass the bar being scored; leave it None only where the last bar in the
        frame *is* now.
        """
        rejections = len(self.rejections_by(at_index))
        decisive = rejections + len(self.breaks_by(at_index))
        return rejections / decisive if decisive else None

    @property
    def touches(self) -> list[ZoneEvent]:
        return self.touches_by(None)

    @property
    def rejections(self) -> list[ZoneEvent]:
        return self.rejections_by(None)

    @property
    def breaks(self) -> list[ZoneEvent]:
        return self.breaks_by(None)

    @property
    def respect(self) -> float | None:
        """The whole-life ratio. Correct only when the frame ends today."""
        return self.respect_at(None)

    def is_resistance(self) -> bool:
        return self.kind is ZoneKind.RESISTANCE

    def is_live(self, at_index: int) -> bool:
        if self.formed_index > at_index:
            return False
        return self.invalidated_index is None or self.invalidated_index > at_index

    def contains(self, price: float) -> bool:
        return self.floor <= price <= self.ceil


def cluster_supports(
    pivots: list[Pivot], atr: pd.Series, *, tolerance: float = CLUSTER_TOLERANCE_ATR
) -> list[list[Pivot]]:
    """Group support pivots that sit within `tolerance` ATR of one another.

    ATR-relative rather than percentage-relative on purpose: a 2% band is
    generous for a utility and far too tight for a small-cap that moves 5% a
    day. The tolerance is taken at each pivot's own bar, so a zone formed in a
    calm period is not widened by later turbulence.

    The distance is measured against the cluster's *lowest* member, not the one
    added last. Chaining off the last member is single-linkage clustering, and
    with thirty-odd swing lows it walks a band all the way up the chart: on
    UltraTech it produced a "support zone" 16% wide, which is not a level
    anyone can place a stop against. Anchoring to the floor bounds every zone
    to `tolerance` ATR by construction.
    """
    lows = sorted(support_pivots(pivots), key=lambda p: p.price)
    clusters: list[list[Pivot]] = []

    for pivot in lows:
        local_atr = _atr_at(atr, pivot.index)
        if local_atr is None or local_atr <= 0:
            continue
        if clusters and abs(pivot.price - clusters[-1][0].price) <= tolerance * local_atr:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])

    return clusters


def _atr_at(atr: pd.Series, index: int) -> float | None:
    if index < 0 or index >= len(atr):
        return None
    value = atr.iloc[index]
    return None if pd.isna(value) else float(value)


def cluster_resistances(
    pivots: list[Pivot], atr: pd.Series, *, tolerance: float = CLUSTER_TOLERANCE_ATR
) -> list[list[Pivot]]:
    """The mirror of `cluster_supports`, anchored to the cluster's ceiling.

    Anchoring to the highest member rather than the last bounds the band the
    same way and for the same reason — chaining off the last addition walks a
    zone across the chart.
    """
    highs = sorted(resistance_pivots(pivots), key=lambda p: p.price, reverse=True)
    clusters: list[list[Pivot]] = []

    for pivot in highs:
        local_atr = _atr_at(atr, pivot.index)
        if local_atr is None or local_atr <= 0:
            continue
        if clusters and abs(pivot.price - clusters[-1][0].price) <= tolerance * local_atr:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])

    return clusters


def zone_from_cluster(cluster: list[Pivot], atr: pd.Series) -> Zone | None:
    """A band spanning the cluster, widened to a minimum so it is never a line."""
    prices = [p.price for p in cluster]
    formed = max(cluster, key=lambda p: p.confirmed_index)

    local_atr = _atr_at(atr, formed.confirmed_index)
    if local_atr is None or local_atr <= 0:
        return None

    floor, ceil = min(prices), max(prices)
    minimum = MIN_ZONE_WIDTH_ATR * local_atr
    if ceil - floor < minimum:
        pad = (minimum - (ceil - floor)) / 2.0
        floor, ceil = floor - pad, ceil + pad

    return Zone(
        timeframe="",
        source=ZoneSource.CLUSTER if len(cluster) > 1 else ZoneSource.PIVOT,
        floor=float(floor),
        ceil=float(ceil),
        # Formed when the *last* pivot in the cluster was confirmed — that is
        # the first moment the whole band was knowable.
        formed_index=formed.confirmed_index,
        formed_date=formed.confirmed_date,
    )


def volume_shelves(
    frame: pd.DataFrame,
    *,
    bins: int = 60,
    top: int = 3,
    lookback: int = 500,
    below: float | None = None,
) -> list[tuple[float, float, float]]:
    """High-volume nodes: (floor, ceil, share of traded volume).

    Real transacted supply, which is the strongest form of memory a price level
    has — someone actually owns stock there and remembers what they paid.

    `below` restricts the result to shelves whose floor is under the current
    price. A shelf entirely *above* price is supply waiting to be sold into,
    which is resistance; returning it as support would point the setup at the
    wrong side of the market. The test is on the floor rather than the whole
    band, matching `live_zones_below` — a shelf price is currently sitting on
    straddles the last close, and that is precisely the support case.
    """
    window = frame.tail(lookback)
    if len(window) < 30 or window["volume"].sum() <= 0:
        return []

    low, high = float(window["low"].min()), float(window["high"].max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return []

    edges = np.linspace(low, high, bins + 1)
    # Each bar's volume is spread over the range it traded, rather than dumped
    # entirely at its close — a wide bar genuinely transacted across the range.
    weights = np.zeros(bins)
    for bar_low, bar_high, volume in zip(
        window["low"].to_numpy(), window["high"].to_numpy(), window["volume"].to_numpy()
    ):
        if not np.isfinite(volume) or volume <= 0:
            continue
        start = np.searchsorted(edges, bar_low, side="right") - 1
        stop = np.searchsorted(edges, bar_high, side="left")
        # A bar sitting exactly on the top edge lands both indices at `bins`,
        # which spreads its volume over an empty slice and divides by zero on
        # the way. Numerically it was harmless — an empty slice absorbs the
        # infinity — but it warned on every flat bar at the high, which the
        # delisted names have plenty of, and it silently dropped that bar's
        # volume. Clamping `start` inside the bins guarantees at least one.
        start = min(max(start, 0), bins - 1)
        stop = min(max(stop, start + 1), bins)
        weights[start:stop] += volume / (stop - start)

    total = weights.sum()
    if total <= 0:
        return []

    ranked = np.argsort(weights)[::-1]
    picked: list[int] = []
    for i in ranked:
        if below is not None and edges[i] >= below:
            continue
        picked.append(int(i))
        if len(picked) >= top:
            break

    return [
        (float(edges[i]), float(edges[i + 1]), float(weights[i] / total))
        for i in sorted(picked)
    ]


def annotate_events(
    zone: Zone, frame: pd.DataFrame, atr: pd.Series, *, timeframe: str
) -> Zone:
    """Walk the bars after formation, recording touches, rejections and breaks.

    Mirrored for resistance. A support zone is approached from above and broken
    by a close beneath it; a resistance zone is approached from below and broken
    by a close above. The reaction that matters is likewise inverted — a bounce
    down off resistance is the equivalent of a bounce up off support.
    """
    if zone.is_resistance():
        return _annotate_resistance(zone, frame, atr, timeframe=timeframe)
    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    closes = frame["close"].to_numpy(dtype="float64")
    volumes = frame["volume"].to_numpy(dtype="float64") if "volume" in frame else None
    dates = frame.index

    window = REACTION_WINDOW.get(timeframe, 20)
    spacing = MIN_BARS_BETWEEN_TOUCHES.get(timeframe, 5)

    last_touch = -10**9
    broken_at: int | None = None

    for i in range(zone.formed_index + 1, len(frame)):
        local_atr = _atr_at(atr, i) or zone.width or 1.0

        if broken_at is None and closes[i] < zone.floor - BREAK_BUFFER_ATR * local_atr:
            zone.events.append(ZoneEvent(i, dates[i], "break"))
            zone.invalidated_index, zone.invalidated_date = i, dates[i]
            broken_at = i
            continue

        if broken_at is not None:
            if closes[i] > zone.ceil:
                zone.events.append(ZoneEvent(i, dates[i], "reclaim"))
                broken_at = None
            continue

        entered = lows[i] <= zone.ceil
        if not entered or i - last_touch < spacing:
            continue
        last_touch = i

        stop = min(i + window + 1, len(frame))
        reaction = (highs[i + 1 : stop].max() - zone.ceil) / local_atr if stop > i + 1 else 0.0

        ratio = None
        if volumes is not None and i >= 20:
            recent = np.nanmean(volumes[max(0, i - 19) : i + 1])
            base = np.nanmean(volumes[max(0, i - 99) : i + 1])
            if base and np.isfinite(base) and base > 0:
                ratio = float(recent / base)

        # A wick into the band that closes back above it is a rejection; a
        # close left inside the band is only a touch.
        kind = "rejection" if closes[i] > zone.ceil else "touch"
        zone.events.append(
            ZoneEvent(i, dates[i], kind, reaction_atr=float(reaction), volume_ratio=ratio)
        )

    return zone


def _annotate_resistance(
    zone: Zone, frame: pd.DataFrame, atr: pd.Series, *, timeframe: str
) -> Zone:
    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    closes = frame["close"].to_numpy(dtype="float64")
    volumes = frame["volume"].to_numpy(dtype="float64") if "volume" in frame else None
    dates = frame.index

    window = REACTION_WINDOW.get(timeframe, 20)
    spacing = MIN_BARS_BETWEEN_TOUCHES.get(timeframe, 5)

    last_touch = -10**9
    broken_at: int | None = None

    for i in range(zone.formed_index + 1, len(frame)):
        local_atr = _atr_at(atr, i) or zone.width or 1.0

        if broken_at is None and closes[i] > zone.ceil + BREAK_BUFFER_ATR * local_atr:
            zone.events.append(ZoneEvent(i, dates[i], "break"))
            zone.invalidated_index, zone.invalidated_date = i, dates[i]
            broken_at = i
            continue

        if broken_at is not None:
            if closes[i] < zone.floor:
                zone.events.append(ZoneEvent(i, dates[i], "reclaim"))
                broken_at = None
            continue

        entered = highs[i] >= zone.floor
        if not entered or i - last_touch < spacing:
            continue
        last_touch = i

        stop = min(i + window + 1, len(frame))
        # The reaction is downward: how far price fell away from the band.
        reaction = (zone.floor - lows[i + 1 : stop].min()) / local_atr if stop > i + 1 else 0.0

        ratio = None
        if volumes is not None and i >= 20:
            recent = np.nanmean(volumes[max(0, i - 19) : i + 1])
            base = np.nanmean(volumes[max(0, i - 99) : i + 1])
            if base and np.isfinite(base) and base > 0:
                ratio = float(recent / base)

        # A wick into the band that closes back below it is a rejection.
        kind = "rejection" if closes[i] < zone.floor else "touch"
        zone.events.append(
            ZoneEvent(i, dates[i], kind, reaction_atr=float(reaction), volume_ratio=ratio)
        )

    return zone


def rate_strength(zone: Zone, *, at_index: int, timeframe: str) -> float:
    """0-100, from what the zone has done rather than how it was drawn."""
    touches = zone.touches_by(at_index)
    rejections = zone.rejections_by(at_index)

    # Touch count: peaks at 3-4. One touch is a guess; many touches mean the
    # level is being worn away and is closer to breaking than holding.
    count = len(touches)
    if count == 0:
        touch_score = 45.0        # untested but structurally real
    elif count <= 2:
        touch_score = 70.0
    elif count <= 4:
        touch_score = 100.0
    elif count <= 6:
        touch_score = 70.0
    else:
        touch_score = 40.0

    reactions = [e.reaction_atr for e in touches if e.reaction_atr is not None]
    reaction_score = float(np.clip(np.mean(reactions) / 4.0 * 100.0, 0, 100)) if reactions else 40.0

    quality = (len(rejections) / count * 100.0) if count else 40.0

    # How often a decisive test went the level's way. A zone broken twice out
    # of five tests is weaker than the touch count alone suggests.
    respect = zone.respect_at(at_index)
    respect_score = 50.0 if respect is None else respect * 100.0

    volumes = [e.volume_ratio for e in touches if e.volume_ratio is not None]
    volume_score = float(np.clip((np.mean(volumes) - 0.7) / 0.8 * 100.0, 0, 100)) if volumes else 50.0

    age = at_index - zone.formed_index
    fade = AGE_FADE_BARS.get(timeframe, 750)
    age_score = float(np.clip(100.0 * (1.0 - max(age - fade, 0) / fade), 20, 100))

    strength = (
        touch_score * 0.28
        + reaction_score * 0.20
        + quality * 0.14
        + respect_score * 0.14
        + volume_score * 0.12
        + age_score * 0.12
    )
    # Weekly structure is more durable than daily; the plan weights it roughly
    # double, applied here as a bounded uplift rather than a raw multiplier.
    if timeframe == "weekly":
        strength = min(100.0, strength * 1.15)

    return round(float(strength), 2)


def build_zones(
    frame: pd.DataFrame, atr: pd.Series, *, timeframe: str, at_index: int | None = None
) -> list[Zone]:
    """Every support zone for one symbol on one timeframe."""
    if len(frame) < 30:
        return []
    at_index = len(frame) - 1 if at_index is None else at_index

    # Both sources feed clustering: SPL says which lows the current structure
    # is built on, fractals give the dense coverage a two-year scan needs.
    structural = find_pivots(frame)
    pivots = structural + fractal_lows(frame)
    highs = structural + fractal_highs(frame)
    zones: list[Zone] = []

    for kind, clusters in (
        (ZoneKind.SUPPORT, cluster_supports(pivots, atr)),
        (ZoneKind.RESISTANCE, cluster_resistances(highs, atr)),
    ):
        for cluster in clusters:
            zone = zone_from_cluster(cluster, atr)
            if zone is None or zone.formed_index > at_index:
                continue
            zone.timeframe = timeframe
            zone.kind = kind
            annotate_events(zone, frame, atr, timeframe=timeframe)
            zone.strength = rate_strength(zone, at_index=at_index, timeframe=timeframe)
            zones.append(zone)

    price = float(frame["close"].iloc[at_index])
    shelf_lookback = 500
    # Shelves are dated to the start of the window that produced them, so the
    # event walk can find the touches they have actually taken since.
    shelf_formed = max(0, min(at_index, len(frame) - 1) - shelf_lookback + 1)

    for floor, ceil, share in volume_shelves(frame, lookback=shelf_lookback, below=price):
        zone = Zone(
            timeframe=timeframe,
            source=ZoneSource.VOLUME_SHELF,
            floor=floor,
            ceil=ceil,
            formed_index=shelf_formed,
            formed_date=frame.index[shelf_formed],
            confluence={"volume_share": round(share, 4)},
        )
        annotate_events(zone, frame, atr, timeframe=timeframe)
        # A shelf carries less structural weight than a confirmed pivot, so its
        # rating is capped below what a well-tested pivot zone can reach.
        rated = rate_strength(zone, at_index=at_index, timeframe=timeframe)
        zone.strength = round(min(rated, 20.0 + share * 700.0), 2)
        zones.append(zone)

    return zones


def live_zones_below(zones: list[Zone], price: float, *, at_index: int) -> list[Zone]:
    """Support zones still intact and not above the current price, nearest first."""
    below = [
        z for z in zones
        if z.is_live(at_index) and not z.is_resistance() and z.floor <= price
    ]
    return sorted(below, key=lambda z: price - z.mid)


def live_zones_above(zones: list[Zone], price: float, *, at_index: int) -> list[Zone]:
    """Resistance zones still intact and not below the price, nearest first."""
    above = [
        z for z in zones
        if z.is_live(at_index) and z.is_resistance() and z.ceil >= price
    ]
    return sorted(above, key=lambda z: z.mid - price)
