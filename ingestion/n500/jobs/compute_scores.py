"""Job: score the latest cross-section.

    python -m n500.jobs.compute_scores --dry-run

Five pillars are live: quality, value, revision, ownership and the technical.
The technical input to the blend is max(T-M, T-S), and the winning setup is
recorded: a stock is never punished for being a good reversal candidate rather
than a good breakout, because they are different setups and averaging them
would make both invisible.

The weights live in config and carry the reasoning behind them. Broadly:
quality and value describe what the business *is*, revision and ownership
describe what is *changing*, and the technical decides when. Only the first
three have been through a sweep.

A business excluded by a red flag gets no blended score at all. Not a low one:
a stock whose reported profit never becomes cash does not belong on the list,
and leaving it there with a poor number invites it to be bought anyway.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import settings
from ..db import Db, run
from ..scoring import conviction, momentum, redflags
from ..scoring.ranking import peer_groups

JOB = "compute_scores"

# Set in config, overridable per environment. See the note there: the sweep's
# one significant result is that quality predicted negatively, so its weight is
# cut without adopting the grid's peak.
WEIGHTS = settings.blend_weights


def _num(value):
    return None if pd.isna(value) else round(float(value), 4)


def build_snapshot(technicals: pd.DataFrame, prices: pd.DataFrame, stocks: pd.DataFrame) -> pd.DataFrame:
    """Latest technicals row per symbol, joined to its close and sector.

    Current constituents only. The survivorship fix added 312 companies that
    have left the index since 2018 — ALBK, ANDHRABANK, 8KMILES — so the
    backtest could see the ones that failed rather than only the survivors.
    They are marked inactive, and every job downstream of prices happily scored
    them anyway: the first run after that change put 210 delisted names into
    the screener and the buy list, offering positions in companies that are no
    longer in the index and in some cases no longer exist.

    Technicals and zones still compute for them, because the backtest needs
    exactly that. Scoring is where the live universe begins.
    """
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

    indexed = stocks.set_index("symbol")
    active = indexed["is_active"].fillna(True).astype(bool) if "is_active" in indexed else None

    snapshot = latest.join(last_close, how="inner", rsuffix="_price")
    snapshot["sector"] = indexed["sector"].reindex(snapshot.index)
    if active is not None:
        snapshot = snapshot[active.reindex(snapshot.index).fillna(False)]

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

    # Both of these are wanted only for their most recent row per symbol, and
    # reading them whole is 775 paged requests that get slower with depth. A
    # fortnight covers any gap a holiday or a suspension can open.
    recent = (date.today() - timedelta(days=21)).isoformat()
    technicals = pd.DataFrame(
        db.select("technicals_daily", since=("date", recent))
    )
    prices = pd.DataFrame(
        db.select("prices_daily", columns="symbol,date,adj_close", since=("date", recent))
    )
    stocks = pd.DataFrame(db.select("stocks"))

    if technicals.empty or prices.empty or stocks.empty:
        print(f"[{JOB}] missing inputs — run the earlier jobs first", file=sys.stderr)
        return 1

    snapshot = build_snapshot(technicals, prices, stocks)
    if snapshot.empty:
        print(f"[{JOB}] nothing to score", file=sys.stderr)
        return 1

    as_of = snapshot["date"].max().date()

    fundamentals = pd.DataFrame(db.select("fundamental_scores"))
    q = pd.Series(np.nan, index=snapshot.index, dtype="float64")
    v = pd.Series(np.nan, index=snapshot.index, dtype="float64")
    rev = pd.Series(np.nan, index=snapshot.index, dtype="float64")
    own = pd.Series(np.nan, index=snapshot.index, dtype="float64")
    excluded = pd.Series(False, index=snapshot.index, dtype="bool")
    flags_by: dict[str, list] = {}
    if not fundamentals.empty:
        latest_f = (
            fundamentals.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
        )
        q = pd.to_numeric(latest_f["quality_score"].reindex(snapshot.index), errors="coerce")
        v = pd.to_numeric(latest_f["value_score"].reindex(snapshot.index), errors="coerce")
        # Guarded on presence: the columns arrive with migration 0015, and a
        # database one migration behind should still produce a screener.
        if "revision_score" in latest_f:
            rev = pd.to_numeric(
                latest_f["revision_score"].reindex(snapshot.index), errors="coerce"
            )
        if "ownership_score" in latest_f:
            own = pd.to_numeric(
                latest_f["ownership_score"].reindex(snapshot.index), errors="coerce"
            )
        excluded = (
            latest_f["excluded"].reindex(snapshot.index).fillna(False).astype(bool)
        )
        flags_by = {
            symbol: latest_f.loc[symbol, "flags"]
            for symbol in latest_f.index
            if symbol in snapshot.index
        }

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

    # The liquidity gate is evaluated here rather than in the fundamentals job
    # because it is computed from price and volume, which change every day
    # while a filing changes once a quarter. It excludes exactly as a red flag
    # does: a stock you cannot get out of is off the list, not marked down.
    turnover = (
        pd.to_numeric(snapshot["turnover_60d_cr"], errors="coerce")
        if "turnover_60d_cr" in snapshot
        else pd.Series(np.nan, index=snapshot.index, dtype="float64")
    )
    market_flags: dict[str, list] = {}
    for symbol in snapshot.index:
        value_cr = turnover.loc[symbol]
        flags = redflags.evaluate_market(
            {"turnover_60d_cr": None if pd.isna(value_cr) else float(value_cr)}
        )
        market_flags[symbol] = flags
        if redflags.excluded(flags):
            excluded.loc[symbol] = True
        flags_by[symbol] = list(flags_by.get(symbol, [])) + redflags.summarise(flags)

    with run(JOB, db=db) as log:
        tm = momentum.score(snapshot)
        groups = peer_groups(snapshot["sector"])

        # max(T-M, T-S): the two setups are alternatives, not components.
        technical = pd.concat([tm, ts], axis=1).max(axis=1, skipna=True)
        winner = pd.Series("none", index=snapshot.index, dtype="object")
        winner[tm.notna()] = "momentum"
        wins_support = ts.notna() & (tm.isna() | (ts > tm))
        winner[wins_support] = "support"

        pillars = {"quality": (q, WEIGHTS["quality"]),
                   "value": (v, WEIGHTS["value"]),
                   "revision": (rev, WEIGHTS["revision"]),
                   "ownership": (own, WEIGHTS["ownership"]),
                   "technical": (technical, WEIGHTS["technical"])}
        weighted = pd.Series(0.0, index=snapshot.index)
        available = pd.Series(0.0, index=snapshot.index)
        for series, weight in pillars.values():
            weighted += series.fillna(0.0) * weight
            available += series.notna().astype("float64") * weight

        # Renormalise over whichever pillars exist, so the screener stays usable
        # while a phase is still landing — but never invent a pillar.
        blended = (weighted / available.replace(0.0, np.nan)).round(2)
        blended = blended.mask(excluded)
        winner = winner.mask(excluded, "none")

        # The validated composite, computed beside the blend rather than in
        # place of it. Its inputs are spread across three tables, so they are
        # assembled here — the only place that already holds all of them.
        features = pd.DataFrame(index=snapshot.index)
        features["tm_score"] = tm
        features["value_score"] = v
        features["ownership_score"] = own
        if not setups.empty:
            for name in ("headroom", "resistance_strength", "zone_respect"):
                if name in latest_setups:
                    features[name] = pd.to_numeric(
                        latest_setups[name].reindex(snapshot.index), errors="coerce"
                    )
            for name in ("rejected_at_resistance", "doji_at_resistance",
                         "hanging_man_at_resistance", "shooting_star_at_resistance",
                         "bearish_engulfing_at_resistance"):
                if name in latest_setups:
                    features[name] = (
                        latest_setups[name].reindex(snapshot.index)
                        .fillna(False).astype(bool).astype("float64")
                    )
            if "false_breakout" in latest_setups:
                # Stored as the event's detail or null, and the composite wants
                # the fact rather than the detail.
                features["false_breakout"] = (
                    latest_setups["false_breakout"].reindex(snapshot.index)
                    .notna().astype("float64")
                )
        if not fundamentals.empty and "margin_revision" in latest_f:
            features["margin_revision"] = pd.to_numeric(
                latest_f["margin_revision"].reindex(snapshot.index), errors="coerce"
            )

        conv = conviction.score(features).mask(excluded)
        conv_decile = pd.Series(pd.NA, index=conv.index, dtype="Int64")
        if conv.notna().sum() >= 10:
            conv_decile[conv.notna()] = (
                pd.qcut(conv[conv.notna()].rank(method="first"), 10, labels=False) + 1
            ).astype("Int64")

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
                    "quality_score": None if pd.isna(q.loc[symbol]) else round(float(q.loc[symbol]), 2),
                    "value_score": None if pd.isna(v.loc[symbol]) else round(float(v.loc[symbol]), 2),
                    "revision_score": None if pd.isna(rev.loc[symbol]) else round(float(rev.loc[symbol]), 2),
                    "ownership_score": None if pd.isna(own.loc[symbol]) else round(float(own.loc[symbol]), 2),
                    "tm_score": None if pd.isna(tm.loc[symbol]) else round(float(tm.loc[symbol]), 2),
                    "ts_score": None if pd.isna(ts.loc[symbol]) else round(float(ts.loc[symbol]), 2),
                    "blended": None if pd.isna(value) else round(float(value), 2),
                    "winning_setup": winner.loc[symbol],
                    "setup_status": status.loc[symbol],
                    # Copied from the technicals so the screener reads one
                    # table; see migration 0013.
                    "close": _num(snapshot.at[symbol, "close"]),
                    "mom_12_1": _num(snapshot.at[symbol, "mom_12_1"]),
                    "rs_vs_index": _num(snapshot.at[symbol, "rs_vs_index"]),
                    "dist_52w_high": _num(snapshot.at[symbol, "dist_52w_high"]),
                    "rsi14": _num(snapshot.at[symbol, "rsi14"]),
                    "turnover_60d_cr": _num(turnover.loc[symbol]),
                    "above_200dma": (
                        None if pd.isna(snapshot.at[symbol, "sma200"])
                        else bool(snapshot.at[symbol, "close"] > snapshot.at[symbol, "sma200"])
                    ),
                    "conviction": None if pd.isna(conv.loc[symbol]) else float(conv.loc[symbol]),
                    "conviction_decile": (
                        None if pd.isna(conv_decile.loc[symbol])
                        else int(conv_decile.loc[symbol])
                    ),
                    "sector_rank": None if pd.isna(sector_rank.loc[symbol]) else int(sector_rank.loc[symbol]),
                    "decile": None if pd.isna(decile.loc[symbol]) else int(decile.loc[symbol]),
                    "flags": flags_by.get(symbol, []),
                }
            )
            log.symbols_ok += 1

        log.rows_written = db.upsert("scores_daily", rows, on_conflict="symbol,date")
        support_wins = int((winner == "support").sum())
        thin = sum(1 for f in market_flags.values() if redflags.excluded(f))
        log.notes = (
            f"{len(rows)} scored as of {as_of}; {support_wins} led by the "
            f"support setup; {int(excluded.sum())} excluded by a red flag "
            f"({thin} of them for turnover); {int(conv.notna().sum())} with a "
            f"conviction score"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
