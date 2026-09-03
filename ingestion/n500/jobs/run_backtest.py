"""Job: replay the strategy over history.

    python -m n500.jobs.run_backtest --dry-run
    python -m n500.jobs.run_backtest --dry-run --setup support --hold 126

Prints the statistics and, unless told otherwise, writes the trade list and the
per-decile table so the numbers can be checked rather than taken on trust.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import numpy as np
import pandas as pd

from ..backtest import engine, pointintime
from ..config import REPO_ROOT, settings
from ..db import Db, run
from ..jobs.compute_technicals import adjusted_frame
from ..jobs.compute_zones import to_weekly
from ..sources.nse_index import BENCHMARK

JOB = "run_backtest"
DEFAULT_WEIGHTS = settings.blend_weights


def load_histories(db: Db, *, limit: int | None = None) -> tuple[dict, dict, pd.Series | None]:
    prices = pd.DataFrame(db.select("prices_daily"))
    if prices.empty:
        raise SystemExit(f"[{JOB}] no prices — run load_prices first")

    stocks = pd.DataFrame(db.select("stocks")).set_index("symbol")
    ratios = pd.DataFrame(db.select("company_ratios"))
    ratios = ratios.set_index("symbol") if not ratios.empty else pd.DataFrame()

    index_close = None
    index_rows = pd.DataFrame(db.select("index_prices"))
    if not index_rows.empty:
        bench = index_rows[index_rows["index_name"] == BENCHMARK].copy()
        if not bench.empty:
            bench["date"] = pd.to_datetime(bench["date"])
            index_close = bench.set_index("date")["close"].astype("float64").sort_index()

    annual_all = pd.DataFrame(db.select("fundamentals_y"))
    quarterly_all = pd.DataFrame(db.select("fundamentals_q"))
    holding_all = pd.DataFrame(db.select("shareholding"))
    for frame, column in (
        (annual_all, "period_end"), (quarterly_all, "period_end"), (holding_all, "quarter_end")
    ):
        if not frame.empty:
            frame.sort_values(column, inplace=True)

    annual_by = dict(tuple(annual_all.groupby("symbol"))) if not annual_all.empty else {}
    quarterly_by = dict(tuple(quarterly_all.groupby("symbol"))) if not quarterly_all.empty else {}
    holding_by = dict(tuple(holding_all.groupby("symbol"))) if not holding_all.empty else {}

    histories: dict[str, pointintime.SymbolHistory] = {}
    fundamentals: dict[str, dict] = {}

    symbols = sorted(prices["symbol"].unique())
    if limit:
        symbols = symbols[:limit]

    grouped = dict(tuple(prices.groupby("symbol")))
    for symbol in symbols:
        frame = grouped.get(symbol)
        if frame is None or len(frame) < pointintime.WARMUP_BARS + 40:
            continue
        daily = adjusted_frame(frame)
        weekly = to_weekly(daily)
        if len(weekly) < 40:
            continue

        shares = None
        if len(ratios) and symbol in ratios.index:
            cap = pd.to_numeric(ratios.loc[symbol].get("market_cap_cr"), errors="coerce")
            price_now = pd.to_numeric(ratios.loc[symbol].get("current_price"), errors="coerce")
            if not pd.isna(cap) and not pd.isna(price_now) and price_now > 0:
                shares = float(cap) / float(price_now)

        stock = stocks.loc[symbol].to_dict() if symbol in stocks.index else {}
        sector = stock.get("sector")
        histories[symbol] = pointintime.prepare(
            symbol,
            daily,
            sector=sector,
            is_financial=(stock.get("company_type") == "financial")
            or sector == "Financial Services",
            weekly=weekly,
            index_close=index_close,
            shares_cr=shares,
        )
        fundamentals[symbol] = {
            "annual": annual_by.get(symbol, pd.DataFrame()),
            "quarterly": quarterly_by.get(symbol, pd.DataFrame()),
            "holding": holding_by.get(symbol, pd.DataFrame()),
        }

    return histories, fundamentals, index_close


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the strategy over history")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hold", type=int, default=126, help="trading days held (126 ~ 6 months)")
    parser.add_argument("--size", type=int, default=20, help="portfolio size")
    parser.add_argument("--setup", choices=["all", "momentum", "support"], default="all")
    parser.add_argument("--quality-weight", type=float, default=DEFAULT_WEIGHTS["quality"])
    parser.add_argument("--value-weight", type=float, default=DEFAULT_WEIGHTS["value"])
    parser.add_argument("--technical-weight", type=float, default=DEFAULT_WEIGHTS["technical"])
    parser.add_argument("--limit", type=int, default=0, help="symbols, for a quick pass")
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "backtest"))
    args = parser.parse_args(argv)

    weights = {
        "quality": args.quality_weight,
        "value": args.value_weight,
        "technical": args.technical_weight,
    }

    db = Db(force_dry_run=args.dry_run)
    histories, fundamentals, index_close = load_histories(db, limit=args.limit or None)
    if not histories:
        print(f"[{JOB}] no symbol has enough history", file=sys.stderr)
        return 1

    calendar = max((h.daily.index for h in histories.values()), key=len)
    dates = engine.month_end_dates(
        calendar, warmup=pointintime.WARMUP_BARS, forward=args.hold
    )
    if not dates:
        print(
            f"[{JOB}] history is too short: {len(calendar)} sessions, and a run needs "
            f"{pointintime.WARMUP_BARS} to warm up plus {args.hold} to see the outcome",
            file=sys.stderr,
        )
        return 1

    cache: dict[date, pd.DataFrame] = {}

    def rank_at(as_of: date):
        frame = pointintime.score_cross_section(histories, fundamentals, as_of)
        if frame.empty:
            return frame
        frame["score"] = pointintime.blend(frame, weights)
        frame["setup"] = frame["winning_setup"]
        if args.setup != "all":
            frame = frame[frame["setup"] == args.setup]
        present = frame["score"].notna()
        frame["decile"] = np.nan
        if present.sum() >= 10:
            frame.loc[present, "decile"] = (
                pd.qcut(frame.loc[present, "score"].rank(method="first"), 10, labels=False) + 1
            )
        cache[as_of] = frame
        return frame

    def price_lookup(symbol: str, on: date, offset: int, *, field: str = "close"):
        history = histories.get(symbol)
        if history is None:
            return None
        base = history.index_at(on)
        if base is None:
            return None
        target = base + offset
        if target >= len(history.daily):
            return None
        if field == "date":
            return history.daily.index[target].date()
        return float(history.daily[field].iloc[target])

    observations: list[dict] = []

    with run(JOB, db=db) as log:
        result = engine.simulate(
            dates=dates,
            rank_at=rank_at,
            price_lookup=price_lookup,
            portfolio_size=args.size,
            holding_days=args.hold,
        )
        # Every scored stock's forward return, not just the traded ones.
        for as_of, frame in cache.items():
            for symbol, row in frame.iterrows():
                if pd.isna(row.get("score")):
                    continue
                entry = price_lookup(symbol, as_of, 1, field="open")
                exit_price = price_lookup(symbol, as_of, args.hold, field="close")
                if entry is None or exit_price is None or entry <= 0:
                    continue
                observations.append(
                    {
                        "as_of": as_of,
                        "symbol": symbol,
                        "decile": row.get("decile"),
                        "setup": row.get("setup"),
                        "score": row.get("score"),
                        "forward_return": exit_price / entry - 1.0,
                    }
                )

        stats = engine.summarise(result, holding_days=args.hold, benchmark=index_close)
        log.symbols_ok = len(histories)
        log.notes = f"{stats.get('trades', 0)} trades over {len(dates)} rebalances"

    study = engine.decile_study(pd.DataFrame(observations))
    _report(stats, dates, histories, args)
    _report_deciles(study, args)

    out = pd.Series(args.out).iloc[0]
    from pathlib import Path

    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    result.frame().to_csv(directory / "trades.csv", index=False)
    pd.DataFrame(observations).to_csv(directory / "observations.csv", index=False)
    if not study.empty:
        study.to_csv(directory / "deciles.csv", index=False)
    (directory / "summary.json").write_text(json.dumps(stats, indent=2, default=str))
    print(f"\n[{JOB}] trades, observations and deciles -> {directory}")
    return 0


def _report_deciles(study: pd.DataFrame, args) -> None:
    if study.empty:
        print("\n  Not enough observations for a decile table.")
        return
    print(f"\n{'=' * 62}")
    print(f"  WHAT A SCORE HAS BEEN WORTH — every scored stock, {args.hold}-session forward return")
    print(f"{'=' * 62}")
    print(f"  {'decile':>7} {'n':>6} {'median':>8} {'>=25%':>7} {'p10':>8} {'p90':>8}")
    for _, row in study.iterrows():
        print(
            f"  {int(row['decile']):>7} {int(row['n']):>6} {row['median'] * 100:>7.1f}% "
            f"{row['hit_rate_25pct'] * 100:>6.0f}% {row['p10'] * 100:>7.1f}% {row['p90'] * 100:>7.1f}%"
        )
    verdict = engine.rank_quality(study)
    print()
    print(f"  Does the score rank? {verdict.get('verdict')}")
    if "median_rho" in verdict:
        print(f"    rank correlation of decile vs median return  {verdict['median_rho']:+.2f}")
        print(f"    rank correlation of decile vs >=25% rate      {verdict['hit_rate_rho']:+.2f}")
        print(f"    top decile median minus bottom decile median  "
              f"{verdict['top_minus_bottom_median'] * 100:+.1f}pp")

    top = study[study["decile"] == study["decile"].max()]
    if not top.empty:
        r = top.iloc[0]
        print()
        print(f"  Top decile reached +25% in {r['hit_rate_25pct'] * 100:.0f}% of "
              f"{int(r['n'])} observations, with a 10th-percentile outcome of "
              f"{r['p10'] * 100:.1f}%.")
        print("  That pair is the honest answer to the question this tracker was")
        print("  built around — a probability and a downside, not a target.")


def _pct(v):
    return "—" if v is None else f"{v * 100:+.1f}%"


def _report(stats: dict, dates, histories, args) -> None:
    print(f"\n{'=' * 62}")
    print(f"  BACKTEST — {args.setup} setup, top {args.size}, held {args.hold} sessions")
    print(f"{'=' * 62}")
    print(f"  universe        {len(histories)} symbols with enough history")
    print(f"  rebalances      {len(dates)}  ({dates[0]} to {dates[-1]})")

    if stats.get("trades", 0) == 0:
        print(f"  {stats.get('note', 'no trades')}")
        return

    print(f"  trades          {stats['trades']}")
    print()
    print(f"  median return   {_pct(stats['median_return'])}   per {args.hold}-session hold")
    print(f"  hit rate        {stats['hit_rate'] * 100:.0f}% positive")
    print(f"  hit rate >=25%  {stats['hit_rate_25pct'] * 100:.0f}%   <- the number this tracker exists to estimate")
    print(f"  median winner   {_pct(stats['median_winner'])}")
    print(f"  median loser    {_pct(stats['median_loser'])}")
    print(f"  10th percentile {_pct(stats['p10'])}   <- the downside tail")
    print(f"  worst / best    {_pct(stats['worst'])} / {_pct(stats['best'])}")
    print(f"  mean per hold   {_pct(stats['mean_per_hold'])}")
    print(f"  annualised      {_pct(stats['annualised'])}   (holds overlap; this is an")
    print(f"                  expected return per hold raised to a year, not an equity curve)")
    periods = stats["drawdown_periods"]
    if periods >= 6:
        print(f"  max drawdown    {stats['max_drawdown'] * 100:.1f}%  over {periods} non-overlapping holds")
    else:
        # Printing "0.0%" off three observations would read as "never fell",
        # which is the opposite of what three observations can support.
        print(f"  max drawdown    not measurable — only {periods} non-overlapping "
              f"holds fit in the window")
    print(f"  calendar span   {stats['calendar_span_years']} years")

    bench = stats.get("benchmark") or {}
    if bench:
        print()
        print(f"  Nifty 500 over the same holds:")
        print(f"    median        {_pct(bench.get('median_return'))}")
        print(f"    >=25%         {bench.get('hit_rate_25pct', 0) * 100:.0f}%")
        print(f"    annualised    {_pct(bench.get('annualised'))}")

    if stats.get("by_setup"):
        print("\n  by setup:")
        for setup, block in stats["by_setup"].items():
            print(f"    {setup:9} n={block['n']:4}  median {_pct(block['median'])}  "
                  f">=25% {block['hit_rate_25pct'] * 100:3.0f}%  p10 {_pct(block['p10'])}")

    print()
    print("  Read with these in mind:")
    print("    * Survivorship. The universe is today's Nifty 500, because")
    print("      point-in-time membership only began being recorded with this")
    print("      project. Every company demoted for doing badly is missing, so")
    print("      these returns are flattered. The bias shrinks as the weekly")
    print("      membership table fills.")
    print(f"    * Sample. {len(dates)} rebalances is indicative, not conclusive.")
    print("    * Filing dates are estimated from the SEBI deadline, which errs")
    print("      late — the direction that makes this pessimistic, not optimistic.")
    print(f"    * Costs of {engine.ROUND_TRIP_COST * 100:.1f}% round trip are charged on every trade.")


if __name__ == "__main__":
    sys.exit(main())
