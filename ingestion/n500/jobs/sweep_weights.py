"""Job: search the blend weights against the decile curve.

    python -m n500.jobs.sweep_weights --dry-run           # builds the panel, ~20 min
    python -m n500.jobs.sweep_weights --dry-run --reuse   # instant, from cache

The expensive half — scoring Q, V, T-M and T-S for every symbol at every
rebalance — does not depend on the weights at all, so it runs once and is
cached. Every weight combination after that is arithmetic over a table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest import engine, pointintime, sweep
from ..config import REPO_ROOT, settings
from ..db import Db, run
from .run_backtest import load_histories

JOB = "sweep_weights"
PANEL_PATH = REPO_ROOT / "data" / "backtest" / "panel.csv"
w = settings.blend_weights
INCUMBENT = sweep.Candidate(
    w["quality"], w["value"], w["technical"],
    revision=w.get("revision", 0.0), ownership=w.get("ownership", 0.0),
)


def build_panel(db: Db, *, hold: int, limit: int | None) -> pd.DataFrame:
    """One row per symbol per rebalance: the pillars, and what happened next."""
    histories, fundamentals, _ = load_histories(db, limit=limit)
    if not histories:
        raise SystemExit(f"[{JOB}] no symbol has enough history")

    calendar = max((h.daily.index for h in histories.values()), key=len)
    dates = engine.month_end_dates(calendar, warmup=pointintime.WARMUP_BARS, forward=hold)
    if not dates:
        raise SystemExit(f"[{JOB}] history is too short for a {hold}-session hold")

    rows: list[dict] = []
    for as_of in dates:
        frame = pointintime.score_cross_section(histories, fundamentals, as_of)
        if frame.empty:
            continue
        for symbol, row in frame.iterrows():
            history = histories[symbol]
            base = history.index_at(as_of)
            if base is None or base + hold >= len(history.daily):
                continue
            entry = float(history.daily["open"].iloc[base + 1])
            exit_price = float(history.daily["close"].iloc[base + hold])
            if entry <= 0:
                continue
            rows.append(
                {
                    "as_of": as_of,
                    "symbol": symbol,
                    "sector": row.get("sector"),
                    "excluded": bool(row.get("excluded", False)),
                    "quality_score": row.get("quality_score"),
                    "value_score": row.get("value_score"),
                    "technical": row.get("technical"),
                    "setup": row.get("winning_setup"),
                    "forward_return": exit_price / entry - 1.0,
                    # Every raw feature travels with the row so `feature_ic`
                    # can put each on trial individually. Cheap to carry and
                    # the only way to tell a pillar that works from one that is
                    # riding on the pillar beside it.
                    **{
                        name: row.get(name)
                        for name in sweep.FEATURES
                        if name in row.index
                    },
                }
            )
        print(f"[{JOB}]   {as_of}: {len(rows)} rows so far", flush=True)

    panel = pd.DataFrame(rows)
    # An excluded business never reaches the screener, so it must not shape the
    # weights either.
    return panel[~panel["excluded"]].drop(columns=["excluded"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep blend weights")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hold", type=int, default=126)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--reuse", action="store_true", help="use the cached panel")
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    args = parser.parse_args(argv)

    if args.reuse and args.panel.exists():
        panel = pd.read_csv(args.panel)
        print(f"[{JOB}] reusing {len(panel)} rows from {args.panel}")
    else:
        db = Db(force_dry_run=args.dry_run)
        with run(JOB, db=db) as log:
            panel = build_panel(db, hold=args.hold, limit=args.limit or None)
            log.rows_written = len(panel)
            log.notes = f"{len(panel)} scored observations"
        args.panel.parent.mkdir(parents=True, exist_ok=True)
        panel.to_csv(args.panel, index=False)
        print(f"[{JOB}] panel of {len(panel)} rows -> {args.panel}")

    if panel.empty:
        print(f"[{JOB}] panel is empty", file=sys.stderr)
        return 1

    features = sweep.feature_ic(panel)
    if not features.empty:
        _report_features(features)
        features.to_csv(args.panel.parent / "feature_ic.csv", index=False)

    results = sweep.search(panel, include=[INCUMBENT])
    if results.empty:
        print(f"[{JOB}] no candidate produced a usable decile table", file=sys.stderr)
        return 1

    _report(panel, results)
    out = args.panel.parent / "weight_sweep.csv"
    results.sort_values("median_rho", ascending=False).to_csv(out, index=False)
    print(f"\n[{JOB}] full surface -> {out}")
    return 0


def _report_features(features: pd.DataFrame) -> None:
    """Each feature alone, before any of them are combined."""
    print(f"\n{'=' * 74}")
    print("  EACH FEATURE ON ITS OWN — does this number predict anything?")
    print(f"{'=' * 74}")
    print("  IC is signed the way the theory says it should point, so a positive")
    print("  number means the idea works as stated and a negative one means it")
    print("  works backwards. A t below 2 means the sample cannot tell.\n")

    print(f"  {'feature':<24} {'want':>4} {'IC':>7} {'t':>6} {'dates+':>7} {'n':>7}")
    print(f"  {'-' * 60}")
    for _, row in features.iterrows():
        marker = "*" if abs(row["t"]) >= 2 else " "
        positive = row.get("positive_dates")
        share = f"{positive * 100:>6.0f}%" if pd.notna(positive) else "     —"
        print(
            f"{marker} {row['feature']:<24} {row['expected_sign']:>4} "
            f"{row['ic']:>+7.3f} {row['t']:>+6.2f} {share} {int(row['n']):>7}"
        )

    significant = features[features["t"].abs() >= 2]
    print()
    if significant.empty:
        print("  Nothing clears t = 2. On this sample no single feature is")
        print("  demonstrably informative, which is a result about the sample as")
        print("  much as about the features — fourteen overlapping six-month")
        print("  windows in one market is not much to ask a question of.")
    else:
        for _, row in significant.iterrows():
            direction = "as predicted" if row["ic"] > 0 else "BACKWARDS"
            print(f"  {row['feature']}: IC {row['ic']:+.3f}, t {row['t']:+.2f} — {direction}.")

    print("\n  'dates+' is the share of rebalances where the correlation had the")
    print("  predicted sign. A feature at 50% is a coin flip whatever its mean IC.")


def _report(panel: pd.DataFrame, results: pd.DataFrame) -> None:
    dates = sorted(panel["as_of"].unique())
    print(f"\n{'=' * 74}")
    print("  WEIGHT SWEEP — scored on whether the decile curve orders, not on returns")
    print(f"{'=' * 74}")
    print(f"  panel        {len(panel)} observations, {len(dates)} rebalances, "
          f"{panel['symbol'].nunique()} symbols")
    print(f"  candidates   {len(results)} weight combinations (Q/V/T, summing to 100)")

    incumbent = results[results["weights"] == INCUMBENT.label]
    ranked = results.sort_values("ic", ascending=False)

    print(f"\n  {'Q/V/T':>10} {'IC':>7} {'t':>6} {'stable':>7} {'decile rho':>11} "
          f"{'top>=25%':>9} {'top p10':>8}")
    print(f"  {'-' * 72}")

    def line(row, marker=" "):
        print(f"{marker} {row['weights']:>10} {row['ic']:>+7.3f} {row['ic_t']:>+6.2f} "
              f"{'yes' if row['stable'] else 'no':>7} {row['median_rho']:>+11.2f} "
              f"{row['top_hit25'] * 100:>8.0f}% {row['top_p10'] * 100:>7.1f}%")

    print("\n  best by information coefficient:")
    for _, row in ranked.head(6).iterrows():
        line(row)

    stable = ranked[ranked["stable"]]
    if not stable.empty:
        print("\n  best that also has a positive IC in BOTH halves:")
        for _, row in stable.head(4).iterrows():
            line(row, marker="*")
    else:
        print("\n  no candidate had a positive IC in both halves of the period.")

    if not incumbent.empty:
        print("\n  the incumbent, for comparison:")
        line(incumbent.iloc[0], marker=">")

    print("\n  each pillar on its own:")
    for label in ("100/0/0", "0/100/0", "0/0/100"):
        row = results[results["weights"] == label]
        if not row.empty:
            line(row.iloc[0])

    best = ranked.iloc[0]
    print(f"\n  Is any of this significant?")
    print(f"    best IC is {best['ic']:+.3f} with t = {best['ic_t']:+.2f} over "
          f"{int(best['dates'])} dates.")
    if abs(best["ic_t"]) < 2:
        print("    That is inside the noise. On this sample no weighting is")
        print("    demonstrably better than any other, and the ordering seen in the")
        print("    decile table does not survive being measured properly.")
    else:
        print("    A t above 2 is suggestive — but the holding periods overlap, so")
        print("    the true standard error is larger than this one and the bar")
        print("    should be higher than the usual 2.")

    print("\n  How to read this:")
    print("    IC is the mean within-date rank correlation between the score and")
    print("    the next six months, across every scored stock. It replaced a rank")
    print("    correlation over ten decile medians, which reported +0.89 for a")
    print("    stock-level signal of +0.035 — ten noisy points make that statistic")
    print("    far more confident than the data supports.")
    print("    A grid of 67 candidates on 14 overlapping rebalances in one regime")
    print("    will always produce a winner. Whether it means anything is what")
    print("    the t-statistic is for.")


if __name__ == "__main__":
    sys.exit(main())
