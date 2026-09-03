"""Job: backfill and update daily prices from the NSE bhavcopy archive.

    python -m n500.jobs.load_prices --days 500          # backfill
    python -m n500.jobs.load_prices --days 5            # nightly top-up
    python -m n500.jobs.load_prices --days 500 --dry-run

Bhavcopy files are immutable once published, so every fetch is cached under
data/cache/bhavcopy/. Re-running a backfill after a failure costs no network at
all — which matters, because a 500-day sweep is 500 requests and we would
rather not repeat it to recover from one bad day.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx

from ..db import Db, run
from ..sources import bhavcopy
from ..sources.bhavcopy import BhavcopyError, BhavcopyUnavailable, Quote

JOB = "load_prices"


def trading_day_candidates(days: int, *, end: date | None = None) -> list[date]:
    """Every calendar day going back from `end`; non-trading days 404 and skip.

    Weekends are deliberately included. NSE holds a special live session on
    Budget day, 1 February, even when it falls on a Saturday or Sunday — and
    skipping it does not merely lose one bar. The next session's PrvsClsgPric
    then disagrees with our stored close, which the corporate-action detector
    reads as a 2-4% split. That produced 119 spurious adjustments before this
    was fixed. Misses are cached, so the extra probes cost one run.
    """
    end = end or date.today()
    return sorted(end - timedelta(days=offset) for offset in range(days))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load daily prices from NSE bhavcopy")
    parser.add_argument(
        "--days", type=int, default=760,
        help="calendar days back to cover (760 ~ 2 years of sessions)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pause", type=float, default=0.35, help="seconds between fetches")
    parser.add_argument(
        "--symbols", help="comma-separated subset, for testing; defaults to the universe"
    )
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)

    if args.symbols:
        universe = {s.strip().upper() for s in args.symbols.split(",")}
    else:
        universe = {
            row["symbol"] for row in db.select("stocks", "symbol,is_active")
            if row.get("is_active", True)
        }
    if not universe:
        print(f"[{JOB}] universe is empty — run load_universe first", file=sys.stderr)
        return 1

    client = bhavcopy.make_client()
    per_symbol: dict[str, list[Quote]] = defaultdict(list)

    with run(JOB, db=db) as log:
        sessions = 0
        missing = 0
        for day in trading_day_candidates(args.days):
            cached = bhavcopy._cache_path(day).exists()
            try:
                quotes = bhavcopy.fetch(client, day)
            except BhavcopyUnavailable:
                missing += 1
                continue
            except (BhavcopyError, httpx.HTTPError) as exc:
                log.error(day.isoformat(), str(exc))
                continue

            for symbol in universe & quotes.keys():
                per_symbol[symbol].append(quotes[symbol])
            sessions += 1

            if not cached:
                time.sleep(args.pause + random.uniform(0, 0.15))

        rows: list[dict] = []
        actions = 0
        for symbol, quotes in per_symbol.items():
            rows.extend(bhavcopy.adjust(quotes))
            actions += len(bhavcopy.corporate_actions(quotes))
            log.symbols_ok += 1

        for missed in sorted(universe - per_symbol.keys()):
            # A Nifty 500 name absent from every session is a symbol-mapping
            # problem, not a market event. Surface it rather than shrugging.
            log.error(missed, "not present in any bhavcopy session")

        log.rows_written = db.upsert("prices_daily", rows, on_conflict="symbol,date")
        log.notes = (
            f"{sessions} sessions ({missing} holidays skipped), "
            f"{len(per_symbol)} symbols, {actions} corporate actions adjusted"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {len(rows)} price rows written ({mode}) — {summary}")
    if not args.dry_run:
        print(f"[{JOB}] cache: {bhavcopy.CACHE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
