"""Backtest mechanics — mostly guards against the ways a backtest lies."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from n500.backtest import engine, pointintime
from n500.backtest.engine import ROUND_TRIP_COST, Result, Trade


def calendar(n=600):
    return pd.bdate_range("2024-01-01", periods=n)


# --- point-in-time filtering ----------------------------------------------


def test_filed_by_excludes_results_not_yet_published():
    """Q2 results for the quarter ending 30 September are filed in early
    November. Reading them on 30 September is trading on information nobody
    had, and it invents a strategy that loses money live."""
    frame = pd.DataFrame(
        {
            "period_end": ["2025-06-30", "2025-09-30"],
            "filed_on": ["2025-08-14", "2025-11-14"],
            "pat": [100.0, 120.0],
        }
    )
    visible = pointintime.filed_by(frame, date(2025, 10, 1))
    assert list(visible["pat"]) == [100.0]

    later = pointintime.filed_by(frame, date(2025, 11, 20))
    assert list(later["pat"]) == [100.0, 120.0]


def test_filed_by_on_the_filing_date_itself_includes_the_row():
    frame = pd.DataFrame({"period_end": ["2025-06-30"], "filed_on": ["2025-08-14"], "pat": [1.0]})
    assert len(pointintime.filed_by(frame, date(2025, 8, 14))) == 1


def test_filed_by_drops_rows_with_no_filing_date():
    frame = pd.DataFrame({"period_end": ["2025-06-30"], "filed_on": [None], "pat": [1.0]})
    assert pointintime.filed_by(frame, date(2026, 1, 1)).empty


# --- valuation built from what was known ----------------------------------


def test_trailing_eps_prefers_four_filed_quarters():
    quarterly = pd.DataFrame({"eps": [2.0, 3.0, 4.0, 5.0, 6.0]})
    assert pointintime.trailing_eps(quarterly, pd.DataFrame()) == pytest.approx(18.0)


def test_trailing_eps_falls_back_to_the_last_filed_year():
    annual = pd.DataFrame({"eps": [10.0, 12.0]})
    assert pointintime.trailing_eps(pd.DataFrame({"eps": [1.0]}), annual) == pytest.approx(12.0)


def test_trailing_eps_is_none_when_nothing_is_filed():
    assert pointintime.trailing_eps(pd.DataFrame(), pd.DataFrame()) is None


def test_book_value_per_share_needs_a_share_count():
    annual = pd.DataFrame({"equity": [1000.0]})
    assert pointintime.book_value_per_share(annual, 10.0) == pytest.approx(100.0)
    assert pointintime.book_value_per_share(annual, None) is None
    assert pointintime.book_value_per_share(annual, 0.0) is None


def test_valuation_uses_the_price_on_the_day():
    quarterly = pd.DataFrame({"eps": [1.0, 1.0, 1.0, 1.0]})
    annual = pd.DataFrame(
        {"period_end": ["2025-03-31"], "equity": [400.0], "debt": [100.0],
         "ebitda": [200.0], "revenue": [1000.0], "eps": [4.0]}
    )
    history = pd.Series([50.0, 80.0], index=pd.to_datetime(["2025-03-31", "2025-06-30"]))

    cheap = pointintime.valuation_at(40.0, quarterly, annual, 10.0, history)
    rich = pointintime.valuation_at(80.0, quarterly, annual, 10.0, history)

    assert cheap["pe"] == pytest.approx(10.0)
    assert rich["pe"] == pytest.approx(20.0)
    assert cheap["pb"] == pytest.approx(1.0)
    # EV = price*shares + debt
    assert cheap["ev_ebitda"] == pytest.approx((40 * 10 + 100) / 200)


# --- rebalance calendar ---------------------------------------------------


def test_rebalance_dates_leave_room_for_warmup_and_the_holding_period():
    """A date without a completed forward window would be scored as flat,
    which quietly pulls every statistic toward zero."""
    index = calendar(600)
    dates = engine.month_end_dates(index, warmup=252, forward=126)
    assert dates
    assert pd.Timestamp(dates[0]) >= index[252]
    assert pd.Timestamp(dates[-1]) <= index[-127]


def test_no_rebalances_when_history_is_too_short():
    assert engine.month_end_dates(calendar(300), warmup=252, forward=126) == []


def test_one_rebalance_per_month():
    dates = engine.month_end_dates(calendar(600), warmup=252, forward=126)
    months = [(d.year, d.month) for d in dates]
    assert len(months) == len(set(months))


# --- costs and returns ----------------------------------------------------


def test_costs_are_charged_on_every_trade():
    trade = Trade("X", "momentum", date(2025, 1, 1), 100.0, exit_price=120.0)
    assert trade.gross_return == pytest.approx(0.20)
    assert trade.net_return == pytest.approx(1.20 * (1 - ROUND_TRIP_COST) - 1)
    assert trade.net_return < trade.gross_return


def test_an_open_trade_has_no_return_rather_than_a_zero():
    assert Trade("X", "momentum", date(2025, 1, 1), 100.0).net_return is None


# --- selection ------------------------------------------------------------


def test_selection_caps_any_one_sector():
    """Momentum screens love to hand you eight public-sector banks at once."""
    ranked = pd.DataFrame(
        {
            "score": np.linspace(100, 50, 20),
            "sector": ["Financial Services"] * 15 + ["IT"] * 5,
        },
        index=[f"S{i}" for i in range(20)],
    )
    picked = engine._select(ranked, size=10, sector_cap=0.25)
    counts = picked["sector"].value_counts()
    assert counts.get("Financial Services", 0) <= 2 + 1


def test_selection_takes_the_best_scores_first():
    ranked = pd.DataFrame(
        {"score": [10.0, 90.0, 50.0], "sector": ["A", "B", "C"]},
        index=["low", "high", "mid"],
    )
    assert list(engine._select(ranked, size=2, sector_cap=1.0).index) == ["high", "mid"]


def test_selection_ignores_unscored_rows():
    ranked = pd.DataFrame(
        {"score": [np.nan, 40.0], "sector": ["A", "B"]}, index=["excluded", "ok"]
    )
    assert list(engine._select(ranked, size=5, sector_cap=1.0).index) == ["ok"]


# --- statistics -----------------------------------------------------------


def make_result(returns: list[float]) -> Result:
    result = Result()
    for i, r in enumerate(returns):
        result.trades.append(
            Trade(f"S{i}", "momentum", date(2025, 1, 1), 100.0, exit_price=100.0 * (1 + r))
        )
    return result


def test_summary_reports_the_25_percent_hit_rate():
    stats = engine.summarise(make_result([0.30, 0.30, 0.05, -0.10]), holding_days=126, benchmark=None)
    assert stats["hit_rate_25pct"] == pytest.approx(0.5)
    assert stats["hit_rate"] == pytest.approx(0.75)


def test_summary_on_no_completed_trades_says_so():
    stats = engine.summarise(Result(), holding_days=126, benchmark=None)
    assert stats["trades"] == 0
    assert "note" in stats


def test_decile_study_needs_a_real_sample_per_bucket():
    """Reporting a hit rate off five observations is theatre."""
    thin = pd.DataFrame({"decile": [10] * 5, "forward_return": [0.1] * 5})
    assert engine.decile_study(thin).empty

    thick = pd.DataFrame({"decile": [10] * 40, "forward_return": np.linspace(-0.2, 0.6, 40)})
    study = engine.decile_study(thick)
    assert len(study) == 1
    assert study.iloc[0]["n"] == 40


def test_decile_study_reports_a_distribution_not_a_point():
    frame = pd.DataFrame(
        {"decile": [10] * 100, "forward_return": np.linspace(-0.4, 0.8, 100)}
    )
    row = engine.decile_study(frame).iloc[0]
    for column in ("median", "hit_rate", "hit_rate_25pct", "p10", "p90"):
        assert column in row
    assert row["p10"] < row["median"] < row["p90"]


# --- annualisation of overlapping holds -----------------------------------


def test_overlapping_holds_are_not_compounded_as_if_sequential():
    """Fourteen monthly rebalances with a six-month hold span about eighteen
    months, not seven years. Compounding them turned a 2.36x product into a
    fictitious seven-year CAGR."""
    result = Result()
    for month in range(1, 15):
        for i in range(5):
            result.trades.append(
                Trade(f"S{i}", "momentum", date(2025, month if month <= 12 else month - 12, 1),
                      100.0, exit_price=110.0)
            )
    stats = engine.summarise(result, holding_days=126, benchmark=None)

    # Each hold returns ~10% gross; annualised is about two holds a year.
    assert stats["mean_per_hold"] == pytest.approx(1.10 * (1 - ROUND_TRIP_COST) - 1, abs=1e-6)
    assert stats["annualised"] == pytest.approx((1 + stats["mean_per_hold"]) ** 2 - 1, abs=1e-6)
    # Emphatically not (1.0956 ** 14) ** (1/7) - 1
    assert stats["annualised"] < 0.30


def test_rank_quality_detects_a_score_that_ranks():
    study = pd.DataFrame(
        {
            "decile": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            "median": [0.18, 0.15, 0.12, 0.10, 0.07, 0.05, 0.03, 0.01, -0.02, -0.05],
            "hit_rate_25pct": [0.35, 0.31, 0.27, 0.24, 0.20, 0.17, 0.14, 0.11, 0.08, 0.05],
        }
    )
    verdict = engine.rank_quality(study)
    assert verdict["median_rho"] > 0.9
    assert "ranks" in verdict["verdict"]


def test_rank_quality_calls_out_a_score_that_does_not_rank():
    """The finding that matters most: a flat decile curve means the score is
    not separating anything, however well the top-twenty portfolio did."""
    study = pd.DataFrame(
        {
            "decile": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            "median": [0.037, 0.019, -0.005, 0.017, 0.022, 0.022, -0.014, 0.010, -0.018, 0.020],
            "hit_rate_25pct": [0.17, 0.17, 0.13, 0.15, 0.15, 0.12, 0.13, 0.19, 0.14, 0.16],
        }
    )
    verdict = engine.rank_quality(study)
    assert abs(verdict["median_rho"]) < 0.6
    assert "NO ordering" in verdict["verdict"] or "weak" in verdict["verdict"]


def test_rank_quality_flags_an_inverted_score():
    study = pd.DataFrame(
        {
            "decile": list(range(10, 0, -1)),
            "median": np.linspace(-0.05, 0.18, 10),
            "hit_rate_25pct": np.linspace(0.05, 0.35, 10),
        }
    )
    assert "INVERTED" in engine.rank_quality(study)["verdict"]
