"""R — earnings revision and acceleration.

The gap this fills
------------------
Q scores what a company *is*: its margins, its returns, its balance sheet.
Those are levels, and levels are largely in the price already — the market has
known for years that Asian Paints earns a high ROCE. What re-rates a stock over
six months is *change*: the quarter where growth stops decelerating, the margin
that turns, the profit that beats what the last four quarters implied.

That is also the one measured problem with the current model. The weight sweep
found quality predicting the next six months negatively (IC -0.041, t = -2.48),
which is what you would expect if a quality score is really a proxy for an
expensive multiple. Revision is the pillar that is supposed to be positive:
post-earnings-announcement drift is the most reliably replicated anomaly there
is, in this market as much as any other.

Standardised unexpected earnings, without analysts
--------------------------------------------------
The classical SUE compares reported profit to the consensus estimate. There is
no free consensus for 500 Indian companies, so this uses the model consensus is
usually benchmarked against anyway — the seasonal random walk with drift, from
Foster (1977) and the Bernard-Thomas drift literature:

    expected_t = actual_{t-4} + mean(actual_{t-i} - actual_{t-i-4})
    SUE_t      = (actual_t - expected_t) / stdev(those same differences)

The prediction is "last year's same quarter, plus however much the year-on-year
gap has typically been running". The surprise is what the company did against
that, divided by how noisy its own history is — so a ₹5 crore beat from a
steady company outranks a ₹50 crore beat from an erratic one, which is the
whole point of standardising.

Seasonality is why the comparison is always to the quarter four back and never
to the previous quarter. A cement company's Q1 is not a worse business than its
Q4; it is the monsoon.

Everything here works on a plain list of quarterly values, oldest first, so it
can be tested against handmade series with no database in the way.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ranking import clamp, peer_groups, percentile

# Four differences need eight quarters. Below that the standard deviation is
# being estimated from two or three numbers and the resulting z-score is noise
# with a decimal point on it.
MIN_QUARTERS_SUE = 8
MIN_QUARTERS_ACCEL = 6

# SUE is unbounded and its tail is fat — a company returning from a loss can
# produce a z-score in the hundreds, which would dominate any percentile rank
# it is averaged into. Winsorised at a level well outside normal results.
SUE_CLIP = 8.0

WEIGHTS = {
    "surprise": 35.0,        # SUE on profit
    "acceleration": 25.0,    # is year-on-year growth speeding up
    "revenue_surprise": 20.0,  # SUE on revenue: harder to manage than profit
    "margin_revision": 10.0,
    "consistency": 10.0,
}


def _clean(series: list[float | None]) -> list[float]:
    """Drop missing quarters, preserving order.

    A gap in the middle is rare and quietly shifts every seasonal comparison by
    one quarter when it happens, so the count checks below are deliberately
    applied to the cleaned list — a series with two holes is treated as short
    rather than as a series with different seasonality.
    """
    return [float(v) for v in series if v is not None and not pd.isna(v)]


def seasonal_differences(values: list[float]) -> list[float]:
    """Year-on-year change for each quarter that has a match four back."""
    return [values[i] - values[i - 4] for i in range(4, len(values))]


def sue(series: list[float | None], *, lookback: int = 4) -> float | None:
    """Standardised unexpected earnings for the latest quarter.

    Positive means the company did better than its own recent trend implied.
    Roughly: above +1 is a genuine beat, above +2 is a large one.
    """
    values = _clean(series)
    if len(values) < MIN_QUARTERS_SUE:
        return None

    diffs = seasonal_differences(values)
    if len(diffs) < 2:
        return None

    # The drift and the noise are both estimated from the quarters *before* the
    # one being judged. Including the latest difference in its own benchmark
    # would shrink every surprise toward zero.
    history = diffs[:-1][-lookback:]
    if len(history) < 2:
        return None

    drift = float(np.mean(history))
    noise = float(np.std(history, ddof=1))
    if noise <= 0:
        return None

    return float(np.clip((diffs[-1] - drift) / noise, -SUE_CLIP, SUE_CLIP))


def growth_acceleration(series: list[float | None]) -> float | None:
    """Change in the year-on-year growth *rate* between the last two quarters.

    +0.10 means growth ran ten percentage points faster this quarter than last.
    A company growing 30% and slowing scores below one growing 12% and speeding
    up, which is the ordering that predicts — the second is where estimates get
    raised.

    Undefined when either base is negative: the growth rate out of a loss has no
    meaning and its change has less.
    """
    values = _clean(series)
    if len(values) < MIN_QUARTERS_ACCEL:
        return None

    def yoy(index: int) -> float | None:
        base = values[index - 4]
        return None if base <= 0 else values[index] / base - 1.0

    latest, previous = yoy(len(values) - 1), yoy(len(values) - 2)
    if latest is None or previous is None:
        return None
    return latest - previous


def margin_revision(opm: list[float | None]) -> float | None:
    """Latest operating margin against the mean of the four before it, in pp."""
    values = _clean(opm)
    if len(values) < 5:
        return None
    return values[-1] - float(np.mean(values[-5:-1]))


def consistency(series: list[float | None], quarters: int = 4) -> float | None:
    """How many of the last `quarters` grew year on year, as a 0-1 fraction.

    A crude measure on purpose. It is here to separate a company on its fourth
    straight good quarter from one that had a single flattering print, and a
    count does that without pretending to more precision than four observations
    can carry.
    """
    values = _clean(series)
    if len(values) < 4 + quarters:
        return None
    recent = seasonal_differences(values)[-quarters:]
    return sum(1 for d in recent if d > 0) / len(recent)


def build_metrics(record: dict) -> dict:
    """The raw revision inputs for one company, from its quarterly history."""
    pat = record.get("quarterly_pat", [])
    revenue = record.get("quarterly_revenue", [])
    opm = record.get("quarterly_opm", [])

    return {
        "sue_pat": sue(pat),
        "sue_revenue": sue(revenue),
        "accel_pat": growth_acceleration(pat),
        "accel_revenue": growth_acceleration(revenue),
        "margin_revision": margin_revision(opm),
        "consistency": consistency(pat),
    }


def component_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol, indexed by symbol, with a `sector` column."""
    groups = peer_groups(frame["sector"])
    out = pd.DataFrame(index=frame.index)

    out["surprise"] = percentile(frame["sue_pat"], groups)
    out["revenue_surprise"] = percentile(frame["sue_revenue"], groups)

    # Profit acceleration is the headline, but a company whose revenue is also
    # accelerating is growing rather than cutting costs, so both are ranked and
    # averaged where present.
    out["acceleration"] = pd.concat(
        [
            percentile(frame["accel_pat"], groups),
            percentile(frame["accel_revenue"], groups),
        ],
        axis=1,
    ).mean(axis=1, skipna=True)

    out["margin_revision"] = percentile(frame["margin_revision"], groups)

    # Already a 0-1 fraction with a natural scale; ranking it would throw away
    # the fact that four of four is good in absolute terms, not just relative.
    out["consistency"] = pd.to_numeric(frame["consistency"], errors="coerce") * 100.0

    return out


def score(frame: pd.DataFrame) -> pd.Series:
    """Weighted R in 0-100, renormalised over whichever inputs exist.

    A company with fewer than eight quarters filed — a recent listing — scores
    nothing rather than a default. There are 500 names in the universe; the
    handful with no history do not need to be forced onto the scale.
    """
    components = component_scores(frame)

    weighted = pd.Series(0.0, index=frame.index)
    available = pd.Series(0.0, index=frame.index)
    for name, weight in WEIGHTS.items():
        # An all-missing component arrives as an object column, where fillna
        # silently downcasts and, in a later pandas, returns something else.
        values = pd.to_numeric(components[name], errors="coerce")
        weighted += values.fillna(0.0) * weight
        available += values.notna().astype("float64") * weight

    total = sum(WEIGHTS.values())
    enough = available >= total * 0.5
    return clamp((weighted / available.replace(0.0, np.nan)).where(enough))
