"""Job: assert the things that must be true after a run.

    python -m n500.jobs.verify

`doctor` answers "is this wired up?" — credentials, tables, freshness. This
answers a different and harder question: "is what came out of tonight's run
actually right?"

Why it exists
-------------
Three faults reached production in a single evening, and every one produced
data that looked entirely plausible:

  * 210 companies that had left the index were scored and offered as sized
    positions, some of them no longer trading;
  * page 700 of a keyset-less read was walking 700,000 rows to return 1,000,
    quietly, until it crossed a statement timeout;
  * a step fetched 600,000 rows into a variable nothing read.

None of them raised an exception where the damage was done. Two were found by
me looking at a number and thinking it was the wrong size; one was found only
because it eventually got slow enough to fail. That is not a process.

An invariant is cheaper than an inspection and it does not get tired. Each check
below states what must hold and what it means when it does not, and the job
exits non-zero if any of them break, so the nightly run reports a failure rather
than a success with bad data behind it.

What belongs here
-----------------
Facts that must be true of *any* correct run, not properties of a good market
day. "Every scored symbol is a current constituent" belongs; "at least twenty
stocks are near support" does not — that is a fact about the market, and a
screener that finds nothing on a given day is working.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from ..db import Db, run

JOB = "verify"

# A trading day is missed for holidays and long weekends; beyond this the
# pipeline has stopped and nobody noticed.
MAX_STALE_DAYS = 5

# The Nifty 500 is 500 by construction, minus whatever has no price history yet.
MIN_SCORED = 450
MAX_SCORED = 520


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # A warning is a thing worth seeing; a failure means the output is wrong and
    # should not be acted on.
    fatal: bool = True


def _latest(frame: pd.DataFrame, column: str = "date") -> pd.DataFrame:
    return frame[frame[column] == frame[column].max()] if len(frame) else frame


def check_scores(db: Db) -> list[Check]:
    scores = pd.DataFrame(
        db.select(
            "scores_daily",
            columns="symbol,date,blended,conviction,decile",
            since=("date", (date.today() - timedelta(days=30)).isoformat()),
        )
    )
    if scores.empty:
        return [Check("scores exist", False, "scores_daily has no recent rows")]

    today = _latest(scores)
    as_of = pd.to_datetime(today["date"].iloc[0]).date()
    stale = (date.today() - as_of).days
    out = [
        Check(
            "scores are fresh",
            stale <= MAX_STALE_DAYS,
            f"latest scoring date is {as_of}, {stale} days old",
        ),
        Check(
            "scored count is sane",
            MIN_SCORED <= len(today) <= MAX_SCORED,
            f"{len(today)} symbols scored (expected {MIN_SCORED}-{MAX_SCORED})",
        ),
        Check(
            "no symbol scored twice",
            not today["symbol"].duplicated().any(),
            f"{int(today['symbol'].duplicated().sum())} duplicate symbols on {as_of}",
        ),
    ]

    for column in ("blended", "conviction"):
        values = pd.to_numeric(today[column], errors="coerce").dropna()
        out.append(
            Check(
                f"{column} is inside 0-100",
                values.between(0, 100).all() if len(values) else True,
                f"{len(values)} values, range "
                f"{values.min():.1f}-{values.max():.1f}" if len(values) else "none scored",
            )
        )

    conviction = pd.to_numeric(today["conviction"], errors="coerce").notna().sum()
    out.append(
        Check(
            "conviction covers most of the universe",
            conviction >= len(today) * 0.5,
            f"{conviction} of {len(today)} have a conviction score",
        )
    )
    return out


def check_universe(db: Db) -> list[Check]:
    """The one that would have caught tonight's worst bug.

    312 companies that left the index are kept so the backtest can see the ones
    that failed. They are marked inactive, and for one run every job downstream
    of prices scored them anyway — putting delisted names into the screener and
    the buy list with position sizes attached.
    """
    stocks = pd.DataFrame(db.select("stocks", "symbol,is_active"))
    scores = pd.DataFrame(
        db.select(
            "scores_daily",
            columns="symbol,date",
            since=("date", (date.today() - timedelta(days=30)).isoformat()),
        )
    )
    if stocks.empty or scores.empty:
        return [Check("universe is loaded", False, "stocks or scores_daily is empty")]

    inactive = set(stocks.loc[stocks["is_active"] == False, "symbol"])  # noqa: E712
    scored = set(_latest(scores)["symbol"])
    leaked = sorted(scored & inactive)

    return [
        Check(
            "only current constituents are scored",
            not leaked,
            f"{len(leaked)} delisted symbols in the screener"
            + (f": {', '.join(leaked[:5])}…" if leaked else ""),
        )
    ]


def check_plans(db: Db) -> list[Check]:
    setups = pd.DataFrame(
        db.select(
            "ts_setups",
            columns="symbol,date,plan_stop,plan_target",
            since=("date", (date.today() - timedelta(days=30)).isoformat()),
        )
    )
    if setups.empty:
        return [Check("plans exist", False, "ts_setups has no recent rows", fatal=False)]

    today = _latest(setups)
    prices = pd.DataFrame(
        db.select(
            "prices_daily",
            columns="symbol,date,adj_close",
            since=("date", (date.today() - timedelta(days=14)).isoformat()),
        )
    )
    last = (
        prices.sort_values("date").groupby("symbol").tail(1).set_index("symbol")["adj_close"]
        if len(prices) else pd.Series(dtype="float64")
    )

    joined = today.assign(close=today["symbol"].map(pd.to_numeric(last, errors="coerce")))
    joined["plan_stop"] = pd.to_numeric(joined["plan_stop"], errors="coerce")
    joined["plan_target"] = pd.to_numeric(joined["plan_target"], errors="coerce")
    usable = joined.dropna(subset=["plan_stop", "close"])

    above = usable[usable["plan_stop"] >= usable["close"]]
    targets = joined.dropna(subset=["plan_target", "close"])
    below = targets[targets["plan_target"] <= targets["close"]]

    return [
        Check(
            "every stop is below its price",
            above.empty,
            f"{len(above)} stops at or above the close"
            + (f": {', '.join(above['symbol'].head(5))}" if len(above) else ""),
        ),
        Check(
            "every target is above its price",
            below.empty,
            f"{len(below)} targets at or below the close"
            + (f": {', '.join(below['symbol'].head(5))}" if len(below) else ""),
        ),
        Check(
            "most symbols have a plan",
            len(usable) >= len(today) * 0.8,
            f"{len(usable)} of {len(today)} have a usable stop",
        ),
    ]


def check_positions(db: Db) -> list[Check]:
    positions = [r for r in db.select("positions") if not r.get("exit_date")]
    if not positions:
        return [Check("positions", True, "none open", fatal=False)]

    stocks = {r["symbol"] for r in db.select("stocks", "symbol")}
    unknown = [p["symbol"] for p in positions if p["symbol"] not in stocks]
    stopless = [p["symbol"] for p in positions if p.get("stop_price") is None]

    return [
        Check(
            "every holding is a known symbol",
            not unknown,
            f"{len(unknown)} unknown: {', '.join(unknown)}" if unknown else f"{len(positions)} open",
        ),
        Check(
            "every holding has a stop",
            not stopless,
            f"{len(stopless)} without one: {', '.join(stopless)}"
            if stopless else "all have one",
            fatal=False,
        ),
    ]


def check_prices(db: Db) -> list[Check]:
    prices = pd.DataFrame(
        db.select(
            "prices_daily",
            columns="symbol,date,adj_close",
            since=("date", (date.today() - timedelta(days=14)).isoformat()),
        )
    )
    if prices.empty:
        return [Check("prices are fresh", False, "no price rows in the last fortnight")]

    latest = pd.to_datetime(prices["date"]).max().date()
    stale = (date.today() - latest).days
    closes = pd.to_numeric(prices["adj_close"], errors="coerce")

    return [
        Check("prices are fresh", stale <= MAX_STALE_DAYS,
              f"latest bar is {latest}, {stale} days old"),
        Check("no non-positive prices", (closes.dropna() > 0).all(),
              f"{int((closes.dropna() <= 0).sum())} rows at or below zero"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the last run's output")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    checks: list[Check] = []

    with run(JOB, db=db) as log:
        for group in (check_prices, check_universe, check_scores, check_plans, check_positions):
            try:
                checks.extend(group(db))
            except Exception as exc:  # noqa: BLE001
                checks.append(Check(group.__name__, False, f"check itself failed: {exc}"))

        failed = [c for c in checks if not c.ok and c.fatal]
        warned = [c for c in checks if not c.ok and not c.fatal]
        for c in failed:
            log.error(c.name, c.detail)
        log.symbols_ok = sum(1 for c in checks if c.ok)
        log.notes = f"{len(checks) - len(failed) - len(warned)} passed, {len(failed)} failed"

    print(f"{'=' * 66}\n  VERIFY — what must be true after a run\n{'=' * 66}")
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.fatal else "warn")
        print(f"  [{mark}] {c.name:<38} {c.detail}")

    failed = [c for c in checks if not c.ok and c.fatal]
    if failed:
        print(f"\n  {len(failed)} check(s) failed. The output is wrong, not merely stale —")
        print("  do not act on the screener until these are resolved.")
        return 1

    print("\n  Everything that must be true is true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
