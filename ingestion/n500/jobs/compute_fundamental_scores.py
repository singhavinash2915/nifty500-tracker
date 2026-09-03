"""Job: turn stored fundamentals into the Q and V scores, and the red flags.

    python -m n500.jobs.compute_fundamental_scores --dry-run

Point-in-time
-------------
Only rows with `filed_on <= as_of` are read. Q2 results for the quarter ending
30 September are filed in early November; using period_end would trade on
information nobody had. The filing dates here are conservative estimates from
the SEBI deadline, which errs late — the safe direction.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import numpy as np
import pandas as pd

from ..db import Db, run
from ..scoring import quality, redflags, value

JOB = "compute_fundamental_scores"


# NSE's own sector label. Row shape alone is not enough: only 11 of the 19
# Financial Services names in the first sample reported with a lender's P&L,
# so eight NBFCs — 360ONE, ANGELONE, BAJAJFINSV among them — were scored as
# manufacturers and four were excluded outright for the negative operating
# cash flow that is simply what a growing loan book looks like.
FINANCIAL_SECTORS = {"Financial Services"}


def is_lender(stock: dict) -> bool:
    """True when the general question set does not apply.

    Two independent signals, either sufficient: Screener's row shape (a bank
    reports Financing Profit) and NSE's sector classification. The sector is
    the more reliable of the two for NBFCs, which often file a conventional
    profit and loss.
    """
    return (
        stock.get("company_type") == "financial"
        or stock.get("sector") in FINANCIAL_SECTORS
    )


def _history(frame: pd.DataFrame, column: str) -> list:
    return frame[column].tolist() if column in frame else []


def assemble(
    symbol: str,
    annual: pd.DataFrame,
    quarterly: pd.DataFrame,
    holding: pd.DataFrame,
    stock: dict,
    market: dict,
) -> tuple[dict, list[redflags.Flag]]:
    """Everything the scorers need for one company, from filed data only."""
    is_financial = is_lender(stock)
    latest = annual.iloc[-1].to_dict() if len(annual) else {}

    pat_history = _history(annual, "pat")
    cfo_history = _history(annual, "cfo")
    debtor_days = _history(annual, "debtor_days")
    opm_quarters = _history(quarterly, "opm")

    promoter_history = _history(holding, "promoter_pct")
    has_promoter = bool(holding["has_promoter"].iloc[-1]) if len(holding) else False
    pledge_checked = bool(holding["pledge_checked"].iloc[-1]) if len(holding) else False

    flags = redflags.evaluate(
        {
            "pledge_pct": None,
            "pledge_checked": pledge_checked,
            "promoter_history": promoter_history,
            "has_promoter": has_promoter,
            "cfo": cfo_history,
            "pat": pat_history,
            "debtor_days": debtor_days,
            "is_financial": is_financial,
        }
    )

    metrics = quality.build_metrics(
        {
            "revenue_history": _history(annual, "revenue"),
            "pat_history": pat_history,
            "quarterly_pat": _history(quarterly, "pat"),
            "cfo_history": cfo_history,
            "opm_history": _history(annual, "ebitda"),
            "debt_history": _history(annual, "debt"),
            "roe": latest.get("roe") or market.get("roe"),
            "roce": latest.get("roce") or market.get("roce"),
            "debt_equity": latest.get("debt_equity"),
            "interest_cover": latest.get("interest_cover"),
            "fcf": latest.get("fcf"),
            "pe": market.get("pe"),
            "pat_cagr_3y": quality.cagr(pat_history, 3),
        }
    )

    metrics.update(
        {
            "symbol": symbol,
            "sector": stock.get("sector"),
            "is_financial": is_financial,
            "pb": market.get("pb"),
            "ev_ebitda": market.get("ev_ebitda"),
            "ev_sales": market.get("ev_sales"),
            "pe_5y_median": market.get("pe_5y_median"),
            "dividend_yield": market.get("dividend_yield"),
            "margin_expanding": value.margin_expanding(opm_quarters),
        }
    )
    return metrics, flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Q and V")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--as-of", help="ISO date; defaults to today")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    stocks = pd.DataFrame(db.select("stocks"))
    annual = pd.DataFrame(db.select("fundamentals_y"))
    quarterly = pd.DataFrame(db.select("fundamentals_q"))
    holding = pd.DataFrame(db.select("shareholding"))
    company_ratios = pd.DataFrame(db.select("company_ratios"))
    prices = pd.DataFrame(db.select("prices_daily"))
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
        prices["adj_close"] = pd.to_numeric(prices["adj_close"], errors="coerce")

    if stocks.empty or annual.empty:
        print(f"[{JOB}] no fundamentals — run load_fundamentals first", file=sys.stderr)
        return 1

    # THE point-in-time filter. Everything downstream depends on it.
    annual = annual[pd.to_datetime(annual["filed_on"]).dt.date <= as_of]
    if not quarterly.empty:
        quarterly = quarterly[pd.to_datetime(quarterly["filed_on"]).dt.date <= as_of]

    for column in ("revenue", "ebitda", "pat", "eps", "cfo", "fcf", "roce", "roe",
                   "debtor_days", "debt", "equity", "debt_equity", "interest_cover"):
        if column in annual:
            annual[column] = pd.to_numeric(annual[column], errors="coerce")
    for column in ("revenue", "pat", "opm", "eps"):
        if column in quarterly:
            quarterly[column] = pd.to_numeric(quarterly[column], errors="coerce")
    if not holding.empty:
        for column in ("promoter_pct", "fii_pct", "dii_pct"):
            if column in holding:
                holding[column] = pd.to_numeric(holding[column], errors="coerce")

    stock_index = stocks.set_index("symbol")
    ratio_index = (
        company_ratios.set_index("symbol") if not company_ratios.empty else pd.DataFrame()
    )
    prices_by = (
        dict(tuple(prices.groupby("symbol"))) if not prices.empty else {}
    )
    annual_by = dict(tuple(annual.sort_values("period_end").groupby("symbol")))
    quarterly_by = (
        dict(tuple(quarterly.sort_values("period_end").groupby("symbol")))
        if not quarterly.empty else {}
    )
    holding_by = (
        dict(tuple(holding.sort_values("quarter_end").groupby("symbol")))
        if not holding.empty else {}
    )

    records: list[dict] = []
    flag_rows: dict[str, list] = {}

    with run(JOB, db=db) as log:
        for symbol, frame in annual_by.items():
            if symbol not in stock_index.index:
                continue
            metrics, flags = assemble(
                symbol,
                frame,
                quarterly_by.get(symbol, pd.DataFrame()),
                holding_by.get(symbol, pd.DataFrame()),
                stock_index.loc[symbol].to_dict(),
                _market_context(
                    symbol, frame, ratio_index, prices_by.get(symbol, pd.DataFrame())
                ),
            )
            records.append(metrics)
            flag_rows[symbol] = flags
            log.symbols_ok += 1

        table = pd.DataFrame(records).set_index("symbol")
        numeric = [
            "revenue_cagr_3y", "pat_cagr_3y", "quarter_yoy", "roe", "roce", "opm",
            "opm_trend", "debt_equity", "interest_cover", "debt_trend", "cfo_to_pat",
            "fcf_positive", "pe", "peg", "pb", "ev_ebitda", "ev_sales",
            "pe_5y_median", "dividend_yield",
        ]
        for column in numeric:
            # Values arrive from JSON as objects; an object column silently
            # turns every downstream fillna into a deprecated downcast and, in
            # a future pandas, into a different result.
            table[column] = pd.to_numeric(table.get(column), errors="coerce")

        q = quality.score(table)
        v = value.score(table)

        rows = []
        for symbol in table.index:
            flags = flag_rows[symbol]
            excluded = redflags.excluded(flags)
            rows.append(
                {
                    "symbol": symbol,
                    "date": as_of.isoformat(),
                    # An excluded business scores nothing. It is not a low
                    # score, it is off the list, and the reason travels with it.
                    "quality_score": None if excluded or pd.isna(q.loc[symbol]) else round(float(q.loc[symbol]), 2),
                    "value_score": None if excluded or pd.isna(v.loc[symbol]) else round(float(v.loc[symbol]), 2),
                    "excluded": excluded,
                    "flags": redflags.summarise(flags),
                }
            )

        log.rows_written = db.upsert(
            "fundamental_scores", rows, on_conflict="symbol,date"
        )
        n_excluded = sum(1 for r in rows if r["excluded"])
        scored = sum(1 for r in rows if r["quality_score"] is not None)
        log.notes = f"{scored} scored, {n_excluded} excluded by a red flag"
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


def own_history_pe_median(
    prices: pd.DataFrame, annual: pd.DataFrame, *, years: int = 5
) -> float | None:
    """Median of the stock's own year-end P/E over the last `years` of results.

    This is the block of the value score that survives a whole sector
    re-rating: a stock at half its own historical multiple is cheap in a way
    that does not depend on comparing it to anybody else. Built from the price
    on each year-end against that year's reported EPS, so it uses only data
    that existed at the time.
    """
    if prices.empty or annual.empty or "eps" not in annual:
        return None

    series = prices.set_index("date")["adj_close"].sort_index()
    multiples: list[float] = []
    for _, row in annual.tail(years).iterrows():
        eps = row.get("eps")
        if eps is None or pd.isna(eps) or eps <= 0:
            continue
        stamp = pd.Timestamp(row["period_end"])
        window = series.loc[:stamp]
        if window.empty:
            continue
        multiples.append(float(window.iloc[-1]) / float(eps))

    return float(np.median(multiples)) if len(multiples) >= 3 else None


def enterprise_multiples(latest: dict, ratios: dict) -> dict:
    """EV/EBITDA and EV/Sales.

    Enterprise value is approximated as market cap plus borrowings. Cash is not
    subtracted because Screener's balance sheet does not break it out, so these
    read slightly rich for cash-heavy companies — acceptable for a percentile
    rank within a sector, where the bias is broadly shared, and noted so nobody
    reads them as exact.
    """
    market_cap = ratios.get("market_cap_cr")
    if not market_cap:
        return {}
    debt = latest.get("debt") or 0.0
    ev = market_cap + debt
    out: dict = {}
    ebitda, revenue = latest.get("ebitda"), latest.get("revenue")
    if ebitda and ebitda > 0:
        out["ev_ebitda"] = round(ev / ebitda, 3)
    if revenue and revenue > 0:
        out["ev_sales"] = round(ev / revenue, 3)
    return out


def _market_context(
    symbol: str, annual: pd.DataFrame, ratio_index: pd.DataFrame, prices: pd.DataFrame
) -> dict:
    ratios = (
        ratio_index.loc[symbol].to_dict()
        if len(ratio_index) and symbol in ratio_index.index
        else {}
    )
    for key in ("pe", "pb", "dividend_yield", "roe", "roce", "market_cap_cr"):
        if key in ratios:
            ratios[key] = pd.to_numeric(ratios[key], errors="coerce")
            if pd.isna(ratios[key]):
                ratios[key] = None

    latest = annual.iloc[-1].to_dict() if len(annual) else {}
    context = dict(ratios)
    context.update(enterprise_multiples(latest, ratios))
    context["pe_5y_median"] = own_history_pe_median(prices, annual)
    return context


if __name__ == "__main__":
    sys.exit(main())
