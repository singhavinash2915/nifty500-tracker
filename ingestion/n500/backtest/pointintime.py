"""Rebuild every score as it would have looked on a past date.

The whole value of a backtest lives in this file, because the two ways to get
it wrong are both silent and both flatter the result.

**Look-ahead.** Q2 results for the quarter ending 30 September are filed in
early November. A backtest that reads them from 30 September is trading on
information nobody had, and it will show a strategy that loses money live.
Every fundamental read here goes through `filed_on <= as_of`, and every
valuation multiple is built from the price on the day against earnings that had
actually been published by then — never from today's headline P/E.

**Stale structure.** A support zone is not knowable until the pivot that forms
it has been confirmed, and a zone that broke last month was intact the month
before. Zones are therefore built once over the full history, carrying the bar
index at which each became knowable and the bar at which it broke, and filtered
to that window at each replay date. Rebuilding them per date would be both
slower and, if the window were forgotten, wrong.

What this cannot fix is **survivorship**: the universe is the Nifty 500 as it
stands today, because point-in-time membership only began being snapshotted
when this project did. Every company that was demoted for doing badly is
missing, which flatters returns. The bias is stated in the report rather than
buried, and it shrinks as the weekly membership table accumulates.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from .. import indicators as ind
from .. import technicals as tech
from ..scoring import momentum, ownership, quality, redflags, revision, support, value
from ..scoring.ranking import peer_groups
from ..zones import candles, reversal
from ..zones.build import (
    Zone,
    build_zones,
    live_zones_above,
    live_zones_below,
    rate_strength,
)
from ..zones.pivots import find_pivots

# Bars of history required before a symbol can be scored at all: a 200-day
# average plus the 12-month momentum lookback.
WARMUP_BARS = 252


@dataclass
class SymbolHistory:
    """Everything about one symbol, prepared once and sliced per date."""

    symbol: str
    sector: str | None
    is_financial: bool
    daily: pd.DataFrame
    weekly: pd.DataFrame
    atr: pd.Series
    weekly_atr: pd.Series
    rsi: pd.Series
    macd_hist: pd.Series
    sma20: pd.Series
    technicals: pd.DataFrame
    daily_zones: list[Zone]
    weekly_zones: list[Zone]
    pivots: list = field(default_factory=list)
    shares_cr: float | None = None

    def index_at(self, as_of: date) -> int | None:
        """Position of the last bar at or before `as_of`."""
        stamp = pd.Timestamp(as_of)
        positions = self.daily.index.searchsorted(stamp, side="right") - 1
        position = int(positions)
        return position if position >= WARMUP_BARS else None


def prepare(
    symbol: str,
    daily: pd.DataFrame,
    *,
    sector: str | None,
    is_financial: bool,
    weekly: pd.DataFrame,
    index_close: pd.Series | None,
    shares_cr: float | None,
) -> SymbolHistory:
    atr = ind.atr(daily["high"], daily["low"], daily["close"], 14)
    weekly_atr = ind.atr(weekly["high"], weekly["low"], weekly["close"], 14)

    return SymbolHistory(
        symbol=symbol,
        sector=sector,
        is_financial=is_financial,
        daily=daily,
        weekly=weekly,
        atr=atr,
        weekly_atr=weekly_atr,
        rsi=ind.rsi(daily["close"], 14),
        macd_hist=ind.macd_histogram(daily["close"]),
        sma20=ind.sma(daily["close"], 20),
        technicals=tech.compute(daily, index_close=index_close),
        # Built once. `formed_index` and `invalidated_index` carry the window in
        # which each zone was actually live.
        daily_zones=build_zones(daily, atr, timeframe="daily"),
        weekly_zones=build_zones(weekly, weekly_atr, timeframe="weekly"),
        pivots=find_pivots(daily),
        shares_cr=shares_cr,
    )


# --- fundamentals as of a date --------------------------------------------


def filed_by(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """The rule the whole exercise depends on.

    Comparison stays in datetime64 rather than going through `.dt.date`: when
    every filing date is missing, the latter produces an object column whose
    comparison against a date raises rather than returning all-False.
    """
    if frame.empty or "filed_on" not in frame:
        return frame
    filed = pd.to_datetime(frame["filed_on"], errors="coerce")
    return frame[filed.notna() & (filed <= pd.Timestamp(as_of))]


def trailing_eps(quarterly: pd.DataFrame, annual: pd.DataFrame) -> float | None:
    """Four filed quarters where possible, else the last filed year."""
    if not quarterly.empty and "eps" in quarterly:
        values = pd.to_numeric(quarterly["eps"], errors="coerce").dropna()
        if len(values) >= 4:
            return float(values.tail(4).sum())
    if not annual.empty and "eps" in annual:
        values = pd.to_numeric(annual["eps"], errors="coerce").dropna()
        if len(values):
            return float(values.iloc[-1])
    return None


def book_value_per_share(annual: pd.DataFrame, shares_cr: float | None) -> float | None:
    """Equity over share count.

    Share count comes from today's market cap divided by today's price, which
    is a present-day figure — but the price series it is paired with is
    split-adjusted onto that same basis, so the two are consistent. Issuance
    since then is not modelled; it moves book value per share slowly and in a
    direction that makes the value score conservative.
    """
    if annual.empty or not shares_cr or shares_cr <= 0:
        return None
    latest = annual.iloc[-1]
    capital = pd.to_numeric(latest.get("equity"), errors="coerce")
    if pd.isna(capital) or capital <= 0:
        return None
    return float(capital) / shares_cr


def valuation_at(
    price: float,
    quarterly: pd.DataFrame,
    annual: pd.DataFrame,
    shares_cr: float | None,
    price_history: pd.Series,
) -> dict:
    """Multiples built from the price on the day and earnings filed by then."""
    out: dict = {}

    eps = trailing_eps(quarterly, annual)
    if eps and eps > 0:
        out["pe"] = price / eps

    bvps = book_value_per_share(annual, shares_cr)
    if bvps and bvps > 0:
        out["pb"] = price / bvps

    if shares_cr and not annual.empty:
        latest = annual.iloc[-1]
        market_cap = price * shares_cr
        debt = pd.to_numeric(latest.get("debt"), errors="coerce")
        enterprise = market_cap + (0.0 if pd.isna(debt) else float(debt))
        ebitda = pd.to_numeric(latest.get("ebitda"), errors="coerce")
        revenue = pd.to_numeric(latest.get("revenue"), errors="coerce")
        if not pd.isna(ebitda) and ebitda > 0:
            out["ev_ebitda"] = enterprise / float(ebitda)
        if not pd.isna(revenue) and revenue > 0:
            out["ev_sales"] = enterprise / float(revenue)

    # The stock's own historical multiple, using only years already filed.
    multiples: list[float] = []
    for _, row in annual.tail(5).iterrows():
        row_eps = pd.to_numeric(row.get("eps"), errors="coerce")
        if pd.isna(row_eps) or row_eps <= 0:
            continue
        window = price_history.loc[: pd.Timestamp(row["period_end"])]
        if window.empty:
            continue
        multiples.append(float(window.iloc[-1]) / float(row_eps))
    if len(multiples) >= 3:
        out["pe_5y_median"] = float(np.median(multiples))

    return out


# --- the cross-section at a date ------------------------------------------


class Membership:
    """Who was in the index on a given date.

    Built from archived constituent lists. `members_at` resolves to the most
    recent snapshot *at or before* the date asked about, which is the whole
    point: a list captured later knows which companies went on to do well, and
    scoring a cross-section against it is how a backtest quietly picks its
    sample from the answer.

    With no snapshots at all this passes everything through. That keeps the
    engine usable before the membership job has run, at the cost of the bias —
    so `has_history` is exposed for callers that want to say which they got.
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._by_date: dict[date, set[str]] = {}
        for row in rows or []:
            when = row["week_start"]
            when = when if isinstance(when, date) else date.fromisoformat(str(when)[:10])
            self._by_date.setdefault(when, set()).add(row["symbol"])
        self._dates = sorted(self._by_date)

    @property
    def has_history(self) -> bool:
        return bool(self._dates)

    def snapshot_for(self, as_of: date) -> date | None:
        earlier = [d for d in self._dates if d <= as_of]
        return earlier[-1] if earlier else (self._dates[0] if self._dates else None)

    def members_at(self, as_of: date) -> set[str] | None:
        """The constituents to score, or None when membership is unknown."""
        stamp = self.snapshot_for(as_of)
        return self._by_date[stamp] if stamp else None


def score_cross_section(
    histories: dict[str, SymbolHistory],
    fundamentals: dict[str, dict[str, pd.DataFrame]],
    as_of: date,
    *,
    quality_gate: bool = True,
    membership: "Membership | None" = None,
) -> pd.DataFrame:
    """Q, V, T-M, T-S and the red flags for every symbol, as of `as_of`."""
    rows: list[dict] = []
    setups: dict[str, support.SupportSetup] = {}

    members = membership.members_at(as_of) if membership else None

    for symbol, history in histories.items():
        if members is not None and symbol not in members:
            continue
        index = history.index_at(as_of)
        if index is None:
            continue

        price = float(history.daily["close"].iloc[index])
        if not np.isfinite(price) or price <= 0:
            continue

        books = fundamentals.get(symbol, {})
        annual = filed_by(books.get("annual", pd.DataFrame()), as_of)
        quarterly = filed_by(books.get("quarterly", pd.DataFrame()), as_of)
        holding = books.get("holding", pd.DataFrame())
        if not holding.empty and "quarter_end" in holding:
            # Not `quarter_end <= as_of`. A quarter's shareholding is filed up
            # to 21 days after it ends, so reading it on the last day of the
            # quarter is a look-ahead of up to three weeks — small, but exactly
            # the kind that makes an ownership signal look predictive when it
            # is only early.
            quarter = pd.to_datetime(holding["quarter_end"], errors="coerce").dt.date
            visible = quarter.notna() & quarter.map(
                lambda q: ownership.disclosed_by(q) <= as_of if pd.notna(q) else False
            )
            holding = holding[visible]

        flags = redflags.evaluate(
            {
                "pledge_checked": False,
                "promoter_history": holding["promoter_pct"].tolist() if "promoter_pct" in holding else [],
                "has_promoter": bool(holding["has_promoter"].iloc[-1]) if len(holding) and "has_promoter" in holding else False,
                "cfo": annual["cfo"].tolist() if "cfo" in annual else [],
                "pat": annual["pat"].tolist() if "pat" in annual else [],
                "debtor_days": annual["debtor_days"].tolist() if "debtor_days" in annual else [],
                "is_financial": history.is_financial,
            }
        )

        valuation = valuation_at(
            price, quarterly, annual, history.shares_cr, history.daily["close"].iloc[: index + 1]
        )

        metrics = quality.build_metrics(
            {
                "revenue_history": annual["revenue"].tolist() if "revenue" in annual else [],
                "pat_history": annual["pat"].tolist() if "pat" in annual else [],
                "quarterly_pat": quarterly["pat"].tolist() if "pat" in quarterly else [],
                "cfo_history": annual["cfo"].tolist() if "cfo" in annual else [],
                "opm_history": annual["ebitda"].tolist() if "ebitda" in annual else [],
                "debt_history": annual["debt"].tolist() if "debt" in annual else [],
                "roe": _last(annual, "roe"),
                "roce": _last(annual, "roce"),
                "debt_equity": _last(annual, "debt_equity"),
                "interest_cover": _last(annual, "interest_cover"),
                "fcf": _last(annual, "fcf"),
                "pe": valuation.get("pe"),
                "pat_cagr_3y": quality.cagr(annual["pat"].tolist() if "pat" in annual else [], 3),
            }
        )

        revision_metrics = revision.build_metrics(
            {
                "quarterly_pat": quarterly["pat"].tolist() if "pat" in quarterly else [],
                "quarterly_revenue": quarterly["revenue"].tolist() if "revenue" in quarterly else [],
                "quarterly_opm": quarterly["opm"].tolist() if "opm" in quarterly else [],
            }
        )
        ownership_metrics = ownership.build_metrics(
            {
                "promoter_history": holding["promoter_pct"].tolist() if "promoter_pct" in holding else [],
                "has_promoter": bool(holding["has_promoter"].iloc[-1])
                if len(holding) and "has_promoter" in holding else False,
                "fii_history": holding["fii_pct"].tolist() if "fii_pct" in holding else [],
                "dii_history": holding["dii_pct"].tolist() if "dii_pct" in holding else [],
            }
        )

        technical_row = (
            history.technicals.iloc[index].to_dict() if index < len(history.technicals) else {}
        )

        rows.append(
            {
                "symbol": symbol,
                "sector": history.sector,
                "is_financial": history.is_financial,
                "close": price,
                "excluded": redflags.excluded(flags),
                "margin_expanding": value.margin_expanding(
                    quarterly["opm"].tolist() if "opm" in quarterly else []
                ),
                "dividend_yield": np.nan,
                **metrics,
                **revision_metrics,
                **ownership_metrics,
                **valuation,
                **{k: technical_row.get(k) for k in tech.TECHNICAL_COLUMNS},
                **_overhead_at(history, index, price),
            }
        )

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows).set_index("symbol")
    for column in ("pe", "pb", "ev_ebitda", "ev_sales", "pe_5y_median", "dividend_yield"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")

    frame["quality_score"] = quality.score(frame)
    frame["value_score"] = value.score(frame)
    frame["revision_score"] = revision.score(frame)
    frame["ownership_score"] = ownership.score(frame)
    frame["tm_score"] = momentum.score(frame)

    # T-S, gated on the quality score computed a moment ago from filed data.
    ts = pd.Series(np.nan, index=frame.index, dtype="float64")
    status = pd.Series("none", index=frame.index, dtype="object")
    for symbol in frame.index:
        history = histories[symbol]
        index = history.index_at(as_of)
        if index is None:
            continue
        setup = _support_at(
            history, index, frame.loc[symbol], quality_gate=quality_gate
        )
        setups[symbol] = setup
        if setup.score is not None:
            ts.loc[symbol] = setup.score
            status.loc[symbol] = setup.status

    frame["ts_score"] = ts
    frame["setup_status"] = status
    frame["technical"] = frame[["tm_score", "ts_score"]].max(axis=1, skipna=True)
    frame["winning_setup"] = np.where(
        frame["ts_score"].notna()
        & (frame["tm_score"].isna() | (frame["ts_score"] > frame["tm_score"])),
        "support",
        np.where(frame["tm_score"].notna(), "momentum", "none"),
    )
    frame.attrs["setups"] = setups
    return frame


def _or_nan(value: float | None) -> float:
    return np.nan if value is None else float(value)


def _rated_at(zones: list[Zone], index: int, *, timeframe: str) -> list[Zone]:
    """The live zones, with `strength` recomputed as of this bar.

    Zones are built once over the whole frame, so `zone.strength` is what the
    level looks like at the end of history. Handing that to the support scorer
    at a 2025 rebalance leaks 2026 into the score. Replacing it on a copy keeps
    the built zones reusable across every date.
    """
    out: list[Zone] = []
    for zone in zones:
        rated = replace(zone, strength=rate_strength(zone, at_index=index, timeframe=timeframe))
        out.append(rated)
    return out


def _overhead_at(history: SymbolHistory, index: int, price: float) -> dict:
    """What stands above the price, and how it has behaved there.

    These are the features the resistance work added, exposed to the panel so
    the sweep can measure whether any of them actually predicts. Three claims
    are being put on trial:

      * a strong overhead level should cap the six-month return, so
        `resistance_strength` should predict negatively and `headroom` — the
        room to it — positively;
      * a failed breakout should predict negatively and hard, because the
        buyers who chased the break become supply;
      * `zone_respect` on the support below should predict positively, since a
        level that has held every test is a better floor than one that has been
        broken twice.

    None of that is assumed anywhere in the scoring. It is recorded so it can be
    checked.
    """
    zones = history.daily_zones
    above = live_zones_above(zones, price, at_index=index)
    below = live_zones_below(zones, price, at_index=index)

    nearest = above[0] if above else None
    floor_below = below[0] if below else None

    breakout = (
        reversal.false_breakout(history.daily, index, ceil=nearest.ceil, timeframe="daily")
        if nearest else None
    )

    # Candle shapes at whichever band the bar actually reached. Recorded, not
    # scored: the whole point is to find out whether the shapes add anything
    # over the location, and a pattern that has been quietly folded into a
    # score can never answer that.
    patterns: dict = {}
    if floor_below is not None:
        patterns.update(
            candles.at_support(
                history.daily, index, floor=floor_below.floor, ceil=floor_below.ceil
            )
        )
    if nearest is not None:
        patterns.update(
            candles.at_resistance(
                history.daily, index, floor=nearest.floor, ceil=nearest.ceil
            )
        )
    patterns = {k: float(v) for k, v in patterns.items()}
    for name in candles.PANEL_FEATURES:
        patterns.setdefault(name, 0.0)

    return {
        **patterns,
        # Rated at this bar, not at the end of the frame. `zone.strength` was
        # computed once over the whole history when the zones were built, so
        # reading it here would tell a January rebalance how the level behaved
        # in June.
        "resistance_strength": (
            rate_strength(nearest, at_index=index, timeframe="daily") if nearest else np.nan
        ),
        "resistance_respect": nearest.respect_at(index) if nearest else np.nan,
        # Fraction of the current price, so it is comparable across stocks.
        "headroom": (nearest.floor / price - 1.0) if nearest else np.nan,
        "false_breakout": 1.0 if breakout else 0.0,
        "rejected_at_resistance": float(
            reversal.rejected_at_resistance(
                history.daily, index, floor=nearest.floor, ceil=nearest.ceil
            )
        ) if nearest else 0.0,
        "zone_respect": _or_nan(floor_below.respect_at(index) if floor_below else None),
        "zone_strength": (
            rate_strength(floor_below, at_index=index, timeframe="daily")
            if floor_below else np.nan
        ),
    }


def _support_at(
    history: SymbolHistory, index: int, row: pd.Series, *, quality_gate: bool
) -> support.SupportSetup:
    price = float(history.daily["close"].iloc[index])

    # Only zones that had formed and had not yet broken by this bar, each
    # re-rated as of it — `zone.strength` as built describes the end of the
    # frame, which at a rebalance in the middle of one is the future.
    daily_live = _rated_at(
        [z for z in history.daily_zones if z.is_live(index)], index, timeframe="daily"
    )
    weekly_index = int(
        history.weekly.index.searchsorted(history.daily.index[index], side="right") - 1
    )
    weekly_live = _rated_at(
        [z for z in history.weekly_zones if z.is_live(weekly_index)],
        weekly_index,
        timeframe="weekly",
    )

    nearest = min(
        (z for z in daily_live if z.floor <= price),
        key=lambda z: price - z.mid,
        default=None,
    )
    confirmation = reversal.confirm(
        history.daily,
        index=index,
        floor=nearest.floor if nearest else price,
        ceil=nearest.ceil if nearest else price,
        rsi=history.rsi,
        macd_hist=history.macd_hist,
        sma20=history.sma20,
        timeframe="daily",
    )

    return support.evaluate(
        frame=history.daily,
        index=index,
        price=price,
        atr=history.atr,
        zones=daily_live,
        weekly_zones=weekly_live,
        pivots=history.pivots,
        confirmation=confirmation,
        quality_score=None if pd.isna(row.get("quality_score")) else float(row["quality_score"]),
        quality_gate=quality_gate,
        hard_excluded=bool(row.get("excluded", False)),
    )


def blend(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    pillars = {
        "quality": frame["quality_score"],
        "value": frame["value_score"],
        "revision": frame.get("revision_score"),
        "ownership": frame.get("ownership_score"),
        "technical": frame["technical"],
    }
    weighted = pd.Series(0.0, index=frame.index)
    available = pd.Series(0.0, index=frame.index)
    for name, series in pillars.items():
        weight = weights.get(name, 0.0)
        if weight <= 0 or series is None:
            continue
        weighted += series.fillna(0.0) * weight
        available += series.notna().astype("float64") * weight

    out = weighted / available.replace(0.0, np.nan)
    return out.mask(frame["excluded"].astype(bool))


def _last(frame: pd.DataFrame, column: str):
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.iloc[-1]) if len(values) else None
