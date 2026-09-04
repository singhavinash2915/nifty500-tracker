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

# Below this the stored universe is not a universe, and continuing would score
# an empty index while reporting success.
MIN_STORED_UNIVERSE = 400


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
        except UniverseParseError as exc:
            # A layout change is not a network blip. The stored list is fine
            # but the parser is now wrong about the world, and continuing would
            # hide that until somebody noticed 500 rows of nulls.
            log.error("*", f"parse failed: {exc}")
            raise
        except Exception as exc:
            # The constituent list changes twice a year at index rebalances,
            # and yesterday's is almost certainly today's. Failing hard on a
            # read timeout meant one unreachable 33KB CSV skipped prices,
            # technicals, zones, scores, alerts — a whole night of data that had
            # nothing to do with the file that failed. It happened at 19:29 on
            # 4 September and cost the day.
            #
            # So a fetch failure is survivable *if* a usable universe is already
            # stored. It is not survivable on a first run, where continuing
            # would score an empty index and report success.
            stored = [
                r for r in db.select("stocks", "symbol,is_active")
                if r.get("is_active", True)
            ]
            log.error("*", f"fetch failed: {exc}")
            if len(stored) < MIN_STORED_UNIVERSE:
                print(
                    f"[{JOB}] fetch failed and only {len(stored)} symbols are stored "
                    f"— refusing to continue on an empty universe",
                    file=sys.stderr,
                )
                raise
            log.notes = (
                f"fetch failed ({type(exc).__name__}); continuing on the stored "
                f"universe of {len(stored)} symbols"
            )
            print(
                f"[{JOB}] could not reach the index CSV ({type(exc).__name__}). "
                f"Using the {len(stored)} symbols already stored — the constituent "
                f"list changes twice a year, so this is stale at worst.",
                file=sys.stderr,
            )
            return 0

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
