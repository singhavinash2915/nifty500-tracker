"""Cross-sectional ranking helpers shared by every pillar.

The rule the whole model leans on: metrics are percentile-ranked *within a
peer group*, not across the index. A 14x P/E means different things for a bank
and an FMCG name, and momentum means different things in a defensive sector
than a cyclical one.

Sectors too small to rank inside are pooled into an all-stocks group instead —
ranking a stock against two peers produces noise dressed up as a signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_PEERS = 10
POOLED = "__POOLED__"


def peer_groups(sectors: pd.Series, *, min_peers: int = MIN_PEERS) -> pd.Series:
    """Map each row to the group it should be ranked within."""
    counts = sectors.value_counts()
    thin = set(counts[counts < min_peers].index) | {None, np.nan}
    return sectors.map(lambda s: POOLED if (s in thin or pd.isna(s)) else s)


def percentile(
    values: pd.Series, groups: pd.Series | None = None, *, ascending: bool = True
) -> pd.Series:
    """Percentile rank in 0-100. `ascending=False` means low values score high.

    NaNs stay NaN — a missing metric must not be silently treated as a median
    or as a zero. Groups with a single ranked member score 50, not 100: one
    observation carries no information about relative standing.
    """
    if groups is None:
        return _rank_block(values, ascending=ascending)

    out = pd.Series(np.nan, index=values.index, dtype="float64")
    for _, index in groups.groupby(groups).groups.items():
        out.loc[index] = _rank_block(values.loc[index], ascending=ascending)
    return out


def _rank_block(values: pd.Series, *, ascending: bool) -> pd.Series:
    present = values.dropna()
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    if present.empty:
        return out
    if len(present) == 1:
        out.loc[present.index] = 50.0
        return out
    ranks = present.rank(ascending=ascending, method="average")
    out.loc[present.index] = (ranks - 1.0) / (len(present) - 1.0) * 100.0
    return out


def clamp(series: pd.Series, low: float = 0.0, high: float = 100.0) -> pd.Series:
    return series.clip(lower=low, upper=high)


def band_score(
    values: pd.Series,
    *,
    ideal: tuple[float, float],
    zero_below: float,
    zero_above: float,
) -> pd.Series:
    """Score 100 inside an ideal band, tapering to 0 at the outer bounds.

    Used where the relationship is not monotonic — RSI is the clear case: 65 is
    strength, 85 is exhaustion, and a plain percentile rank would score the
    exhausted stock highest.
    """
    low, high = ideal
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    present = values.notna()

    inside = present & values.between(low, high)
    out[inside] = 100.0

    below = present & (values < low)
    span_below = low - zero_below
    out[below] = ((values[below] - zero_below) / span_below * 100.0) if span_below > 0 else 0.0

    above = present & (values > high)
    span_above = zero_above - high
    out[above] = ((zero_above - values[above]) / span_above * 100.0) if span_above > 0 else 0.0

    return clamp(out)
