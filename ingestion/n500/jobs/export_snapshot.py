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

JOB = "export_snapshot"
DEFAULT_OUT = REPO_ROOT / "web" / "public" / "scores-sample.json"


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
            }
        )

    rows.sort(key=lambda r: (r["blended"] is None, -(r["blended"] or 0)))
    return {"as_of": str(scores["date"].iloc[0]), "rows": rows}


def serialise(snapshot: dict) -> str:
    """Strict JSON, so an unclean value fails here rather than in the browser."""
    return json.dumps(snapshot, allow_nan=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the screener snapshot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    snapshot = build(Db(force_dry_run=args.dry_run))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialise(snapshot))

    rows = snapshot["rows"]
    print(
        f"[{JOB}] {len(rows)} rows -> {args.out} "
        f"(as of {snapshot['as_of']}; "
        f"{sum(1 for r in rows if r['quality_score'] is not None)} with Q, "
        f"{sum(1 for r in rows if r['flags'])} carrying flags)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
