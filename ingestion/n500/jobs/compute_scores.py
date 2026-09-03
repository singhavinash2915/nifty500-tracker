"""Job: score the latest cross-section.

    python -m n500.jobs.compute_scores --dry-run

Phase 3 fills in T-M and T-S. Q and V arrive in phase 4; their columns stay
null rather than being faked with a placeholder, so nothing downstream can
mistake "not computed yet" for "scored zero".

The technical input to the blend is max(T-M, T-S), and the winning setup is
recorded. A stock is never punished for being a good reversal candidate rather
than a good breakout — they are different setups, and averaging them would
make both invisible.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from ..db import Db, run
from ..scoring import momentum
from ..scoring.ranking import peer_groups

JOB = "compute_scores"


def build_snapshot(technicals: pd.DataFrame, prices: pd.DataFrame, stocks: pd.DataFrame) -> pd.DataFrame:
    """Latest technicals row per symbol, joined to its close and sector."""
    technicals = technicals.copy()
    technicals["date"] = pd.to_datetime(technicals["date"])
    latest = technicals.sort_values("date").groupby("symbol").tail(1).set_index("symbol")

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    last_close = (
        prices.sort_values("date")
        .groupby("symbol")
        .tail(1)
        .set_index("symbol")[["adj_close", "date"]]
        .rename(columns={"adj_close": "close", "date": "price_date"})
    )

    sectors = stocks.set_index("symbol")["sector"]

    snapshot = latest.join(last_close, how="inner", rsuffix="_price")
    snapshot["sector"] = sectors.reindex(snapshot.index)

    # A technicals row that is older than the price row means the technicals
    # job has not caught up; scoring on it would mix dates.
    stale = snapshot["date"] < snapshot["price_date"]
    if stale.any():
        snapshot = snapshot[~stale]

    for column in snapshot.columns:
        if column not in {"sector", "date", "price_date", "symbol"}:
            snapshot[column] = pd.to_numeric(snapshot[column], errors="coerce")

    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute the daily scores")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)

    technicals = pd.DataFrame(db.select("technicals_daily"))
    prices = pd.DataFrame(db.select("prices_daily"))
    stocks = pd.DataFrame(db.select("stocks"))

    if technicals.empty or prices.empty or stocks.empty:
        print(f"[{JOB}] missing inputs — run the earlier jobs first", file=sys.stderr)
        return 1

    snapshot = build_snapshot(technicals, prices, stocks)
    if snapshot.empty:
        print(f"[{JOB}] nothing to score", file=sys.stderr)
        return 1

    as_of = snapshot["date"].max().date()

    setups = pd.DataFrame(db.select("ts_setups"))
    ts = pd.Series(np.nan, index=snapshot.index, dtype="float64")
    status = pd.Series("none", index=snapshot.index, dtype="object")
    if not setups.empty:
        latest_setups = (
            setups.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
        )
        ts = pd.to_numeric(
            latest_setups["ts_score"].reindex(snapshot.index), errors="coerce"
        )
        status = latest_setups["setup_status"].reindex(snapshot.index).fillna("none")

    with run(JOB, db=db) as log:
        tm = momentum.score(snapshot)
        groups = peer_groups(snapshot["sector"])

        # max(T-M, T-S): the two setups are alternatives, not components.
        blended = pd.concat([tm, ts], axis=1).max(axis=1, skipna=True)
        winner = pd.Series("none", index=snapshot.index, dtype="object")
        winner[tm.notna()] = "momentum"
        wins_support = ts.notna() & (tm.isna() | (ts > tm))
        winner[wins_support] = "support"

        sector_rank = (
            blended.groupby(groups).rank(ascending=False, method="min").astype("Int64")
        )
        decile = pd.Series(pd.NA, index=blended.index, dtype="Int64")
        present = blended.notna()
        if present.sum() >= 10:
            decile[present] = (
                pd.qcut(blended[present].rank(method="first"), 10, labels=False) + 1
            ).astype("Int64")

        rows = []
        for symbol in snapshot.index:
            value = blended.loc[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "date": as_of.isoformat(),
                    "quality_score": None,
                    "value_score": None,
                    "tm_score": None if pd.isna(tm.loc[symbol]) else round(float(tm.loc[symbol]), 2),
                    "ts_score": None if pd.isna(ts.loc[symbol]) else round(float(ts.loc[symbol]), 2),
                    "blended": None if pd.isna(value) else round(float(value), 2),
                    "winning_setup": winner.loc[symbol],
                    "setup_status": status.loc[symbol],
                    "sector_rank": None if pd.isna(sector_rank.loc[symbol]) else int(sector_rank.loc[symbol]),
                    "decile": None if pd.isna(decile.loc[symbol]) else int(decile.loc[symbol]),
                    "flags": [],
                }
            )
            log.symbols_ok += 1

        log.rows_written = db.upsert("scores_daily", rows, on_conflict="symbol,date")
        support_wins = int((winner == "support").sum())
        log.notes = (
            f"{len(rows)} scored as of {as_of}; "
            f"{support_wins} led by the support setup"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
