"""Job: hold the tables that grow every night to a fixed window.

    python -m n500.jobs.prune
    python -m n500.jobs.prune --days 120 --dry-run

The free plan caps each project at 500MB, and the project that held this
tracker was restricted after passing it. Three tables gain a full set of rows
every session and nothing ever removed them. Every reader wants recent rows:
scoring looks back 30 days, the holdings page 90. So 120 days are kept here.

The score history is not lost. `compute_scores` archives each night's ranking
to data/archive/scores/ on the machine that runs it, which is the live track
record, at no egress cost.

`technicals_daily` is pruned by its own job, which already knows its window.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from ..db import Db, run

JOB = "prune"
KEEP_DAYS = 120
TABLES = ("scores_daily", "ts_setups", "fundamental_scores")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune tables that grow nightly")
    parser.add_argument("--days", type=int, default=KEEP_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()

    with run(JOB, db=db) as log:
        removed = {t: db.delete_before(t, "date", cutoff) for t in TABLES}
        log.rows_written = sum(removed.values())
        log.symbols_ok = len(TABLES)
        log.notes = ", ".join(f"{t} -{n}" for t, n in removed.items()) + f" (before {cutoff})"
        summary = log.notes

    print(f"[{JOB}] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
