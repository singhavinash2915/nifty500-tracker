"""Single-bar and two-bar candle shapes, named and measured separately.

The one structural point worth stating up front: **a hammer and a hanging man
are the same candle.** So are an inverted hammer and a shooting star. The shape
carries no direction at all — a small body with a long lower wick means the same
thing wherever it prints, that sellers pushed price down and buyers took it back
before the close. What makes one bullish and the other bearish is *where* it
happened, and that is the caller's business, not the pattern's.

So this module detects shapes and knows nothing about zones. `reversal.py` and
the scoring supply the location. Writing it the other way round — a
`hammer_at_support()` that checked both at once — is how you end up unable to
answer the only question that matters, which is whether the shape adds anything
over the location.

That question is open. Candlestick patterns are the most widely believed and
least well evidenced thing in technical analysis, and this project has now had
two hypotheses come back inverted and one pillar turn out to be a single
subtraction in disguise. Every shape here is therefore exposed to the panel as
its own feature and left out of the score until it has been measured. If they do
nothing, the honest outcome is a table saying so.

Definitions follow the conventional ones. Where a threshold is arbitrary — and
most of them are — it is named rather than inlined, because the numbers are
convention rather than physics and somebody will want to move them.
"""

from __future__ import annotations

import pandas as pd

# A wick this much longer than the body is what makes the shape. 2.0 is the
# textbook figure for hammers; the doji and marubozu thresholds below are the
# usual rules of thumb.
WICK_TO_BODY = 2.0

# The opposite wick has to be small or the candle is a spinning top, which means
# indecision rather than rejection. Expressed against the whole range.
OPPOSITE_WICK_MAX = 0.25

# Body as a share of the range. Below the first it is a doji; above the second
# there is essentially no wick and the bar is a marubozu.
DOJI_BODY_MAX = 0.10
MARUBOZU_BODY_MIN = 0.90


def _parts(frame: pd.DataFrame, index: int) -> tuple[float, float, float, float] | None:
    """open, high, low, close — or None when the bar cannot be read."""
    try:
        o, h, l, c = (float(frame[k].iloc[index]) for k in ("open", "high", "low", "close"))
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (l <= o <= h and l <= c <= h) or h <= l:
        return None
    return o, h, l, c


def shape(frame: pd.DataFrame, index: int) -> dict[str, bool]:
    """Every shape this bar has, as a flat dict.

    A bar can satisfy more than one — a doji is often also a spinning top — so
    these are not exclusive categories and nothing here picks a winner.
    """
    parts = _parts(frame, index)
    if parts is None:
        return {}
    o, h, l, c = parts

    span = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    reference = max(body, span * 0.05)   # a doji body must not divide by ~zero

    return {
        # Long lower wick, body up near the high. Buyers took the bar back.
        "hammer_shape": (
            lower >= WICK_TO_BODY * reference and upper <= OPPOSITE_WICK_MAX * span
        ),
        # The mirror: long upper wick, body down near the low.
        "inverted_hammer_shape": (
            upper >= WICK_TO_BODY * reference and lower <= OPPOSITE_WICK_MAX * span
        ),
        "doji": body <= DOJI_BODY_MAX * span,
        "marubozu": body >= MARUBOZU_BODY_MIN * span,
        "bullish_body": c > o,
        "bearish_body": c < o,
    }


def engulfing(frame: pd.DataFrame, index: int) -> dict[str, bool]:
    """Two-bar engulfing, both directions.

    The body must cover the previous body outright. Wicks are ignored, which is
    the standard reading: the point is that the session opened beyond where the
    last one closed and finished beyond where it opened.
    """
    if index < 1:
        return {"bullish_engulfing": False, "bearish_engulfing": False}
    now, prev = _parts(frame, index), _parts(frame, index - 1)
    if now is None or prev is None:
        return {"bullish_engulfing": False, "bearish_engulfing": False}

    o, _, _, c = now
    po, _, _, pc = prev
    return {
        "bullish_engulfing": pc < po and c > o and c >= po and o <= pc,
        "bearish_engulfing": pc > po and c < o and c <= po and o >= pc,
    }


def piercing(frame: pd.DataFrame, index: int) -> dict[str, bool]:
    """Piercing line and dark cloud cover.

    Weaker relatives of engulfing: the body reclaims more than half of the
    previous one rather than all of it. Included because the half-way rule is
    what most people actually watch, and because it fires more often, which
    matters when the whole point is to measure whether any of it predicts.
    """
    if index < 1:
        return {"piercing_line": False, "dark_cloud": False}
    now, prev = _parts(frame, index), _parts(frame, index - 1)
    if now is None or prev is None:
        return {"piercing_line": False, "dark_cloud": False}

    o, _, _, c = now
    po, _, _, pc = prev
    midpoint = (po + pc) / 2.0
    return {
        # Down bar, then an up bar opening below its low-side and closing back
        # past the midpoint without fully engulfing.
        "piercing_line": pc < po and c > o and o < pc and midpoint < c < po,
        "dark_cloud": pc > po and c < o and o > pc and po < c < midpoint,
    }


def at_zone(frame: pd.DataFrame, index: int, *, floor: float, ceil: float) -> bool:
    """Whether the bar actually traded into the band.

    Location is half of every pattern below, and it is the half people skip. A
    textbook hammer three percent above support is a hammer that happened, not a
    rejection of anything.
    """
    parts = _parts(frame, index)
    if parts is None:
        return False
    _, h, l, _ = parts
    return l <= ceil and h >= floor


def at_support(frame: pd.DataFrame, index: int, *, floor: float, ceil: float) -> dict[str, bool]:
    """Bullish readings: the shape, plus the location that makes it bullish.

    The close has to hold above the floor. A bar that reached into the band and
    closed underneath it is a break in progress however pretty its wick.
    """
    parts = _parts(frame, index)
    if parts is None or not at_zone(frame, index, floor=floor, ceil=ceil):
        return {}
    _, _, _, c = parts
    if c <= floor:
        return {}

    s = shape(frame, index)
    return {
        "hammer_at_support": bool(s.get("hammer_shape")),
        # The one asked for. A failed rally that still closed above support says
        # buyers were willing to chase and sellers could not push it back
        # through — weaker than a hammer on its own, conventionally needing the
        # next bar to confirm, which is why it is measured separately.
        "inverted_hammer_at_support": bool(s.get("inverted_hammer_shape")),
        "doji_at_support": bool(s.get("doji")),
        "bullish_engulfing_at_support": bool(engulfing(frame, index)["bullish_engulfing"]),
        "piercing_at_support": bool(piercing(frame, index)["piercing_line"]),
    }


def at_resistance(frame: pd.DataFrame, index: int, *, floor: float, ceil: float) -> dict[str, bool]:
    """Bearish readings — the same shapes, read the other way up.

    `shooting_star` is the inverted hammer at a high, and `hanging_man` is the
    hammer at a high. Identical candles to the two above; only the location
    differs, which is exactly the claim this module exists to let you test.
    """
    parts = _parts(frame, index)
    if parts is None or not at_zone(frame, index, floor=floor, ceil=ceil):
        return {}
    _, _, _, c = parts
    if c >= ceil:
        return {}      # closed through it: a breakout, not a rejection

    s = shape(frame, index)
    return {
        "shooting_star_at_resistance": bool(s.get("inverted_hammer_shape")),
        "hanging_man_at_resistance": bool(s.get("hammer_shape")),
        "doji_at_resistance": bool(s.get("doji")),
        "bearish_engulfing_at_resistance": bool(engulfing(frame, index)["bearish_engulfing"]),
        "dark_cloud_at_resistance": bool(piercing(frame, index)["dark_cloud"]),
    }


# Every named pattern, so the panel carries a column for each even on bars where
# no zone was in reach. Absent and false have to be the same thing here, or a
# pattern's information coefficient would be computed only over the bars near a
# zone and would not be comparable with anything else.
PANEL_FEATURES = (
    "hammer_at_support",
    "inverted_hammer_at_support",
    "doji_at_support",
    "bullish_engulfing_at_support",
    "piercing_at_support",
    "shooting_star_at_resistance",
    "hanging_man_at_resistance",
    "doji_at_resistance",
    "bearish_engulfing_at_resistance",
    "dark_cloud_at_resistance",
)
