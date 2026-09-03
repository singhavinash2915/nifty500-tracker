"""Job: turn stored prices into the technicals_daily table.

    python -m n500.jobs.compute_technicals --dry-run

Indicators are computed on *split-adjusted* series. prices_daily stores both the
raw close and adj_close; the ratio between them is the cumulative corporate
action factor, which is applied to open/high/low and inverted for volume. Using
raw prices here would make every split look like a crash and every bonus issue
like a collapse in momentum.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from .. import technicals
from ..db import Db, run
from ..sources.nse_index import BENCHMARK

JOB = "compute_technicals"

# Enough history for a 200DMA plus the 12-month momentum lookback.
MIN_BARS = 220


def adjusted_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """Restate OHLCV onto the split-adjusted basis, indexed by date."""
    frame = rows.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()

    for column in ("open", "high", "low", "close", "adj_close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    factor = (frame["adj_close"] / frame["close"]).fillna(1.0)

    out = pd.DataFrame(index=frame.index)
    out["open"] = frame["open"] * factor
    out["high"] = frame["high"] * factor
    out["low"] = frame["low"] * factor
    out["close"] = frame["adj_close"]
    # A 1:2 split doubles the share count, so historical volume must be scaled
    # the other way for the 20d/100d ratio to compare like with like.
    out["volume"] = frame["volume"] / factor.replace(0.0, pd.NA)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute daily technicals")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="only write the last N rows per symbol (0 = all)",
    )
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)

    prices = pd.DataFrame(db.select("prices_daily"))
    if prices.empty:
        print(f"[{JOB}] no prices found — run load_prices first", file=sys.stderr)
        return 1

    index_close = None
    index_rows = pd.DataFrame(db.select("index_prices"))
    if not index_rows.empty:
        bench = index_rows[index_rows["index_name"] == BENCHMARK].copy()
        if not bench.empty:
            bench["date"] = pd.to_datetime(bench["date"])
            index_close = (
                bench.set_index("date")["close"].astype("float64").sort_index()
            )
    if index_close is None:
        print(f"[{JOB}] no benchmark history — relative strength will be null",
              file=sys.stderr)

    rows: list[dict] = []
    with run(JOB, db=db) as log:
        for symbol, group in prices.groupby("symbol", sort=True):
            if len(group) < MIN_BARS:
                log.error(symbol, f"only {len(group)} bars, need {MIN_BARS}")
                continue

            frame = adjusted_frame(group)
            computed = technicals.compute(frame, index_close=index_close)
            if args.tail:
                computed = computed.tail(args.tail)

            symbol_rows = technicals.to_rows(symbol, computed)
            rows.extend(symbol_rows)
            log.symbols_ok += 1

        log.rows_written = db.upsert("technicals_daily", rows, on_conflict="symbol,date")
        log.notes = f"{log.symbols_ok} symbols, {len(rows)} rows"
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
