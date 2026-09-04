"""O — who has been buying.

Shareholding patterns are the one genuinely India-specific edge in this model.
Every listed company files a quarterly breakdown of who owns it, which is more
than most markets disclose, and two of those lines carry information:

Promoters buying is the strongest single signal in the file. A promoter is the
ultimate insider, cannot trade on unpublished information without SEBI noticing,
and creeping acquisition costs them real money — so an increase is a costly,
regulated, public statement about the next few years. The reverse already has a
hard gate: `redflags.promoter_selling` excludes anyone down more than 3pp over
four quarters. This scores the upside the gate ignores.

FII accumulation is slower and noisier but front-runs re-rating, because foreign
institutions size positions over quarters rather than days and their buying is
what lifts a mid-cap into the indices that force other people to buy it.

DII flows are included at low weight and half-heartedly. Domestic institutional
holding now moves substantially with SIP inflows into index and quasi-index
funds, so a rise can mean a fund manager liked the stock or merely that the
public bought a fund that had to own it. That ambiguity is why it is a
tiebreaker rather than a signal.

Point-in-time
-------------
Shareholding for a quarter is disclosed under LODR within 21 days of quarter
end, and the stored rows carry no filing date of their own. `DISCLOSURE_LAG`
below is what stands in for one; anything reading this for a backtest must
apply it or it will be trading on a filing that did not exist.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .ranking import clamp, peer_groups, percentile

# SEBI LODR Regulation 31: within 21 days of quarter end. Rounded up to a month
# because the deadline is the last permitted date, not the usual one, and
# erring late is the only safe direction for a point-in-time filter.
DISCLOSURE_LAG = timedelta(days=31)

WEIGHTS = {
    "promoter": 40.0,
    "fii": 35.0,
    "dii": 25.0,
}


def disclosed_by(quarter_end: date) -> date:
    """The date a quarter's shareholding may first be assumed public."""
    return quarter_end + DISCLOSURE_LAG


def delta(series: list[float | None], quarters: int) -> float | None:
    """Change in percentage points over `quarters`, or None if unknowable.

    Returned in points, not as a ratio: promoter holding going from 51.0 to
    52.5 is "+1.5pp", and expressing it as +2.9% would invite it to be read as
    a return.
    """
    values = [float(v) for v in series if v is not None and not pd.isna(v)]
    if len(values) < quarters + 1:
        return None
    return values[-1] - values[-(quarters + 1)]


def build_metrics(record: dict) -> dict:
    """Ownership deltas for one company.

    Promoter movement is reported as None where there is no promoter at all —
    ITC, Infosys, HDFC Bank and the rest of the professionally managed names.
    Their promoter line is a constant zero, and scoring a constant would place
    every one of them at the same middling rank for a question that does not
    apply to them. The weight renormalises over what is left instead.
    """
    promoter = record.get("promoter_history", [])
    has_promoter = bool(record.get("has_promoter", False))

    return {
        "promoter_delta_4q": delta(promoter, 4) if has_promoter else None,
        "promoter_delta_1q": delta(promoter, 1) if has_promoter else None,
        "fii_delta_4q": delta(record.get("fii_history", []), 4),
        "fii_delta_2q": delta(record.get("fii_history", []), 2),
        "dii_delta_4q": delta(record.get("dii_history", []), 4),
    }


def component_scores(frame: pd.DataFrame) -> pd.DataFrame:
    groups = peer_groups(frame["sector"])
    out = pd.DataFrame(index=frame.index)

    # The four-quarter view carries most of the weight; the latest quarter is
    # included so a fresh reversal is not averaged away by three stale ones.
    out["promoter"] = _blend(
        percentile(frame["promoter_delta_4q"], groups), 0.65,
        percentile(frame["promoter_delta_1q"], groups), 0.35,
    )
    out["fii"] = _blend(
        percentile(frame["fii_delta_4q"], groups), 0.6,
        percentile(frame["fii_delta_2q"], groups), 0.4,
    )
    out["dii"] = percentile(frame["dii_delta_4q"], groups)

    return out


def _blend(a: pd.Series, wa: float, b: pd.Series, wb: float) -> pd.Series:
    """Weighted mean of two ranks, renormalised when one is missing."""
    weighted = a.fillna(0.0) * wa + b.fillna(0.0) * wb
    available = a.notna().astype("float64") * wa + b.notna().astype("float64") * wb
    return weighted / available.replace(0.0, np.nan)


def score(frame: pd.DataFrame) -> pd.Series:
    """Weighted O in 0-100.

    Deliberately permissive about missing inputs — a company with no promoter
    is scored on its institutional lines alone rather than dropped, because the
    absence of a promoter is a fact about the company, not a gap in the data.
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

    return clamp(weighted / available.replace(0.0, np.nan))
