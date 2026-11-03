"""Job: lock this month's picks, once.

    python -m n500.jobs.compute_monthly_picks
    python -m n500.jobs.compute_monthly_picks --dry-run

Does nothing once the month has picks, so re-running or re-scoring can never
change them. See n500.scoring.picks for the rule.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ..db import Db, run
from ..scoring import picks

JOB = "compute_monthly_picks"


def first_session(db: Db, month: str) -> str:
    """The earliest scored date in `month` ('YYYY-MM'), 31 one-row reads at most."""
    dates = [str(d) for d in db.recent_values("scores_daily", "date", 31)]
    return min(d for d in dates if d.startswith(month))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lock this month's picks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    latest = db.recent_values("scores_daily", "date", 1)
    if not latest:
        print(f"[{JOB}] no scores yet", file=sys.stderr)
        return 1

    month = str(latest[0])[:7]
    month_start = date.fromisoformat(f"{month}-01").isoformat()
    existing = db.select("monthly_picks", "month,rank,symbol", where={"month": month_start})
    if existing:
        names = ", ".join(r["symbol"] for r in sorted(existing, key=lambda r: r["rank"]))
        print(f"[{JOB}] {month} already locked: {names}")
        return 0

    with run(JOB, db=db) as log:
        as_of = first_session(db, month)
        stocks = db.select("stocks", "symbol,sector,instrument_type")
        portfolio = db.select("portfolio", "id,total_capital")
        position_budget, risk_budget = picks.budgets(
            float(portfolio[0]["total_capital"]) if portfolio else None
        )
        chosen = picks.choose(
            db.select("scores_daily", "symbol,date,conviction,close,flags", where={"date": as_of}),
            instruments={s["symbol"]: s.get("instrument_type") or "equity" for s in stocks},
            sectors={s["symbol"]: s.get("sector") for s in stocks},
            plans={p["symbol"]: p for p in db.select(
                "ts_setups", "symbol,date,plan_stop,plan_stop_basis,plan_target",
                where={"date": as_of})},
            held={p["symbol"] for p in db.select("positions", "id,symbol,exit_date")
                  if not p.get("exit_date")},
        )
        rows = [{**c, "month": month_start, "as_of": as_of,
                 "position_budget": position_budget, "risk_budget": risk_budget}
                for c in chosen]
        log.rows_written = db.upsert("monthly_picks", rows, on_conflict="month,rank")
        log.symbols_ok = len(rows)
        log.notes = f"{month} from {as_of}: " + ", ".join(r["symbol"] for r in rows)
        summary = log.notes

    print(f"[{JOB}] locked {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
