"""V — value.

Weights from the build plan:

    multiples          35   P/E, P/B, EV/EBITDA, EV/Sales
    own-history discount 25 how cheap against the stock's own 5-year median
    earnings yield     20   against the 10-year government bond
    turnaround         12   two straight quarters of expanding margin
    dividend yield      8

Every multiple is ranked inside its sector, which is the whole point. A 14x
P/E is expensive for a public-sector bank and cheap for an FMCG franchise, and
a screener that ranks the two on the same axis will hand you the cheapest
sector in the market every single time, dressed up as stock selection.

The own-history block is the part that survives a sector re-rating: a stock at
half its own five-year median multiple is cheap in a way that does not depend
on comparing it to anyone else.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ranking import clamp, peer_groups, percentile

WEIGHTS = {
    "multiples": 35.0,
    "own_history": 25.0,
    "earnings_yield": 20.0,
    "turnaround": 12.0,
    "dividend": 8.0,
}

# The 10-year government bond, as the risk-free comparison for earnings yield.
# A constant is honest here as long as it is labelled: it moves slowly, and a
# live feed can replace it without touching the scoring.
GSEC_10Y_YIELD = 6.5


def component_scores(frame: pd.DataFrame) -> pd.DataFrame:
    groups = peer_groups(frame["sector"])
    out = pd.DataFrame(index=frame.index)

    # Cheaper is better throughout, hence ascending=False.
    out["multiples"] = _mean(
        percentile(frame["pe"], groups, ascending=False),
        percentile(frame["pb"], groups, ascending=False),
        percentile(frame["ev_ebitda"], groups, ascending=False),
        percentile(frame["ev_sales"], groups, ascending=False),
    )

    # A stock at half its own five-year median is cheap regardless of what its
    # sector is doing.
    discount = 1.0 - (frame["pe"] / frame["pe_5y_median"])
    out["own_history"] = clamp(discount * 200.0 + 50.0)

    earnings_yield = 100.0 / frame["pe"].where(frame["pe"] > 0)
    spread = earnings_yield - GSEC_10Y_YIELD
    out["earnings_yield"] = clamp(spread * 12.0 + 50.0)

    # Two consecutive quarters of margin expansion off a low base.
    out["turnaround"] = (
        frame["margin_expanding"].fillna(False).astype(bool).map({True: 100.0, False: 25.0})
    ).astype("float64")

    out["dividend"] = percentile(frame["dividend_yield"], groups)

    return out


def score(frame: pd.DataFrame) -> pd.Series:
    components = component_scores(frame)

    weighted = pd.Series(0.0, index=frame.index)
    available = pd.Series(0.0, index=frame.index)
    for name, weight in WEIGHTS.items():
        values = components[name]
        weighted += values.fillna(0.0) * weight
        available += values.notna().astype("float64") * weight

    total = sum(WEIGHTS.values())
    enough = available >= total * 0.5
    result = (weighted / available.replace(0.0, np.nan)).where(enough)
    return clamp(result)


def margin_expanding(opm_quarters: list[float | None]) -> bool:
    """Two consecutive quarters of margin expansion."""
    values = [v for v in opm_quarters[-3:] if v is not None]
    if len(values) < 3:
        return False
    return values[2] > values[1] > values[0]


def _mean(*parts: pd.Series) -> pd.Series:
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)
