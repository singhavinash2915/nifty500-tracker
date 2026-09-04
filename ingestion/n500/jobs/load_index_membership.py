"""Job: record who was in the Nifty 500 on past dates.

    python -m n500.jobs.load_index_membership --dry-run
    python -m n500.jobs.load_index_membership

Writes one row per (snapshot date, symbol) to `index_membership`, and adds a
`stocks` row for any company that has since left the index — without it the
foreign key rejects exactly the names that matter most, the ones that fell out.
Those companies are marked inactive so nothing downstream treats them as
current, but their history stays available to the backtest.

Prices for them are not loaded here. The bhavcopy is the whole market on each
day, so the data exists; `load_prices` reads the universe from `stocks`, so it
picks them up on its next run.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from ..db import Db, run
from ..sources import index_history
from ..sources.index_history import MembershipError

JOB = "load_index_membership"
INDEX_NAME = "NIFTY500"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load historical index membership")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    client = index_history.make_client()

    known = {row["symbol"] for row in db.select("stocks", "symbol")}

    membership_rows: list[dict] = []
    new_stocks: dict[str, dict] = {}
    snapshots = 0

    with run(JOB, db=db) as log:
        for timestamp in index_history.TIMESTAMPS:
            on = index_history.snapshot_date(timestamp)
            try:
                constituents = index_history.fetch(client, timestamp)
            except (MembershipError, httpx.HTTPError) as exc:
                log.error(timestamp, str(exc))
                continue

            for c in constituents:
                membership_rows.append(
                    {
                        "symbol": c.symbol,
                        "week_start": on.isoformat(),
                        "index_name": INDEX_NAME,
                    }
                )
                if c.symbol not in known and c.symbol not in new_stocks:
                    new_stocks[c.symbol] = {
                        "symbol": c.symbol,
                        "company_name": c.company_name or c.symbol,
                        "sector": c.industry or None,
                        "isin": c.isin or None,
                        # Left the index at some point. Kept so the backtest can
                        # see it, flagged so nothing live screens it.
                        "is_active": False,
                    }
            snapshots += 1
            log.symbols_ok += len(constituents)

        if new_stocks:
            db.upsert("stocks", list(new_stocks.values()), on_conflict="symbol")

        log.rows_written = db.upsert(
            "index_membership", membership_rows,
            on_conflict="index_name,week_start,symbol",
        )
        log.notes = (
            f"{snapshots} snapshots, {len(membership_rows)} memberships, "
            f"{len(new_stocks)} companies that have since left the index"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
