"""Job: write the screener snapshot the web app reads.

    python -m n500.jobs.export_snapshot --dry-run

Exists so the front end has one well-defined contract instead of ad-hoc scripts,
and so the same shape is produced whether the data came from Supabase or from a
dry run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ..config import REPO_ROOT
from ..db import Db
from ..serialise import dumps

JOB = "export_snapshot"
DEFAULT_OUT = REPO_ROOT / "web" / "public" / "scores-sample.json"
DETAIL_DIR = REPO_ROOT / "web" / "public" / "stocks"

# Two years of daily bars is what the zone engine reasons over, so it is what
# the chart shows. Written as parallel arrays rather than an array of objects:
# same information, roughly a third of the bytes, and each file is fetched on
# its own when a stock is opened.
DETAIL_BARS = 520


def _clean(value):
    """pandas NaN -> None.

    `json.dumps` writes a bare `NaN` for a float nan, which is not valid JSON:
    the browser's JSON.parse rejects the whole file and the page renders empty
    with only a console error to show for it.
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _num(value):
    if value is None:
        return None
    value = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(value) else round(float(value), 4)


def _latest(db: Db, table: str, key: str = "date") -> pd.DataFrame:
    frame = pd.DataFrame(db.select(table))
    if frame.empty:
        return frame
    return frame.sort_values(key).groupby("symbol").tail(1).set_index("symbol")


def build(db: Db) -> dict:
    scores = _latest(db, "scores_daily")
    if scores.empty:
        raise SystemExit(f"[{JOB}] no scores — run compute_scores first")

    technicals = _latest(db, "technicals_daily")
    setups = _latest(db, "ts_setups")
    stocks = pd.DataFrame(db.select("stocks")).set_index("symbol")
    ratios = pd.DataFrame(db.select("company_ratios"))
    ratios = ratios.set_index("symbol") if not ratios.empty else pd.DataFrame()

    prices = pd.DataFrame(db.select("prices_daily"))
    last_close = {}
    if not prices.empty:
        prices["date"] = pd.to_datetime(prices["date"])
        last = prices.sort_values("date").groupby("symbol").tail(1)
        last_close = dict(zip(last["symbol"], pd.to_numeric(last["adj_close"], errors="coerce")))

    rows = []
    for symbol, score in scores.iterrows():
        tech = technicals.loc[symbol] if symbol in technicals.index else None
        setup = setups.loc[symbol] if len(setups) and symbol in setups.index else None
        ratio = ratios.loc[symbol] if len(ratios) and symbol in ratios.index else None
        stock = stocks.loc[symbol] if symbol in stocks.index else None

        confirmation = (setup.get("confirmation") if setup is not None else None) or {}
        rows.append(
            {
                "symbol": symbol,
                "company_name": stock["company_name"] if stock is not None else symbol,
                "sector": _clean(stock["sector"]) if stock is not None else None,
                "company_type": _clean(stock.get("company_type")) if stock is not None else None,
                "close": _num(last_close.get(symbol)),
                "quality_score": _num(score.get("quality_score")),
                "value_score": _num(score.get("value_score")),
                "revision_score": _num(score.get("revision_score")),
                "ownership_score": _num(score.get("ownership_score")),
                "tm_score": _num(score.get("tm_score")),
                "ts_score": _num(score.get("ts_score")),
                "blended": _num(score.get("blended")),
                "winning_setup": _clean(score.get("winning_setup")),
                "setup_status": _clean(score.get("setup_status")),
                "decile": None if pd.isna(score.get("decile")) else int(score["decile"]),
                "flags": score.get("flags") or [],
                "pe": _num(ratio.get("pe")) if ratio is not None else None,
                "roe": _num(ratio.get("roe")) if ratio is not None else None,
                "mom_12_1": _num(tech.get("mom_12_1")) if tech is not None else None,
                "rs_vs_index": _num(tech.get("rs_vs_index")) if tech is not None else None,
                "dist_52w_high": _num(tech.get("dist_52w_high")) if tech is not None else None,
                "rsi14": _num(tech.get("rsi14")) if tech is not None else None,
                "turnover_60d_cr": _num(tech.get("turnover_60d_cr")) if tech is not None else None,
                "above_200dma": (
                    None
                    if tech is None or pd.isna(pd.to_numeric(tech.get("sma200"), errors="coerce"))
                    or last_close.get(symbol) is None
                    else bool(last_close[symbol] > float(tech["sma200"]))
                ),
                "stop_price": _num(setup.get("stop_price")) if setup is not None else None,
                "target_price": _num(setup.get("target_price")) if setup is not None else None,
                "reward_risk": _num(setup.get("reward_risk")) if setup is not None else None,
                "headroom": _num(setup.get("headroom")) if setup is not None else None,
                "zone_floor": _num(setup.get("zone_floor")) if setup is not None else None,
                "zone_ceil": _num(setup.get("zone_ceil")) if setup is not None else None,
                "confirmations": [k for k, v in confirmation.items() if v is True],
                "caps": (setup.get("caps") if setup is not None else None) or [],
                "reason": _clean(setup.get("reason")) if setup is not None else None,
                "resistance_floor": _num(setup.get("resistance_floor")) if setup is not None else None,
                "resistance_ceil": _num(setup.get("resistance_ceil")) if setup is not None else None,
                "resistance_strength": _num(setup.get("resistance_strength")) if setup is not None else None,
                "false_breakout": (setup.get("false_breakout") if setup is not None else None) or None,
                "rejected_at_resistance": bool(
                    setup.get("rejected_at_resistance") if setup is not None else False
                ),
            }
        )

    rows.sort(key=lambda r: (r["blended"] is None, -(r["blended"] or 0)))
    return {"as_of": str(scores["date"].iloc[0]), "rows": rows}


def serialise(snapshot: dict) -> str:
    """Strict JSON, so an unclean value fails here rather than in the browser."""
    return dumps(snapshot)


def build_details(db: Db, symbols: set[str]) -> dict[str, dict]:
    """Per-symbol payloads for the stock detail page."""
    prices = pd.DataFrame(db.select("prices_daily"))
    if prices.empty:
        return {}
    prices["date"] = pd.to_datetime(prices["date"])
    for column in ("open", "high", "low", "close", "adj_close", "volume"):
        prices[column] = pd.to_numeric(prices[column], errors="coerce")

    technicals = pd.DataFrame(db.select("technicals_daily"))
    if not technicals.empty:
        technicals["date"] = pd.to_datetime(technicals["date"])

    zones = pd.DataFrame(db.select("support_zones"))
    annual = pd.DataFrame(db.select("fundamentals_y"))
    quarterly = pd.DataFrame(db.select("fundamentals_q"))
    holding = pd.DataFrame(db.select("shareholding"))

    by_symbol = dict(tuple(prices.groupby("symbol")))
    zones_by = dict(tuple(zones.groupby("symbol"))) if not zones.empty else {}
    annual_by = dict(tuple(annual.groupby("symbol"))) if not annual.empty else {}
    quarterly_by = dict(tuple(quarterly.groupby("symbol"))) if not quarterly.empty else {}
    holding_by = dict(tuple(holding.groupby("symbol"))) if not holding.empty else {}

    out: dict[str, dict] = {}
    for symbol in sorted(symbols):
        frame = by_symbol.get(symbol)
        if frame is None or frame.empty:
            continue
        frame = frame.sort_values("date").tail(DETAIL_BARS)

        # Split-adjusted, so the chart matches what the indicators were computed on.
        factor = (frame["adj_close"] / frame["close"]).fillna(1.0)
        bars = {
            "t": [d.strftime("%Y-%m-%d") for d in frame["date"]],
            "o": [_num(v) for v in frame["open"] * factor],
            "h": [_num(v) for v in frame["high"] * factor],
            "l": [_num(v) for v in frame["low"] * factor],
            "c": [_num(v) for v in frame["adj_close"]],
            "v": [_num(v) for v in frame["volume"]],
        }

        # Moving averages are computed here from the full adjusted series, not
        # read from technicals_daily. Two reasons: the stored table is often
        # written with a short tail for speed, which leaves the chart with an
        # empty overlay; and computing over the whole history means the 200DMA
        # has a value on the first bar of the visible window instead of 200
        # bars of nothing.
        full = by_symbol[symbol].sort_values("date")
        full_adjusted = full["adj_close"].astype("float64")
        window = full_adjusted.tail(DETAIL_BARS)
        overlays = {
            "sma50": [_num(v) for v in full_adjusted.rolling(50, min_periods=50).mean().loc[window.index]],
            "sma200": [_num(v) for v in full_adjusted.rolling(200, min_periods=200).mean().loc[window.index]],
        }

        live_zones = []
        zframe = zones_by.get(symbol)
        if zframe is not None:
            for _, z in zframe.iterrows():
                live_zones.append(
                    {
                        "timeframe": _clean(z.get("timeframe")),
                        "source": _clean(z.get("source")),
                        "floor": _num(z.get("floor_price")),
                        "ceil": _num(z.get("ceil_price")),
                        "touches": int(z.get("touch_count") or 0),
                        "strength": _num(z.get("strength")),
                        "formed_on": _clean(z.get("formed_on")),
                        "invalidated_on": _clean(z.get("invalidated_on")),
                    }
                )

        out[symbol] = {
            "symbol": symbol,
            "bars": bars,
            "overlays": overlays,
            "zones": live_zones,
            "annual": _records(annual_by.get(symbol), "period_end",
                               ["revenue", "ebitda", "pat", "eps", "cfo", "roce", "roe",
                                "debt_equity", "debtor_days"]),
            "quarterly": _records(quarterly_by.get(symbol), "period_end",
                                  ["revenue", "pat", "opm", "eps"]),
            "shareholding": _records(holding_by.get(symbol), "quarter_end",
                                     ["promoter_pct", "fii_pct", "dii_pct", "public_pct"]),
        }
    return out


def _records(frame, date_column: str, columns: list[str]) -> list[dict]:
    if frame is None or frame.empty or date_column not in frame:
        return []
    frame = frame.sort_values(date_column)
    rows = []
    for _, row in frame.iterrows():
        record = {"period": _clean(row[date_column])}
        for column in columns:
            record[column] = _num(row.get(column)) if column in frame else None
        rows.append(record)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the screener snapshot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--detail-dir", type=Path, default=DETAIL_DIR)
    parser.add_argument("--no-details", action="store_true")
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)
    snapshot = build(db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialise(snapshot))

    rows = snapshot["rows"]
    detail_count = 0
    if not args.no_details:
        details = build_details(db, {r["symbol"] for r in rows})
        args.detail_dir.mkdir(parents=True, exist_ok=True)
        for symbol, payload in details.items():
            (args.detail_dir / f"{symbol}.json").write_text(serialise(payload))
        detail_count = len(details)

    print(
        f"[{JOB}] {len(rows)} rows -> {args.out} "
        f"(as of {snapshot['as_of']}; "
        f"{sum(1 for r in rows if r['quality_score'] is not None)} with Q, "
        f"{sum(1 for r in rows if r['flags'])} carrying flags)"
    )
    if detail_count:
        print(f"[{JOB}] {detail_count} detail files -> {args.detail_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
