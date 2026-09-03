"""Job: scrape company fundamentals from Screener.in.

    python -m n500.jobs.load_fundamentals --dry-run

Roughly 25 minutes cold for 500 symbols at the pacing in the source module,
and near-instant afterwards: pages are cached for 25 days, because financials
change four times a year and refetching daily is the fastest route to a block.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from ..db import Db, run
from ..sources import screener
from ..sources.screener import CompanyNotFound, ScreenerError

JOB = "load_fundamentals"


# Flow items that scale with the length of the reporting period.
FLOW_FIELDS = ("revenue", "expenses", "ebitda", "pat", "other_income",
               "interest", "depreciation", "pbt", "cfo", "fcf")


def _index_by_period(records: list[dict]) -> dict:
    return {r["period_end"]: r for r in records if r.get("period_end")}


def annualise(record: dict) -> dict:
    """Rescale flow items to a 12-month basis.

    A company that changes its year-end files a 15-month period, which inflates
    revenue and profit by a quarter. Left alone that reads as a growth spurt
    followed by a collapse, and both are artefacts of the calendar. Stock items
    (balance-sheet lines) and ratios are untouched — they are point-in-time
    values and do not scale with duration.
    """
    months = record.get("period_months") or 12
    # Quarters are already three months; only a genuinely odd length matters.
    basis = 3 if months in (1, 2, 3, 4, 5, 6) and record.get("is_quarter") else 12
    if months == basis:
        return record
    factor = float(basis) / months
    scaled = dict(record)
    for field_name in FLOW_FIELDS:
        if scaled.get(field_name) is not None:
            scaled[field_name] = scaled[field_name] * factor
    scaled["annualised_from_months"] = months
    return scaled


def build_rows(data: screener.Fundamentals) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Assemble the P&L, cash flow, ratios and balance sheet into yearly rows."""
    cash = _index_by_period(data.cash_flow)
    ratios = _index_by_period(data.ratios)
    balance = _index_by_period(data.balance_sheet)

    annual_rows: list[dict] = []
    for raw in data.annual:
        record = annualise(raw)
        period = record["period_end"]
        cf = cash.get(period, {})
        ra = ratios.get(period, {})
        bs = balance.get(period, {})

        equity = None
        if bs.get("equity_capital") is not None and bs.get("reserves") is not None:
            equity = bs["equity_capital"] + bs["reserves"]

        borrowings = bs.get("borrowings")
        annual_rows.append(
            {
                "symbol": data.symbol,
                "fy": period.year,
                "period_end": period.isoformat(),
                "filed_on": record["filed_on"].isoformat(),
                "filed_on_is_estimated": True,
                "period_months": record.get("period_months", 12),
                "revenue": record.get("revenue"),
                "ebitda": record.get("ebitda"),
                "pat": record.get("pat"),
                "eps": record.get("eps"),
                "cfo": cf.get("cfo"),
                "fcf": cf.get("fcf"),
                "roce": ra.get("roce"),
                "roe": ra.get("roe"),
                "debtor_days": ra.get("debtor_days"),
                "debt": borrowings,
                "equity": equity,
                # A bank's balance sheet is leveraged by design; debt/equity
                # carries no signal there and is left null rather than computed
                # into a number that would rank every lender last.
                "debt_equity": (
                    round(borrowings / equity, 4)
                    if not data.is_financial and borrowings is not None and equity
                    else None
                ),
                "interest_cover": (
                    round(record["ebitda"] / record["interest"], 3)
                    if not data.is_financial
                    and record.get("ebitda") is not None
                    and record.get("interest")
                    else None
                ),
                "source": "screener",
            }
        )

    quarterly_rows = [
        {
            "symbol": data.symbol,
            "period_end": r["period_end"].isoformat(),
            "period_months": r.get("period_months", 3),
            "filed_on": r["filed_on"].isoformat(),
            "filed_on_is_estimated": True,
            "revenue": r.get("revenue"),
            "ebitda": r.get("ebitda"),
            "pat": r.get("pat"),
            "eps": r.get("eps"),
            "opm": r.get("opm"),
            "source": "screener",
        }
        for r in (annualise(x) for x in data.quarterly)
    ]

    shareholding_rows = [
        {
            "symbol": data.symbol,
            "quarter_end": r["period_end"].isoformat(),
            "promoter_pct": r.get("promoter_pct"),
            "fii_pct": r.get("fii_pct"),
            "dii_pct": r.get("dii_pct"),
            "public_pct": r.get("public_pct"),
            # Screener publishes no pledge figure. NULL means "not checked",
            # which must never be read as "no pledge".
            "pledge_pct": None,
            "pledge_checked": False,
            "has_promoter": data.has_promoter,
            "source": "screener",
        }
        for r in data.shareholding
    ]

    top = data.top_ratios
    book_value = top.get("book_value")
    current_price = top.get("current_price")
    ratio_row = {
        "symbol": data.symbol,
        "market_cap_cr": top.get("market_cap"),
        "pe": top.get("pe"),
        # Screener publishes book value per share, so P/B comes from the same
        # snapshot as the price it quotes — mixing today's price with a stale
        # book value would drift.
        "pb": (
            round(current_price / book_value, 4)
            if current_price and book_value and book_value > 0 else None
        ),
        "book_value": book_value,
        "dividend_yield": top.get("dividend_yield"),
        "roe": top.get("roe"),
        "roce": top.get("roce"),
        "company_type": data.company_type,
    }

    return annual_rows, quarterly_rows, shareholding_rows, ratio_row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape fundamentals from Screener.in")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", help="comma-separated subset")
    parser.add_argument("--limit", type=int, default=0, help="stop after N symbols")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)

    if args.symbols:
        universe = sorted({s.strip().upper() for s in args.symbols.split(",")})
    else:
        universe = sorted(
            row["symbol"]
            for row in db.select("stocks", "symbol,is_active")
            if row.get("is_active", True)
        )
    if args.limit:
        universe = universe[: args.limit]
    if not universe:
        print(f"[{JOB}] universe is empty — run load_universe first", file=sys.stderr)
        return 1

    client = screener.make_client()
    annual: list[dict] = []
    quarterly: list[dict] = []
    shareholding: list[dict] = []
    ratios: list[dict] = []
    types: dict[str, str] = {}
    missing: list[str] = []

    with run(JOB, db=db) as log:
        for symbol in universe:
            try:
                data = screener.load(client, symbol)
            except CompanyNotFound:
                missing.append(symbol)
                log.error(symbol, "no Screener page (renamed, merged or demerged)")
                continue
            except (ScreenerError, httpx.HTTPError) as exc:
                log.error(symbol, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                # A 500-symbol sweep must not die on symbol 17. An unexpected
                # page shape is one company's problem, recorded and skipped.
                log.error(symbol, f"unexpected: {type(exc).__name__}: {exc}")
                continue

            a, q, s, r = build_rows(data)
            annual.extend(a)
            quarterly.extend(q)
            shareholding.extend(s)
            ratios.append(r)
            types[symbol] = data.company_type
            log.symbols_ok += 1

        log.rows_written = (
            db.upsert("fundamentals_y", annual, on_conflict="symbol,fy")
            + db.upsert("fundamentals_q", quarterly, on_conflict="symbol,period_end")
            + db.upsert("shareholding", shareholding, on_conflict="symbol,quarter_end")
            + db.upsert("company_ratios", ratios, on_conflict="symbol")
        )
        db.upsert(
            "stocks",
            [{"symbol": s, "company_type": t} for s, t in types.items()],
            on_conflict="symbol",
        )

        financial = sum(1 for t in types.values() if t == "financial")
        log.notes = (
            f"{log.symbols_ok} companies ({financial} financial), "
            f"{len(missing)} with no page"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    if missing:
        print(f"[{JOB}] no page: {', '.join(missing[:20])}"
              + (f" ... +{len(missing)-20} more" if len(missing) > 20 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
