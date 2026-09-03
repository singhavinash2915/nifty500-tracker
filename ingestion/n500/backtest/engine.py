"""Portfolio replay and the statistics that judge it.

Deliberately unglamorous arithmetic. A backtest earns its keep by being
believed, and the things that make it believable are the ones that make the
numbers worse: costs charged on both legs, entries at the *next* day's open
rather than the signal day's close, and a hard refusal to score a stock whose
history had not accumulated yet.

If a run here shows a 60% CAGR, the correct response is to look for the bug.
A quality-momentum screen on Indian mid-caps that beats the index by four to
eight points a year, with deeper drawdowns than the index, is a genuinely good
outcome and should feel underwhelming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

# Charged on entry and exit. Brokerage, STT, exchange fees, GST, stamp duty and
# a slippage allowance for a mid-cap — deliberately not optimistic.
ROUND_TRIP_COST = 0.004

TRADING_DAYS_YEAR = 252


@dataclass
class Trade:
    symbol: str
    setup: str
    entry_date: date
    entry_price: float
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = "held"
    score: float | None = None
    decile: int | None = None

    @property
    def gross_return(self) -> float | None:
        if self.exit_price is None or not self.entry_price:
            return None
        return self.exit_price / self.entry_price - 1.0

    @property
    def net_return(self) -> float | None:
        gross = self.gross_return
        return None if gross is None else (1.0 + gross) * (1.0 - ROUND_TRIP_COST) - 1.0


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    benchmark: list[float] = field(default_factory=list)
    rebalances: list[date] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": t.symbol,
                    "setup": t.setup,
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "score": t.score,
                    "decile": t.decile,
                    "gross_return": t.gross_return,
                    "net_return": t.net_return,
                }
                for t in self.trades
            ]
        )


def month_end_dates(index: pd.DatetimeIndex, *, warmup: int, forward: int) -> list[date]:
    """Last trading day of each month that has both history and a future.

    A rebalance needs `warmup` bars behind it to score at all and `forward`
    bars ahead of it for the holding period to complete — otherwise the trade
    is still open and would silently be scored as flat.
    """
    if len(index) <= warmup + forward:
        return []
    usable = index[warmup : len(index) - forward]
    marks = pd.Series(usable, index=usable).groupby(usable.to_period("M")).max()
    return [d.date() for d in marks]


def simulate(
    *,
    dates: list[date],
    rank_at: "callable",
    price_lookup: "callable",
    portfolio_size: int = 20,
    holding_days: int = 126,
    sector_cap: float = 0.25,
) -> Result:
    """Run the rebalance loop.

    `rank_at(as_of)` returns a frame indexed by symbol with `score`, `setup`,
    `sector` and `decile`. `price_lookup(symbol, on, offset)` returns the price
    `offset` trading days after `on`, or None.
    """
    result = Result(rebalances=list(dates))

    for as_of in dates:
        ranked = rank_at(as_of)
        if ranked is None or ranked.empty:
            continue

        picks = _select(ranked, portfolio_size, sector_cap)
        for symbol, row in picks.iterrows():
            # Entry on the NEXT session's open: a score computed from a close
            # cannot be acted on at that same close.
            entry = price_lookup(symbol, as_of, 1, field="open")
            if entry is None or entry <= 0:
                continue
            exit_price = price_lookup(symbol, as_of, holding_days, field="close")
            exit_date = price_lookup(symbol, as_of, holding_days, field="date")

            result.trades.append(
                Trade(
                    symbol=symbol,
                    setup=str(row.get("setup", "none")),
                    entry_date=as_of,
                    entry_price=float(entry),
                    exit_price=None if exit_price is None else float(exit_price),
                    exit_date=exit_date,
                    exit_reason="target_period",
                    score=None if pd.isna(row.get("score")) else float(row["score"]),
                    decile=None if pd.isna(row.get("decile")) else int(row["decile"]),
                )
            )

    return result


def _select(ranked: pd.DataFrame, size: int, sector_cap: float) -> pd.DataFrame:
    """Top scores, with a cap on any one sector.

    Momentum screens love to hand you eight public-sector banks at once. The
    cap is what stops a single sector's drawdown becoming the whole portfolio's.
    """
    ordered = ranked[ranked["score"].notna()].sort_values("score", ascending=False)
    max_per_sector = max(1, int(size * sector_cap))

    chosen: list[str] = []
    counts: dict[str, int] = {}
    for symbol, row in ordered.iterrows():
        sector = row.get("sector") or "Unknown"
        if counts.get(sector, 0) >= max_per_sector:
            continue
        chosen.append(symbol)
        counts[sector] = counts.get(sector, 0) + 1
        if len(chosen) >= size:
            break

    return ordered.loc[chosen]


# --- statistics -----------------------------------------------------------


def summarise(result: Result, *, holding_days: int, benchmark: pd.Series | None) -> dict:
    frame = result.frame()
    closed = frame[frame["net_return"].notna()] if not frame.empty else frame

    if closed.empty:
        return {"trades": 0, "note": "no completed trades in the window"}

    returns = closed["net_return"]
    periods_per_year = TRADING_DAYS_YEAR / holding_days

    # Equal-weighted portfolio return per rebalance.
    per_rebalance = closed.groupby("entry_date")["net_return"].mean()

    # Monthly rebalances with a six-month hold OVERLAP: fourteen of them span
    # about eighteen months of calendar, not seven years. Compounding them as
    # if they were sequential turned a 2.36x product into a fictitious
    # seven-year CAGR. The honest annualisation of an overlapping sample is to
    # take the mean return per hold and raise it to the number of holds in a
    # year — an expected return, not a realised equity curve.
    mean_per_hold = float(per_rebalance.mean())
    annualised = (
        (1 + mean_per_hold) ** periods_per_year - 1 if mean_per_hold > -1 else None
    )

    # A drawdown needs a real equity curve, so it is measured only on the
    # non-overlapping subset: every `holding_days` worth of rebalances.
    stride = max(1, int(round(periods_per_year and holding_days / 21)))
    sequential = per_rebalance.iloc[::stride]
    equity = np.cumprod([1 + r for r in sequential])
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.max((peak - equity) / peak)) if len(equity) else 0.0

    stats = {
        "trades": int(len(closed)),
        "rebalances": int(per_rebalance.shape[0]),
        "median_return": float(returns.median()),
        "mean_return": float(returns.mean()),
        "hit_rate": float((returns > 0).mean()),
        "hit_rate_25pct": float((returns >= 0.25).mean()),
        "median_winner": float(returns[returns > 0].median()) if (returns > 0).any() else None,
        "median_loser": float(returns[returns <= 0].median()) if (returns <= 0).any() else None,
        "worst": float(returns.min()),
        "best": float(returns.max()),
        "p10": float(returns.quantile(0.10)),
        "mean_per_hold": mean_per_hold,
        "annualised": annualised,
        "max_drawdown": max_dd,
        "drawdown_periods": int(len(sequential)),
        "calendar_span_years": _span_years(per_rebalance.index.tolist(), holding_days),
    }

    if benchmark is not None and len(benchmark):
        stats["benchmark"] = _benchmark_stats(
            benchmark, per_rebalance.index.tolist(), holding_days, periods_per_year
        )

    stats["by_setup"] = {
        setup: _block(group["net_return"])
        for setup, group in closed.groupby("setup")
        if len(group) >= 5
    }
    stats["by_decile"] = {
        int(decile): _block(group["net_return"])
        for decile, group in closed.dropna(subset=["decile"]).groupby("decile")
        if len(group) >= 5
    }
    return stats


def _block(returns: pd.Series) -> dict:
    return {
        "n": int(len(returns)),
        "median": float(returns.median()),
        "hit_rate": float((returns > 0).mean()),
        "hit_rate_25pct": float((returns >= 0.25).mean()),
        "p10": float(returns.quantile(0.10)),
    }


def decile_study(
    observations: pd.DataFrame, *, threshold: float = 0.25
) -> pd.DataFrame:
    """Forward returns of *every* scored stock, bucketed by decile.

    Distinct from the traded portfolio and more useful than it. A twenty-name
    portfolio drawn from the top of the list produces a handful of trades per
    decile and no usable distribution; scoring the whole cross-section at every
    rebalance produces thousands of observations, which is what a statement
    like "stocks scoring here reached +25% about a fifth of the time" needs
    behind it.

    The output is a distribution, never a point estimate — the tenth percentile
    sits beside the hit rate on purpose, because a setup that reaches the
    target a fifth of the time and loses 30% the rest is not the same
    proposition as one that reaches it a fifth of the time and loses 5%.
    """
    columns = [
        "decile", "n", "median", "mean", "hit_rate",
        f"hit_rate_{int(threshold * 100)}pct", "p10", "p90",
    ]
    if observations.empty or "decile" not in observations:
        return pd.DataFrame(columns=columns)

    rows = []
    for decile, group in observations.dropna(subset=["decile"]).groupby("decile"):
        returns = group["forward_return"].dropna()
        if len(returns) < 20:
            continue
        rows.append(
            {
                "decile": int(decile),
                "n": int(len(returns)),
                "median": float(returns.median()),
                "mean": float(returns.mean()),
                "hit_rate": float((returns > 0).mean()),
                f"hit_rate_{int(threshold * 100)}pct": float((returns >= threshold).mean()),
                "p10": float(returns.quantile(0.10)),
                "p90": float(returns.quantile(0.90)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).sort_values("decile", ascending=False)


def _benchmark_stats(
    benchmark: pd.Series, entries: list[date], holding_days: int, periods_per_year: float
) -> dict:
    """The same holding periods, bought on the index instead."""
    values: list[float] = []
    for entry in entries:
        start = benchmark.index.searchsorted(pd.Timestamp(entry), side="right")
        end = start + holding_days
        if start >= len(benchmark) or end >= len(benchmark):
            continue
        values.append(float(benchmark.iloc[end]) / float(benchmark.iloc[start]) - 1.0)

    if not values:
        return {}
    mean_per_hold = float(np.mean(values))
    return {
        "periods": len(values),
        "median_return": float(np.median(values)),
        "mean_per_hold": mean_per_hold,
        "hit_rate_25pct": float(np.mean([v >= 0.25 for v in values])),
        # Annualised the same way as the portfolio, so the two are comparable.
        "annualised": (1 + mean_per_hold) ** periods_per_year - 1 if mean_per_hold > -1 else None,
    }


def _span_years(entries: list, holding_days: int) -> float:
    """Calendar years actually covered, entry to final exit."""
    if not entries:
        return 0.0
    start, end = min(entries), max(entries)
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days + holding_days * 1.4
    return round(days / 365.25, 2)


def rank_quality(study: pd.DataFrame) -> dict:
    """Does a higher score actually mean a better forward return?

    The question a backtest exists to answer, and the one easiest to skip past
    when the headline number looks good. A rank correlation near zero means the
    score is not ranking anything, however well the top-twenty portfolio
    happened to do.
    """
    if study.empty or len(study) < 4:
        return {"verdict": "not enough deciles to judge"}

    deciles = study["decile"].astype(float)
    medians = study["median"].astype(float)
    hits = study[[c for c in study.columns if c.startswith("hit_rate_")][-1]].astype(float)

    def spearman(a: pd.Series, b: pd.Series) -> float:
        return float(a.rank().corr(b.rank()))

    median_rho = spearman(deciles, medians)
    hit_rho = spearman(deciles, hits)

    if median_rho >= 0.6:
        verdict = "the score ranks: higher deciles did better"
    elif median_rho >= 0.3:
        verdict = "weak ordering — suggestive, not established"
    elif median_rho > -0.3:
        verdict = "NO ordering — the score did not separate winners from losers"
    else:
        verdict = "INVERTED — higher scores did worse"

    return {
        "median_rho": round(median_rho, 3),
        "hit_rate_rho": round(hit_rho, 3),
        "top_minus_bottom_median": round(
            float(medians.iloc[0] - medians.iloc[-1]), 4
        ),
        "verdict": verdict,
    }
