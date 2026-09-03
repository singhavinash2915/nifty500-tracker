"""Job: build support zones and score the T-S setup.

    python -m n500.jobs.compute_zones --dry-run

Zones are built on both timeframes. Weekly carries the structure; daily
supplies precision and the confirmation trigger. A daily zone sitting inside a
weekly one is the multi-timeframe confluence that scores highest.

Weekly bars are resampled from daily rather than fetched separately, so the two
timeframes can never disagree about what a week contained.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..db import Db, run
from ..scoring import support
from ..zones import reversal
from ..zones.build import build_zones
from ..zones.pivots import find_pivots
from .compute_technicals import MIN_BARS, adjusted_frame

JOB = "compute_zones"

# Fibonacci retracements of the last major leg count as confluence, never as a
# zone in their own right.
FIB_LEVELS = (0.382, 0.5, 0.618)
FIB_TOLERANCE_ATR = 0.6
LONG_MA_TOLERANCE_ATR = 0.75


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample to weeks ending Friday, matching how a weekly chart is drawn.

    Each bar is stamped with the last session that actually traded in it, not
    the nominal Friday. Pandas labels a week by its Friday even when the week
    is still running, which dated the current partial bar 2026-09-04 while the
    latest real session was 2026-09-02 — a zone would then carry an
    invalidation date two days in the future, and any point-in-time query would
    quietly include a bar that had not finished yet.
    """
    grouped = daily.resample("W-FRI")
    weekly = grouped.agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])

    last_session = daily.index.to_series().resample("W-FRI").max()
    weekly.index = pd.DatetimeIndex(
        [last_session.get(stamp, stamp) for stamp in weekly.index], name=daily.index.name
    )
    return weekly


def _extras(frame: pd.DataFrame, index: int, price: float, atr_value: float) -> dict:
    """Moving-average and Fibonacci agreement near the current price."""
    out: dict = {}

    for window in (200, 250):
        ma = ind.sma(frame["close"], window)
        value = ma.iloc[index] if index < len(ma) else np.nan
        if not pd.isna(value) and abs(price - float(value)) <= LONG_MA_TOLERANCE_ATR * atr_value:
            out["near_long_ma"] = True
            break

    lookback = frame.iloc[max(0, index - 251) : index + 1]
    if len(lookback) > 30:
        high, low = float(lookback["high"].max()), float(lookback["low"].min())
        if high > low:
            for level in FIB_LEVELS:
                retracement = high - (high - low) * level
                if abs(price - retracement) <= FIB_TOLERANCE_ATR * atr_value:
                    out["near_fib"] = level
                    break

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build zones and score T-S")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-quality-gate",
        action="store_true",
        help="score T-S without requiring Q >= 60 (for debugging the engine)",
    )
    parser.add_argument("--symbols", help="comma-separated subset, for debugging")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    prices = pd.DataFrame(db.select("prices_daily"))
    if prices.empty:
        print(f"[{JOB}] no prices — run load_prices first", file=sys.stderr)
        return 1

    # The fundamentals gate: a weak business at support is a cheaper weak
    # business, and it will keep getting cheaper.
    quality_gate = not args.no_quality_gate
    quality_by: dict[str, float] = {}
    excluded: set[str] = set()
    fundamentals = pd.DataFrame(db.select("fundamental_scores"))
    if not fundamentals.empty:
        latest = fundamentals.sort_values("date").groupby("symbol").tail(1)
        for _, row in latest.iterrows():
            if row.get("excluded"):
                excluded.add(row["symbol"])
                continue
            score_value = pd.to_numeric(row.get("quality_score"), errors="coerce")
            if not pd.isna(score_value):
                quality_by[row["symbol"]] = float(score_value)
    elif quality_gate:
        print(
            f"[{JOB}] no quality scores yet — every setup will report "
            "'quality score not yet available'. Run compute_fundamental_scores "
            "first, or pass --no-quality-gate.",
            file=sys.stderr,
        )

    wanted = (
        {s.strip().upper() for s in args.symbols.split(",")} if args.symbols else None
    )

    zone_rows: list[dict] = []
    setup_rows: list[dict] = []

    with run(JOB, db=db) as log:
        for symbol, group in prices.groupby("symbol", sort=True):
            if wanted and symbol not in wanted:
                continue
            if len(group) < MIN_BARS:
                continue

            daily = adjusted_frame(group)
            weekly = to_weekly(daily)
            if len(weekly) < 40:
                log.error(symbol, f"only {len(weekly)} weekly bars")
                continue

            try:
                setup, zones = _evaluate_symbol(
                    daily,
                    weekly,
                    quality_gate=quality_gate,
                    quality_score=quality_by.get(symbol),
                    hard_excluded=symbol in excluded,
                )
            except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the sweep
                log.error(symbol, repr(exc))
                continue

            for timeframe, built in zones.items():
                for zone in built:
                    zone_rows.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "source": zone.source.value,
                            "floor_price": round(zone.floor, 4),
                            "ceil_price": round(zone.ceil, 4),
                            "formed_on": zone.formed_date.date().isoformat(),
                            "last_touch_on": (
                                zone.touches[-1].date.date().isoformat()
                                if zone.touches
                                else None
                            ),
                            "touch_count": len(zone.touches),
                            "avg_reaction_atr": _mean_reaction(zone),
                            "rejection_quality": (
                                round(len(zone.rejections) / len(zone.touches), 3)
                                if zone.touches
                                else None
                            ),
                            "confluence": zone.confluence,
                            "strength": zone.strength,
                            "invalidated_on": (
                                zone.invalidated_date.date().isoformat()
                                if zone.invalidated_date is not None
                                else None
                            ),
                        }
                    )

            setup_rows.append(
                {
                    "symbol": symbol,
                    "date": daily.index[-1].date().isoformat(),
                    "ts_score": setup.score,
                    "setup_status": setup.status,
                    "stop_price": setup.stop,
                    "target_price": setup.target,
                    "reward_risk": setup.reward_risk,
                    "headroom": setup.headroom,
                    "components": setup.components,
                    "confirmation": (
                        setup.confirmation.as_dict() if setup.confirmation else None
                    ),
                    "caps": setup.caps,
                    "reason": setup.reason,
                    "zone_floor": setup.zone.floor if setup.zone else None,
                    "zone_ceil": setup.zone.ceil if setup.zone else None,
                    "zone_timeframe": setup.zone.timeframe if setup.zone else None,
                }
            )
            log.symbols_ok += 1

        log.rows_written = db.upsert("support_zones", zone_rows)
        db.upsert("ts_setups", setup_rows, on_conflict="symbol,date")

        scored = sum(1 for r in setup_rows if r["ts_score"] is not None)
        triggered = sum(1 for r in setup_rows if r["setup_status"] == "triggered")
        watching = sum(1 for r in setup_rows if r["setup_status"] == "watching")
        log.notes = (
            f"{len(zone_rows)} zones, {scored} setups scored "
            f"({triggered} triggered, {watching} watching)"
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {summary} ({mode})")
    return 0


def _mean_reaction(zone) -> float | None:
    values = [e.reaction_atr for e in zone.touches if e.reaction_atr is not None]
    return round(float(np.mean(values)), 3) if values else None


def _evaluate_symbol(
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    *,
    quality_gate: bool,
    quality_score: float | None = None,
    hard_excluded: bool = False,
):
    """Build both timeframes' zones and score the daily setup."""
    daily_atr = ind.atr(daily["high"], daily["low"], daily["close"], 14)
    weekly_atr = ind.atr(weekly["high"], weekly["low"], weekly["close"], 14)

    daily_zones = build_zones(daily, daily_atr, timeframe="daily")
    weekly_zones = build_zones(weekly, weekly_atr, timeframe="weekly")

    index = len(daily) - 1
    price = float(daily["close"].iloc[index])
    atr_value = float(daily_atr.iloc[index]) if not pd.isna(daily_atr.iloc[index]) else 0.0

    rsi = ind.rsi(daily["close"], 14)
    macd_hist = ind.macd_histogram(daily["close"])
    sma20 = ind.sma(daily["close"], 20)

    candidates = [z for z in daily_zones if z.is_live(index) and z.floor <= price]
    nearest = min(candidates, key=lambda z: price - z.mid, default=None)

    confirmation = reversal.confirm(
        daily,
        index=index,
        floor=nearest.floor if nearest else price,
        ceil=nearest.ceil if nearest else price,
        rsi=rsi,
        macd_hist=macd_hist,
        sma20=sma20,
        timeframe="daily",
    )

    setup = support.evaluate(
        frame=daily,
        index=index,
        price=price,
        atr=daily_atr,
        zones=daily_zones,
        weekly_zones=weekly_zones,
        pivots=find_pivots(daily),
        confirmation=confirmation,
        extras=_extras(daily, index, price, atr_value or 1.0),
        quality_score=quality_score,
        quality_gate=quality_gate,
        hard_excluded=hard_excluded,
    )
    return setup, {"daily": daily_zones, "weekly": weekly_zones}


if __name__ == "__main__":
    sys.exit(main())
