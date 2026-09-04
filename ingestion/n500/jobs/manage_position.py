"""Job: record and close positions from the command line.

    python -m n500.jobs.manage_position open  TATACHEM --qty 50 --price 612 --stop 560 --dry-run
    python -m n500.jobs.manage_position close TATACHEM --price 690 --reason target --dry-run
    python -m n500.jobs.manage_position move-stop TATACHEM --stop 585
    python -m n500.jobs.manage_position move-stop --all --to-suggested --dry-run
    python -m n500.jobs.manage_position capital --set 2000000
    python -m n500.jobs.manage_position list --dry-run

The browser is the intended place for this once Supabase is connected — the
schema already permits writes to positions from the anon key. Until then a CLI
is the honest alternative to hand-editing JSON.

Opening a position without a stop is refused. A position with no predetermined
exit is the single most reliable way to turn a six-month thesis into a two-year
one, and the whole alert engine has nothing to say about a position it cannot
measure risk on.

A stop can be raised and never lowered. That rule is enforced here rather than
left to memory, because lowering one is never a decision — it is always the same
moment, where the price is approaching the level, the reasoning suddenly
available for giving it more room is excellent, and the loss that was defined in
advance quietly becomes undefined. `--force` exists for the genuine case, a
position added to at a higher average, and says what it is doing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from ..db import Db, run

JOB = "manage_position"

EXIT_REASONS = ("target", "stop", "thesis_broken", "score_decay", "manual")


def _suggested_stops(db: Db) -> dict[str, float]:
    """The engine's plan_stop per symbol, from the most recent scoring date."""
    rows = db.select("ts_setups", columns="symbol,date,plan_stop,plan_stop_basis")
    if not rows:
        return {}
    latest = max(r["date"] for r in rows)
    return {
        r["symbol"]: float(r["plan_stop"])
        for r in rows
        if r["date"] == latest and r.get("plan_stop") is not None
    }


def _capital(db: Db, args) -> int:
    """Read or set total capital.

    It cannot be derived from the positions: a tracker sees what it is told
    about and the cash beside it is invisible. Stating it wrong is worse than
    leaving it unset, because every risk percentage on the page divides by it.
    """
    current = db.select("portfolio")
    if args.set_value is None:
        if not current:
            print(f"[{JOB}] no capital set — risk percentages have no denominator")
            return 0
        row = current[0]
        print(f"[{JOB}] capital {float(row['total_capital']):,.0f}, "
              f"risking {float(row['risk_pct']):.1%} per position "
              f"= {float(row['total_capital']) * float(row['risk_pct']):,.0f} a unit")
        return 0

    if args.set_value <= 0:
        print(f"[{JOB}] capital must be positive", file=sys.stderr)
        return 1

    risk_pct = args.risk_pct
    if risk_pct is None:
        risk_pct = float(current[0]["risk_pct"]) if current else 0.01
    if not 0 < risk_pct < 1:
        print(f"[{JOB}] --risk-pct is a fraction, so 0.01 for 1%", file=sys.stderr)
        return 1

    row = {"id": 1, "total_capital": args.set_value, "risk_pct": risk_pct}
    with run(JOB, db=db) as log:
        log.rows_written = db.upsert("portfolio", [row], on_conflict="id")
        log.notes = f"capital {args.set_value:,.0f} at {risk_pct:.1%}"
    print(f"[{JOB}] capital {args.set_value:,.0f}, risking {risk_pct:.1%} "
          f"= {args.set_value * risk_pct:,.0f} a unit")
    return 0


def decide_stop_change(
    current: float | None, new: float | None, *, force: bool = False
) -> tuple[bool, str | None]:
    """Whether to apply a stop change, and why not when the answer is no.

    Pulled out of the job so the one rule that matters can be tested without a
    database in the way: **a stop moves up or not at all.**

    Lowering one is never really a decision. It is always the same moment — the
    price approaching the level, a suddenly excellent reason to give it more
    room, and a loss that was defined in advance quietly becoming undefined.
    `force` is for the genuine case, a position added to at a higher average.
    """
    if new is None:
        return False, "no suggested stop for this symbol"
    if current is None:
        return True, None
    if abs(new - current) < 1e-9:
        return False, "already there"
    if new < current and not force:
        return False, (
            f"would lower the stop {current:g} -> {new:g}; a stop moves up or "
            f"not at all (--force to override)"
        )
    return True, None


def _move_stop(db: Db, args, existing: list[dict]) -> int:
    """Raise the stop on one position or all of them."""
    open_rows = [r for r in existing if not r.get("exit_date")]
    if args.symbol:
        symbol = args.symbol.upper()
        open_rows = [r for r in open_rows if r["symbol"] == symbol]
        if not open_rows:
            print(f"[{JOB}] no open position in {symbol}", file=sys.stderr)
            return 1
    elif not args.all:
        print(f"[{JOB}] name a symbol or pass --all", file=sys.stderr)
        return 1

    suggested = _suggested_stops(db) if args.to_suggested else {}
    if args.to_suggested and not suggested:
        print(f"[{JOB}] no plan_stop values — run compute_zones first", file=sys.stderr)
        return 1

    updates, skipped = [], []
    for row in open_rows:
        symbol = row["symbol"]
        new = suggested.get(symbol) if args.to_suggested else args.stop
        current = row.get("stop_price")
        current = None if current is None else float(current)
        apply, why = decide_stop_change(current, new, force=args.force)
        if not apply:
            skipped.append((symbol, why))
            continue

        updates.append({"id": row["id"], "symbol": symbol, "old": current, "new": new})

    for symbol, why in skipped:
        print(f"[{JOB}] {symbol}: skipped — {why}")

    if not updates:
        print(f"[{JOB}] nothing to change")
        return 0

    with run(JOB, db=db) as log:
        for u in updates:
            db.update("positions", {"stop_price": u["new"]}, where={"id": u["id"]})
            old = "none" if u["old"] is None else f"{u['old']:g}"
            print(f"[{JOB}] {u['symbol']}: stop {old} -> {u['new']:g}")
        log.symbols_ok = len(updates)
        log.rows_written = len(updates)
        log.notes = f"raised {len(updates)} stop(s)"
    return 0


def _next_id(rows: list[dict]) -> int:
    return max((int(r.get("id") or 0) for r in rows), default=0) + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record and close positions")
    parser.add_argument(
        "action", choices=("open", "close", "list", "move-stop", "capital")
    )
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
    parser.add_argument("--all", action="store_true",
                        help="move-stop: every open position")
    parser.add_argument("--to-suggested", action="store_true",
                        help="move-stop: use the engine's plan_stop")
    parser.add_argument("--force", action="store_true",
                        help="move-stop: allow lowering a stop, which is almost never right")
    parser.add_argument("--set", type=float, dest="set_value",
                        help="capital: total capital in rupees")
    parser.add_argument("--risk-pct", type=float,
                        help="capital: fraction risked per position (default 0.01)")
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

    if args.action == "capital":
        return _capital(db, args)

    if args.action == "move-stop":
        return _move_stop(db, args, existing)

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
