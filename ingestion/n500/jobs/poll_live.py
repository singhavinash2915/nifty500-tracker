"""Job: refresh intraday index quotes.

    python -m n500.jobs.poll_live          # one poll, then exit
    python -m n500.jobs.poll_live --force  # poll even outside market hours

Designed to be run on a short interval by launchd rather than to loop, so a
crash costs one poll rather than the rest of the day.

Nothing in the scoring engine reads what this writes. A score built on a price
that moves under it would mean something different between two glances at the
screen, so the pipeline stays on settled end-of-day data and this is only ever
context.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from ..db import Db, run
from ..sources import nse_live
from ..sources.nse_live import LiveQuoteError

JOB = "poll_live"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh live index quotes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="poll outside market hours too")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.force and not nse_live.market_is_open():
        if not args.quiet:
            print(f"[{JOB}] market closed — nothing to poll")
        return 0

    db = Db(force_dry_run=args.dry_run)
    client = nse_live.make_client()

    with run(JOB, db=db) as log:
        try:
            quotes = nse_live.fetch(client)
        except (LiveQuoteError, httpx.HTTPError) as exc:
            # NSE throttles and occasionally 403s. One missed poll is not worth
            # a failed job; the next one is a few minutes away and the stored
            # timestamp shows the gap.
            log.error("*", str(exc))
            log.notes = "poll failed; previous quotes left in place"
            if not args.quiet:
                print(f"[{JOB}] poll failed: {exc}", file=sys.stderr)
            return 0

        rows = [nse_live.to_row(q) for q in quotes]
        log.rows_written = db.upsert("live_quotes", rows, on_conflict="name")
        log.symbols_ok = len(rows)
        log.notes = f"{len(rows)} indices at {quotes[0].as_of}"
        stamp = quotes[0].as_of

    if not args.quiet:
        print(f"[{JOB}] {len(rows)} indices refreshed, exchange time {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
