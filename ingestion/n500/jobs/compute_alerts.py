"""Job: evaluate the alert rules against today's scores and open positions.

    python -m n500.jobs.compute_alerts --dry-run

Runs last in the nightly chain, because every rule reads something an earlier
job produced. Alerts are transitions, so this needs yesterday's snapshot as
well as today's — which is why scores_daily keeps a row per date rather than
being overwritten.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from ..alerts import rules
from ..alerts.rules import Alert
from ..config import REPO_ROOT
from ..db import Db, run
from ..serialise import write

JOB = "compute_alerts"

# How far back to look for an already-reported instance of the same event.
DEDUPE_WINDOW_DAYS = 10

OUT = REPO_ROOT / "web" / "public" / "alerts.json"


def _record(frame: pd.DataFrame, symbol: str) -> dict | None:
    if frame.empty or symbol not in frame.index:
        return None
    row = frame.loc[symbol]
    return row.to_dict() if hasattr(row, "to_dict") else None


def snapshots(db: Db) -> tuple[pd.DataFrame, pd.DataFrame, date | None]:
    """Today's and the previous session's scores, indexed by symbol."""
    scores = pd.DataFrame(db.select("scores_daily"))
    if scores.empty:
        raise SystemExit(f"[{JOB}] no scores — run compute_scores first")

    scores["date"] = pd.to_datetime(scores["date"]).dt.date
    dates = sorted(scores["date"].unique())
    today = dates[-1]
    previous = dates[-2] if len(dates) > 1 else None

    current = scores[scores["date"] == today].set_index("symbol")
    prior = (
        scores[scores["date"] == previous].set_index("symbol")
        if previous is not None
        else pd.DataFrame()
    )
    return current, prior, today


def enrich_from_setups(db: Db, current: pd.DataFrame) -> pd.DataFrame:
    """Fold in the reasoning fields the alert messages quote."""
    setups = pd.DataFrame(db.select("ts_setups"))
    if setups.empty:
        return current
    latest = setups.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
    for column in ("stop_price", "target_price", "reward_risk", "zone_floor", "reason"):
        if column in latest:
            current[column] = latest[column].reindex(current.index)
    if "confirmation" in latest:
        current["confirmations"] = latest["confirmation"].reindex(current.index).map(
            lambda d: [k for k, v in (d or {}).items() if v is True] if isinstance(d, dict) else []
        )
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate alert rules")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="write without printing")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    current, prior, today = snapshots(db)
    current = enrich_from_setups(db, current)

    prices = pd.DataFrame(db.select("prices_daily"))
    closes: dict[str, float] = {}
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
        last = prices.sort_values("date").groupby("symbol").tail(1)
        closes = dict(zip(last["symbol"], pd.to_numeric(last["adj_close"], errors="coerce")))

    positions = pd.DataFrame(db.select("positions"))
    open_positions = (
        positions[positions["exit_date"].isna()].to_dict("records")
        if not positions.empty and "exit_date" in positions
        else (positions.to_dict("records") if not positions.empty else [])
    )

    # Anything already reported recently, so a standing condition is not
    # re-announced every night.
    existing = pd.DataFrame(db.select("alerts"))
    already: set[str] = set()
    if not existing.empty and "payload" in existing:
        existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.date
        recent = existing[
            existing["date"].notna()
            & (existing["date"] >= today - pd.Timedelta(days=DEDUPE_WINDOW_DAYS).to_pytimedelta())
        ]
        already = {
            str((row.get("payload") or {}).get("dedupe_key"))
            for _, row in recent.iterrows()
        }

    found: list[Alert] = []

    with run(JOB, db=db) as log:
        for position in open_positions:
            symbol = position.get("symbol")
            found += rules.for_position(
                position, _record(current, symbol), closes.get(symbol)
            )

        watched = {row["symbol"] for row in db.select("watchlist")} if db.select("watchlist") else set()
        for symbol in current.index:
            today_row = {"symbol": symbol, **current.loc[symbol].to_dict()}
            yesterday_row = _record(prior, symbol)
            if yesterday_row is not None:
                yesterday_row = {"symbol": symbol, **yesterday_row}
            screen = rules.for_screen(today_row, yesterday_row)
            # Screening noise is only worth surfacing for names being followed,
            # unless it is a genuine setup trigger.
            found += [
                a for a in screen
                if symbol in watched or a.rule == "setup_triggered"
            ]

        fresh = rules.rank(rules.suppress_seen(found, already))
        rows = [
            {
                "symbol": a.symbol,
                "date": today.isoformat(),
                "rule": a.rule,
                "message": f"{a.symbol} {a.message}",
                "payload": {**a.payload, "severity": a.severity.value, "dedupe_key": a.dedupe_key},
                "seen": False,
            }
            for a in fresh
        ]
        log.rows_written = db.upsert("alerts", rows, on_conflict="symbol,date,rule")
        log.symbols_ok = len(current)
        log.notes = (
            f"{len(fresh)} alerts ({len(found) - len(fresh)} suppressed as already seen), "
            f"{len(open_positions)} open positions"
        )
        summary = log.notes

    write(OUT, rows)

    if not args.quiet:
        print(f"[{JOB}] {summary}")
        for alert in fresh[:20]:
            print(f"  [{alert.severity.value:8}] {alert.symbol:12} {alert.message}")
        if not fresh:
            print("  nothing worth interrupting for tonight")
    return 0


if __name__ == "__main__":
    sys.exit(main())
