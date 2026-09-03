"""Q — quality and growth.

Weights from the build plan:

    growth          30   3y revenue and PAT CAGR, latest quarter year on year
    profitability   25   ROE, ROCE, margin and margin trend
    balance sheet   20   debt/equity, interest cover, debt direction
    cash quality    15   3y CFO/PAT, positive free cash flow
    valuation       10   PEG, P/E against the stock's own history

Lenders are scored on a different set
-------------------------------------
A bank funds itself with deposits, so debt/equity and interest cover describe
its business model rather than its risk, and Screener publishes no ROCE or
operating margin for one. Scoring a bank on the general set would push all 101
Financial Services names to the bottom of the index — not because they are bad
businesses but because the wrong questions were asked. The financial variant
drops the balance-sheet and cash-quality blocks and redistributes their weight
onto growth and profitability, where the comparison is meaningful.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ranking import clamp, peer_groups, percentile

GENERAL_WEIGHTS = {
    "growth": 30.0,
    "profitability": 25.0,
    "balance_sheet": 20.0,
    "cash_quality": 15.0,
    "valuation": 10.0,
}

# Blocks that describe a lender's funding model rather than its quality are
# dropped, and their weight moves to the questions that still discriminate.
FINANCIAL_WEIGHTS = {
    "growth": 42.0,
    "profitability": 42.0,
    "valuation": 16.0,
}


def cagr(series: list[float | None], years: int) -> float | None:
    """Compound growth over `years`, or None when it cannot be computed.

    A negative or zero starting value makes CAGR meaningless — a company that
    went from a loss to a profit has no meaningful growth *rate*, and pretending
    otherwise produces enormous nonsense numbers that dominate a percentile
    rank.
    """
    values = [v for v in series if v is not None]
    if len(values) < years + 1:
        return None
    start, end = values[-(years + 1)], values[-1]
    if start is None or start <= 0 or end is None or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def yoy(series: list[float | None], periods: int = 4) -> float | None:
    values = [v for v in series if v is not None]
    if len(values) < periods + 1:
        return None
    previous, current = values[-(periods + 1)], values[-1]
    if previous is None or previous <= 0:
        return None
    return current / previous - 1.0


def trend(series: list[float | None], window: int = 3) -> float | None:
    """Direction of the last `window` observations, as a slope per period."""
    values = [v for v in series if v is not None][-window:]
    if len(values) < 2:
        return None
    x = np.arange(len(values), dtype="float64")
    return float(np.polyfit(x, np.asarray(values, dtype="float64"), 1)[0])


def build_metrics(record: dict) -> dict:
    """Derive the raw inputs for one company from its assembled history."""
    revenue = record.get("revenue_history", [])
    pat = record.get("pat_history", [])
    quarterly_pat = record.get("quarterly_pat", [])
    cfo = record.get("cfo_history", [])
    opm = record.get("opm_history", [])
    debt = record.get("debt_history", [])

    cfo_values = [v for v in cfo[-3:] if v is not None]
    pat_values = [v for v in pat[-3:] if v is not None]
    total_pat = sum(pat_values) if pat_values else 0.0

    return {
        "revenue_cagr_3y": cagr(revenue, 3),
        "pat_cagr_3y": cagr(pat, 3),
        "quarter_yoy": yoy(quarterly_pat, 4),
        "roe": record.get("roe"),
        "roce": record.get("roce"),
        "opm": opm[-1] if opm else None,
        "opm_trend": trend(opm),
        "debt_equity": record.get("debt_equity"),
        "interest_cover": record.get("interest_cover"),
        "debt_trend": trend(debt),
        "cfo_to_pat": (sum(cfo_values) / total_pat) if total_pat > 0 and cfo_values else None,
        "fcf_positive": (
            1.0 if record.get("fcf") is not None and record["fcf"] > 0 else 0.0
        ) if record.get("fcf") is not None else None,
        "pe": record.get("pe"),
        "peg": (
            record["pe"] / (record["pat_cagr_3y"] * 100.0)
            if record.get("pe") and record.get("pat_cagr_3y") and record["pat_cagr_3y"] > 0
            else None
        ),
    }


def component_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` is one row per symbol, indexed by symbol, with a `sector` column."""
    groups = peer_groups(frame["sector"])
    out = pd.DataFrame(index=frame.index)

    out["growth"] = _mean(
        percentile(frame["revenue_cagr_3y"], groups),
        percentile(frame["pat_cagr_3y"], groups),
        percentile(frame["quarter_yoy"], groups),
    )

    out["profitability"] = _mean(
        percentile(frame["roe"], groups),
        percentile(frame["roce"], groups),
        percentile(frame["opm"], groups),
        percentile(frame["opm_trend"], groups),
    )

    # Lower debt is better; a falling debt trend is better.
    out["balance_sheet"] = _mean(
        percentile(frame["debt_equity"], groups, ascending=False),
        percentile(frame["interest_cover"], groups),
        percentile(frame["debt_trend"], groups, ascending=False),
    )

    out["cash_quality"] = _mean(
        percentile(frame["cfo_to_pat"], groups),
        frame["fcf_positive"] * 100.0,
    )

    # Cheap on PEG and cheap against the stock's own history both score well.
    out["valuation"] = _mean(
        percentile(frame["peg"], groups, ascending=False),
        percentile(frame["pe"], groups, ascending=False),
    )

    return out


def score(frame: pd.DataFrame) -> pd.Series:
    """Weighted Q in 0-100, using the weight set that fits each company type."""
    components = component_scores(frame)
    is_financial = frame.get("is_financial", pd.Series(False, index=frame.index)).fillna(False)

    result = pd.Series(np.nan, index=frame.index, dtype="float64")

    for financial, weights in ((False, GENERAL_WEIGHTS), (True, FINANCIAL_WEIGHTS)):
        mask = is_financial.astype(bool) == financial
        if not mask.any():
            continue
        block = components.loc[mask]

        weighted = pd.Series(0.0, index=block.index)
        available = pd.Series(0.0, index=block.index)
        for name, weight in weights.items():
            values = block[name]
            weighted += values.fillna(0.0) * weight
            available += values.notna().astype("float64") * weight

        total = sum(weights.values())
        enough = available >= total * 0.5
        result.loc[mask] = (
            weighted / available.replace(0.0, np.nan)
        ).where(enough)

    return clamp(result)


def _mean(*parts: pd.Series) -> pd.Series:
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)
