"""Job: mark open positions to market.

    python -m n500.jobs.compute_positions --dry-run

Turns the position rows into what you actually need to look at each morning:
what it is worth now, how far the stop is, and — the one most easily missed —
whether the case that justified the entry still holds.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from ..config import REPO_ROOT
from ..db import Db, run
from ..serialise import write

JOB = "compute_positions"
OUT = REPO_ROOT / "web" / "public" / "positions.json"


def mark_to_market(position: dict, close: float | None, score: dict | None) -> dict:
    entry = float(position.get("entry_price") or 0)
    quantity = float(position.get("quantity") or 0)
    stop = position.get("stop_price")
    target = position.get("target_price")

    marked = {
        "id": position.get("id"),
        "symbol": position["symbol"],
        "entry_date": position.get("entry_date"),
        "entry_price": entry,
        "quantity": quantity,
        "stop_price": None if stop is None else float(stop),
        "target_price": None if target is None else float(target),
        "thesis": position.get("thesis"),
        "setup": position.get("setup"),
        "close": close,
    }

    if close and entry:
        marked["return_pct"] = round(close / entry - 1.0, 4)
        marked["pnl"] = round((close - entry) * quantity, 2)
        marked["value"] = round(close * quantity, 2)
        if stop:
            # Negative means the stop is already breached.
            marked["stop_distance_pct"] = round(close / float(stop) - 1.0, 4)
            # What this position can still lose from here, which is the number
            # that should drive sizing rather than the entry-to-stop distance.
            marked["risk_remaining"] = round((close - float(stop)) * quantity, 2)
        if target:
            marked["to_target_pct"] = round(float(target) / close - 1.0, 4)

    if score:
        marked["blended"] = score.get("blended")
        marked["decile"] = score.get("decile")
        marked["quality_score"] = score.get("quality_score")
        flags = [f for f in (score.get("flags") or []) if f.get("verdict") == "fail"]
        marked["failed_gates"] = [f["name"] for f in flags]
        # The alert engine says this loudly; the position view should show it
        # quietly, every day, until it is dealt with.
        marked["thesis_intact"] = not flags

    return marked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mark positions to market")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    positions = pd.DataFrame(db.select("positions"))
    if positions.empty:
        write(OUT, {"positions": [], "totals": {}})
        print(f"[{JOB}] no positions recorded")
        return 0

    open_rows = (
        positions[positions["exit_date"].isna()]
        if "exit_date" in positions else positions
    ).to_dict("records")

    prices = pd.DataFrame(db.select("prices_daily"))
    closes: dict[str, float] = {}
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
        last = prices.sort_values("date").groupby("symbol").tail(1)
        closes = dict(zip(last["symbol"], pd.to_numeric(last["adj_close"], errors="coerce")))

    scores = pd.DataFrame(db.select("scores_daily"))
    latest = (
        scores.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
        if not scores.empty else pd.DataFrame()
    )

    with run(JOB, db=db) as log:
        marked = [
            mark_to_market(
                row,
                closes.get(row["symbol"]),
                latest.loc[row["symbol"]].to_dict()
                if len(latest) and row["symbol"] in latest.index else None,
            )
            for row in open_rows
        ]
        log.symbols_ok = len(marked)

        invested = sum(p["entry_price"] * p["quantity"] for p in marked)
        value = sum(p.get("value") or 0 for p in marked)
        totals = {
            "positions": len(marked),
            "invested": round(invested, 2),
            "value": round(value, 2),
            "pnl": round(value - invested, 2),
            "return_pct": round(value / invested - 1.0, 4) if invested else None,
            "risk_remaining": round(sum(p.get("risk_remaining") or 0 for p in marked), 2),
            "thesis_broken": sum(1 for p in marked if p.get("thesis_intact") is False),
        }
        log.notes = f"{len(marked)} open, {totals['thesis_broken']} with a broken thesis"

    write(OUT, {"positions": marked, "totals": totals})
    print(f"[{JOB}] {len(marked)} open positions -> {OUT}")
    if totals["thesis_broken"]:
        print(f"[{JOB}] {totals['thesis_broken']} now fail a hard gate — the reason for "
              "holding has changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
