"""Job: fit on the early period, score the late one once.

    python -m n500.jobs.run_holdout                      # rebuilds the panel
    python -m n500.jobs.run_holdout --reuse              # from the cached panel
    python -m n500.jobs.run_holdout --split 2025-01-01

Reads the same panel the sweep builds. Everything decided — which features, and
which way each points — comes from the training rows; the test rows are touched
once, at the end, to print a number.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest import holdout, sweep
from ..config import REPO_ROOT
from ..db import Db, run
from .sweep_weights import PANEL_PATH, build_panel

JOB = "run_holdout"
DEFAULT_SPLIT = date(2025, 1, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out evaluation")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hold", type=int, default=126)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    parser.add_argument("--split", type=date.fromisoformat, default=DEFAULT_SPLIT)
    parser.add_argument("--select-t", type=float, default=holdout.SELECT_T)
    args = parser.parse_args(argv)

    if args.reuse and args.panel.exists():
        panel = pd.read_csv(args.panel)
        print(f"[{JOB}] reusing {len(panel)} rows from {args.panel}")
    else:
        db = Db(force_dry_run=args.dry_run)
        with run(JOB, db=db) as log:
            panel = build_panel(db, hold=args.hold, limit=args.limit or None)
            log.rows_written = len(panel)
        args.panel.parent.mkdir(parents=True, exist_ok=True)
        panel.to_csv(args.panel, index=False)

    train, test = holdout.split(panel, args.split)
    if train.empty or test.empty:
        print(f"[{JOB}] split at {args.split} leaves one side empty", file=sys.stderr)
        return 1

    fitted = holdout.fit(train, sweep.FEATURES, select_t=args.select_t)
    _report(fitted, train, test, args.split, args.select_t)

    out = args.panel.parent / "holdout.csv"
    _table(fitted, test).to_csv(out, index=False)
    print(f"\n[{JOB}] per-feature detail -> {out}")
    return 0


def _table(fitted: holdout.Fit, test: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in fitted.training.iterrows():
        name = row["feature"]
        raw = pd.to_numeric(test[name], errors="coerce") * row["sign"]
        stats = holdout.evaluate(test, raw)
        rows.append(
            {
                **row.to_dict(),
                "test_ic": stats["ic"],
                "test_t": stats["t"],
                "test_dates": stats["dates"],
                "test_positive_dates": stats["positive_dates"],
                # The number that matters: did the training sign hold up?
                "held": bool(np.isfinite(stats["ic"]) and stats["ic"] > 0),
            }
        )
    return pd.DataFrame(rows)


def _report(
    fitted: holdout.Fit, train: pd.DataFrame, test: pd.DataFrame,
    split: date, select_t: float,
) -> None:
    table = _table(fitted, test)
    train_dates = pd.to_datetime(train["as_of"]).dt.date
    test_dates = pd.to_datetime(test["as_of"]).dt.date

    print(f"\n{'=' * 84}")
    print("  HELD-OUT TEST — fit on the early period, look at the late one once")
    print(f"{'=' * 84}")
    print(f"  train   {train_dates.min()} .. {train_dates.max()}   "
          f"{len(train):>6,} obs, {train['as_of'].nunique()} rebalances")
    print(f"  test    {test_dates.min()} .. {test_dates.max()}   "
          f"{len(test):>6,} obs, {test['as_of'].nunique()} rebalances")
    print(f"  rule    a feature is selected if |t| >= {select_t} in training; its "
          f"direction is taken from training too")

    print(f"\n  {'feature':<30} {'train IC':>9} {'t':>6} {'sign':>5} | "
          f"{'test IC':>8} {'t':>6} {'held':>5}")
    print(f"  {'-' * 78}")
    for _, r in table.iterrows():
        mark = "*" if r["selected"] else " "
        sign = "+" if r["sign"] > 0 else "-"
        held = "yes" if r["held"] else "NO"
        test_ic = f"{r['test_ic']:>+8.3f}" if np.isfinite(r["test_ic"]) else "       —"
        test_t = f"{r['test_t']:>+6.2f}" if np.isfinite(r["test_t"]) else "     —"
        print(f"{mark} {r['feature']:<30} {r['train_ic']:>+9.3f} {r['train_t']:>+6.2f} "
              f"{sign:>5} | {test_ic} {test_t} {held:>5}")

    chosen = table[table["selected"]]
    print(f"\n  {len(chosen)} feature(s) selected in training. Starred above.")
    if len(chosen):
        held = int(chosen["held"].sum())
        print(f"  Of those, {held} of {len(chosen)} kept the same direction out of sample.")

    train_c = holdout.evaluate(train, fitted.composite(train))
    test_c = holdout.evaluate(test, fitted.composite(test))
    print(f"\n  Equal-weighted composite of the selected features:")
    print(f"    in training   IC {train_c['ic']:+.3f}  t {train_c['t']:+.2f}  "
          f"over {train_c['dates']} dates")
    print(f"    held out      IC {test_c['ic']:+.3f}  t {test_c['t']:+.2f}  "
          f"over {test_c['dates']} dates")

    if np.isfinite(test_c["ic"]):
        shrink = (
            1.0 - test_c["ic"] / train_c["ic"]
            if train_c["ic"] not in (0, np.nan) else np.nan
        )
        print(f"    the composite kept {100 * (1 - shrink):.0f}% of its training IC")

    print("\n  How to read this:")
    print("    Training IC is not evidence — every feature here was chosen while")
    print("    that period was visible. The test column is the claim. A feature")
    print("    that reverses sign out of sample was an artefact of searching, and")
    print("    the composite is the honest estimate of what the whole exercise is")
    print("    worth going forward.")
    print("    Consulting this twice turns it back into a training set. If the")
    print("    numbers disappoint, the options are to accept them or to gather")
    print("    more data — not to adjust the features and run it again.")


if __name__ == "__main__":
    sys.exit(main())
