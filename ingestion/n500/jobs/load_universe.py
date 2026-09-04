"""Job: refresh the Nifty 500 universe.

Writes/updates `stocks` and appends this week's `index_membership` snapshot.
Names that have dropped out of the index are marked is_active=false rather than
deleted — their price and score history stays intact, and the weekly membership
rows are what a backtest reads to rebuild a point-in-time universe.

    python -m n500.jobs.load_universe            # writes to Supabase
    python -m n500.jobs.load_universe --dry-run  # writes data/dryrun/*.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ..db import Db, run
from ..sources.universe import (
    UniverseParseError,
    etf_constituents,
    fetch_constituents,
    to_stock_row,
    week_start,
)

JOB = "load_universe"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the Nifty 500 constituent list")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write JSON to data/dryrun/ instead of Supabase",
    )
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    today = date.today()
    monday = week_start(today)

    with run(JOB, db=db) as log:
        try:
            constituents = fetch_constituents()
        except (UniverseParseError, Exception) as exc:
            log.error("*", f"fetch/parse failed: {exc}")
            raise

        # ETFs are tracked but are not index members, so they go into `stocks`
        # and never into `index_membership` — a backtest reconstructing the
        # Nifty 500 of a past date must not find a gold ETF in it.
        etfs = etf_constituents()
        symbols = {c.symbol for c in constituents}

        stock_rows = [to_stock_row(c, today=today) for c in constituents + etfs]
        log.rows_written += db.upsert("stocks", stock_rows, on_conflict="symbol")

        membership_rows = [
            {"symbol": s, "week_start": monday.isoformat(), "index_name": "NIFTY500"}
            for s in sorted(symbols)
        ]
        log.rows_written += db.upsert(
            "index_membership",
            membership_rows,
            on_conflict="index_name,week_start,symbol",
        )

        log.symbols_ok = len(symbols)

        tracked = symbols | {c.symbol for c in etfs}
        dropped = 0
        if not db.dry_run:
            existing = db.select("stocks", "symbol,is_active")
            for row in existing:
                if row["is_active"] and row["symbol"] not in tracked:
                    db.update(
                        "stocks",
                        {"is_active": False},
                        where={"symbol": row["symbol"]},
                    )
                    dropped += 1

        log.notes = (
            f"{len(symbols)} constituents + {len(etfs)} ETFs, "
            f"{dropped} dropped out, week of {monday}"
        )

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {len(symbols)} constituents + {len(etfs)} ETFs written ({mode}), "
          f"week of {monday}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
