"""Three stocks a month, chosen by a rule rather than on the day.

The rule was tested over 38 month-ends (January 2023 to March 2026), holding each
pick six months: the top 3 by conviction returned +22.3% against +11.0% for the
average stock, with an edge of +11.5% in 2023-24 and +11.0% in 2025-26. Limits:
no costs or stops in that test, and about six independent periods. 39% of picks
reached +25%, so most did not. One per sector left returns unchanged; it is here
to stop a month becoming three versions of one bet.

Locked on the month's first scored session, because a choice remade nightly is
the churn this exists to avoid.
"""

from __future__ import annotations

from typing import Iterable

MONTHLY_PICKS = 3
HOLD_MONTHS = 6
# Every position the plan can hold at once, if none is stopped out early.
MAX_OPEN = MONTHLY_PICKS * HOLD_MONTHS
PORTFOLIO_RISK_BUDGET = 0.06


def budgets(capital: float | None) -> tuple[float | None, float | None]:
    """Most to put in one position, and most to risk on it.

    The usual 10% and 1% would spend the cash in under three months and the risk
    budget in about two, so at three a month both are spread over MAX_OPEN.
    """
    if not capital or capital <= 0:
        return None, None
    return round(capital / MAX_OPEN, 2), round(capital * PORTFOLIO_RISK_BUDGET / MAX_OPEN, 2)


def choose(
    scores: Iterable[dict],
    *,
    instruments: dict[str, str],
    sectors: dict[str, str | None],
    plans: dict[str, dict],
    held: set[str],
    n: int = MONTHLY_PICKS,
) -> list[dict]:
    """The top `n` by conviction that can actually be bought, one per sector.

    Skips what is already held, anything failing a hard gate, anything that is
    not an ordinary share, and anything without a stop below its price, which
    cannot be sized. Ties go alphabetically so the answer is repeatable.
    """
    ranked = sorted(
        (r for r in scores if r.get("conviction") is not None),
        key=lambda r: (-float(r["conviction"]), r["symbol"]),
    )
    chosen: list[dict] = []
    used_sectors: set[str] = set()
    for row in ranked:
        symbol = row["symbol"]
        sector = sectors.get(symbol) or "Unknown"
        plan = plans.get(symbol) or {}
        stop, close = plan.get("plan_stop"), row.get("close")
        if (
            symbol in held
            or instruments.get(symbol, "equity") != "equity"
            or sector in used_sectors
            or any(f.get("verdict") == "fail" for f in (row.get("flags") or []))
            or stop is None or close is None or float(stop) >= float(close)
        ):
            continue
        used_sectors.add(sector)
        chosen.append({
            "rank": len(chosen) + 1,
            "symbol": symbol,
            "sector": sector,
            "conviction": float(row["conviction"]),
            "close": float(close),
            "stop": float(stop),
            "stop_basis": plan.get("plan_stop_basis"),
            "target": None if plan.get("plan_target") is None else float(plan["plan_target"]),
        })
        if len(chosen) == n:
            break
    return chosen
