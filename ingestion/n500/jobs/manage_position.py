"""Job: record and close positions from the command line.

    python -m n500.jobs.manage_position open  TATACHEM --qty 50 --price 612 --stop 560 --dry-run
    python -m n500.jobs.manage_position close TATACHEM --price 690 --reason target --dry-run
    python -m n500.jobs.manage_position list --dry-run

The browser is the intended place for this once Supabase is connected — the
schema already permits writes to positions from the anon key. Until then a CLI
is the honest alternative to hand-editing JSON.

Opening a position without a stop is refused. A position with no predetermined
exit is the single most reliable way to turn a six-month thesis into a two-year
one, and the whole alert engine has nothing to say about a position it cannot
measure risk on.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from ..db import Db, run

JOB = "manage_position"

EXIT_REASONS = ("target", "stop", "thesis_broken", "score_decay", "manual")


def _next_id(rows: list[dict]) -> int:
    return max((int(r.get("id") or 0) for r in rows), default=0) + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record and close positions")
    parser.add_argument("action", choices=("open", "close", "list"))
    parser.add_argument("symbol", nargs="?")
    parser.add_argument("--qty", type=float)
    parser.add_argument("--price", type=float)
    parser.add_argument("--stop", type=float)
    parser.add_argument("--target", type=float)
    parser.add_argument("--thesis")
    parser.add_argument("--setup", choices=("momentum", "support", "other"), default="other")
    parser.add_argument("--reason", choices=EXIT_REASONS, default="manual")
    parser.add_argument("--date", help="ISO date; defaults to today")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    existing = db.select("positions")
    when = date.fromisoformat(args.date) if args.date else date.today()

    if args.action == "list":
        if not existing:
            print(f"[{JOB}] no positions recorded")
            return 0
        frame = pd.DataFrame(existing)
        columns = [c for c in ("id", "symbol", "entry_date", "entry_price", "quantity",
                               "stop_price", "target_price", "exit_date", "exit_reason")
                   if c in frame]
        print(frame[columns].to_string(index=False))
        return 0

    if not args.symbol:
        print(f"[{JOB}] a symbol is required for {args.action}", file=sys.stderr)
        return 1
    symbol = args.symbol.upper()

    if args.action == "open":
        if args.qty is None or args.price is None:
            print(f"[{JOB}] --qty and --price are required", file=sys.stderr)
            return 1
        if args.stop is None:
            print(
                f"[{JOB}] --stop is required. A position with no predetermined exit is "
                "how a six-month thesis becomes a two-year one, and the alert engine "
                "cannot measure risk without it.",
                file=sys.stderr,
            )
            return 1
        if args.stop >= args.price:
            print(f"[{JOB}] the stop ({args.stop}) must sit below the entry ({args.price})",
                  file=sys.stderr)
            return 1

        row = {
            "id": _next_id(existing),
            "symbol": symbol,
            "entry_date": when.isoformat(),
            "entry_price": args.price,
            "quantity": args.qty,
            "stop_price": args.stop,
            "target_price": args.target,
            "thesis": args.thesis,
            "setup": args.setup,
            "exit_date": None,
            "exit_price": None,
            "exit_reason": None,
        }
        with run(JOB, db=db) as log:
            log.rows_written = db.upsert("positions", [row], on_conflict="id")
            log.symbols_ok = 1
            risk = (args.price - args.stop) * args.qty
            log.notes = f"opened {symbol}, risking {risk:.0f}"
        risk = (args.price - args.stop) * args.qty
        print(f"[{JOB}] opened {symbol}: {args.qty:g} at {args.price:g}, stop {args.stop:g}")
        print(f"[{JOB}] risk to the stop: {risk:,.0f} ({(args.price / args.stop - 1) * 100:.1f}% away)")
        return 0

    # close
    open_rows = [r for r in existing if r["symbol"] == symbol and not r.get("exit_date")]
    if not open_rows:
        print(f"[{JOB}] no open position in {symbol}", file=sys.stderr)
        return 1
    if args.price is None:
        print(f"[{JOB}] --price is required to close", file=sys.stderr)
        return 1

    position = open_rows[-1]
    entry = float(position["entry_price"])
    quantity = float(position["quantity"])
    with run(JOB, db=db) as log:
        log.rows_written = db.upsert(
            "positions",
            [{**position, "exit_date": when.isoformat(), "exit_price": args.price,
              "exit_reason": args.reason}],
            on_conflict="id",
        )
        log.symbols_ok = 1
        log.notes = f"closed {symbol} for {args.reason}"

    pnl = (args.price - entry) * quantity
    print(f"[{JOB}] closed {symbol} at {args.price:g} ({args.reason}): "
          f"{args.price / entry - 1:+.1%}, {pnl:+,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
