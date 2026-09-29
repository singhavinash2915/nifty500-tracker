"""Job: measure the market backdrop and record what the ranking earned in it.

    python -m n500.jobs.compute_backdrop
    python -m n500.jobs.compute_backdrop --dry-run

One row a session in `market_backdrop`. Reads the local price mirror, so it
costs no egress; see n500.scoring.regime for the measures and the study the
historical figures come from.
"""

from __future__ import annotations

import argparse
import sys

from .. import pricecache
from ..db import Db, run
from ..scoring import regime

JOB = "compute_backdrop"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the market backdrop")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    pricecache.sync(db)

    with run(JOB, db=db) as log:
        backdrop = regime.measure(pricecache.load("symbol,date,adj_close"))
        row = backdrop.to_row()
        log.rows_written = db.upsert("market_backdrop", [row], on_conflict="date")
        log.symbols_ok = 1
        log.notes = (f"{row['date']}: {row['regime']}, breadth {backdrop.breadth:.0%}, "
                     f"{backdrop.drawdown:+.1%} off the 52-week high")
        summary = log.notes

    print(f"[{JOB}] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
