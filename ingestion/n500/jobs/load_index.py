"""Job: benchmark index history from the NSE index archive.

    python -m n500.jobs.load_index --days 500 --dry-run

Same day-by-day, cache-on-disk pattern as load_prices.
"""

from __future__ import annotations

import argparse
import random
import sys
import time

import httpx

from ..db import Db, run
from ..sources import nse_index
from ..sources.bhavcopy import make_client
from ..sources.nse_index import IndexArchiveError, IndexArchiveUnavailable
from .load_prices import trading_day_candidates

JOB = "load_index"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load benchmark index history")
    parser.add_argument("--days", type=int, default=760, help="calendar days back")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pause", type=float, default=0.35)
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    client = make_client()

    rows = []
    with run(JOB, db=db) as log:
        missing = 0
        for day in trading_day_candidates(args.days):
            cached = nse_index._cache_path(day).exists()
            try:
                quotes = nse_index.fetch_all(client, day)
            except IndexArchiveUnavailable:
                missing += 1
                continue
            except (IndexArchiveError, httpx.HTTPError) as exc:
                log.error(day.isoformat(), str(exc))
                continue

            rows.extend(nse_index.to_row(q) for q in quotes)
            log.symbols_ok += 1

            if not cached:
                time.sleep(args.pause + random.uniform(0, 0.15))

        log.rows_written = db.upsert("index_prices", rows, on_conflict="index_name,date")
        indices = len({r["index_name"] for r in rows})
        log.notes = (
            f"{log.symbols_ok} sessions across {indices} indices ({missing} skipped)"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
