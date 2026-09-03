"""Screener.in parsing and the fundamental scores."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from n500.jobs.load_fundamentals import annualise
from n500.scoring import quality, redflags, value
from n500.scoring.redflags import Verdict
from n500.sources import screener


# --- value parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234", 1234.0), ("-1,234", -1234.0), ("19%", 19.0),
        ("-12%", -12.0), ("", None), ("-", None), ("N/A", None), (None, None),
    ],
)
def test_number_parsing(raw, expected):
    assert screener.to_number(raw) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Mar 2024", (date(2024, 3, 31), 12)),
        ("Dec 2023", (date(2023, 12, 31), 12)),
        ("Jun 2025", (date(2025, 6, 30), 12)),
        ("TTM", None),
        ("", None),
    ],
)
def test_period_parsing(label, expected):
    assert screener.parse_period(label) == expected


def test_a_fifteen_month_year_parses_and_reports_its_length():
    """Screener appends the duration when a company changes its year-end.
    'Mar 202315m' crashed the parser on symbol 17 of the first full sweep."""
    assert screener.parse_period("Mar 202315m") == (date(2023, 3, 31), 15)
    assert screener.parse_period("Sep 20249m") == (date(2024, 9, 30), 9)


def test_filing_dates_are_estimated_conservatively():
    """Later than reality is the safe direction: it can only make a backtest
    pessimistic, whereas earlier is look-ahead bias."""
    quarter = screener.estimated_filing_date(date(2025, 9, 30), annual=False)
    annual = screener.estimated_filing_date(date(2025, 3, 31), annual=True)
    assert quarter > date(2025, 9, 30)
    assert (quarter - date(2025, 9, 30)).days == 45
    assert (annual - date(2025, 3, 31)).days == 60


# --- odd reporting periods ------------------------------------------------


def test_a_fifteen_month_year_is_annualised():
    """Otherwise the change of calendar reads as a 25% growth spurt followed by
    a collapse, and both are artefacts."""
    record = {"period_months": 15, "revenue": 1500.0, "pat": 150.0, "eps": 10.0}
    out = annualise(record)
    assert out["revenue"] == pytest.approx(1200.0)
    assert out["pat"] == pytest.approx(120.0)
    # EPS is per-share and already normalised by the market; it is not a flow.
    assert out["eps"] == 10.0
    assert out["annualised_from_months"] == 15


def test_a_normal_year_is_untouched():
    record = {"period_months": 12, "revenue": 1000.0}
    assert annualise(record) is record


# --- growth maths ---------------------------------------------------------


def test_cagr_over_three_years():
    assert quality.cagr([100.0, 110.0, 121.0, 133.1], 3) == pytest.approx(0.1, abs=1e-9)


def test_cagr_refuses_a_negative_or_zero_base():
    """A company going from a loss to a profit has no meaningful growth rate,
    and a fabricated one would dominate a percentile rank."""
    assert quality.cagr([-50.0, 10.0, 20.0, 30.0], 3) is None
    assert quality.cagr([0.0, 10.0, 20.0, 30.0], 3) is None


def test_cagr_needs_enough_history():
    assert quality.cagr([100.0, 110.0], 3) is None


def test_yoy_compares_the_same_quarter_last_year():
    assert quality.yoy([100.0, 50.0, 60.0, 70.0, 120.0], 4) == pytest.approx(0.2)


def test_margin_expanding_needs_two_consecutive_improvements():
    assert value.margin_expanding([10.0, 12.0, 14.0])
    assert not value.margin_expanding([10.0, 14.0, 12.0])
    assert not value.margin_expanding([14.0, 12.0])


# --- red flags ------------------------------------------------------------


def test_a_check_that_could_not_run_is_unknown_not_passed():
    """Screener publishes no pledge figure. Reporting that as clear would give
    false comfort about the commonest way an Indian mid-cap goes wrong."""
    flag = redflags.promoter_pledge(None, checked=False)
    assert flag.verdict is Verdict.UNKNOWN
    assert not flag.excludes


def test_a_high_pledge_excludes_when_it_can_be_checked():
    assert redflags.promoter_pledge(35.0, checked=True).verdict is Verdict.FAIL
    assert redflags.promoter_pledge(5.0, checked=True).verdict is Verdict.PASS


def test_no_promoter_is_not_applicable_rather_than_a_failure():
    """ITC, HDFC Bank and Infosys genuinely have no promoter. Treating that as
    a failed check would exclude some of the best businesses in the index."""
    flag = redflags.promoter_selling([], has_promoter=False)
    assert flag.verdict is Verdict.NOT_APPLICABLE
    assert not flag.excludes


def test_promoter_selling_excludes_a_large_drop():
    assert redflags.promoter_selling(
        [60.0, 59.0, 58.0, 57.0, 55.0], has_promoter=True
    ).verdict is Verdict.FAIL
    assert redflags.promoter_selling(
        [60.0, 60.0, 59.5, 59.4, 59.3], has_promoter=True
    ).verdict is Verdict.PASS


def test_cash_conversion_catches_profit_that_is_not_cash():
    assert redflags.cash_conversion(
        [10.0, 12.0, 8.0], [100.0, 110.0, 120.0], is_financial=False
    ).verdict is Verdict.FAIL
    assert redflags.cash_conversion(
        [110.0, 120.0, 130.0], [100.0, 110.0, 120.0], is_financial=False
    ).verdict is Verdict.PASS


def test_cash_conversion_is_skipped_for_lenders():
    """A bank's operating cash flow tracks its deposit and loan-book movements
    and routinely goes deeply negative in a perfectly healthy year."""
    flag = redflags.cash_conversion([-500.0], [100.0], is_financial=True)
    assert flag.verdict is Verdict.NOT_APPLICABLE
    assert not flag.excludes


def test_receivable_bloat_flags_a_debtor_day_jump():
    assert redflags.receivable_bloat([50.0, 90.0]).verdict is Verdict.FAIL
    assert redflags.receivable_bloat([50.0, 55.0]).verdict is Verdict.PASS


def test_receivable_bloat_is_skipped_for_lenders():
    """Receivables are the loan book, so debtor days describe the product."""
    flag = redflags.receivable_bloat([20.0, 200.0], is_financial=True)
    assert flag.verdict is Verdict.NOT_APPLICABLE
    assert not flag.excludes


def test_summary_reports_failures_and_unknowns_only():
    flags = [
        redflags.Flag("a", Verdict.PASS),
        redflags.Flag("b", Verdict.FAIL, "bad"),
        redflags.Flag("c", Verdict.UNKNOWN, "not checked"),
        redflags.Flag("d", Verdict.NOT_APPLICABLE),
    ]
    names = {f["name"] for f in redflags.summarise(flags)}
    assert names == {"b", "c"}
    assert redflags.excluded(flags)


# --- scoring branches -----------------------------------------------------


def q_frame(n=20, **overrides):
    data = {
        "sector": ["Fin"] * n,
        "is_financial": [False] * n,
        "revenue_cagr_3y": np.linspace(-0.05, 0.30, n),
        "pat_cagr_3y": np.linspace(-0.10, 0.40, n),
        "quarter_yoy": np.linspace(-0.20, 0.50, n),
        "roe": np.linspace(2, 35, n),
        "roce": np.linspace(3, 40, n),
        "opm": np.linspace(2, 30, n),
        "opm_trend": np.linspace(-1, 2, n),
        "debt_equity": np.linspace(3, 0.05, n),
        "interest_cover": np.linspace(1, 25, n),
        "debt_trend": np.linspace(2, -2, n),
        "cfo_to_pat": np.linspace(0.3, 1.6, n),
        "fcf_positive": np.ones(n),
        "pe": np.linspace(60, 8, n),
        "peg": np.linspace(4, 0.4, n),
    }
    data.update(overrides)
    return pd.DataFrame(data, index=[f"S{i:02d}" for i in range(n)])


def test_quality_score_is_bounded_and_ordered():
    out = quality.score(q_frame())
    assert out.min() >= 0 and out.max() <= 100
    assert out.iloc[-1] > out.iloc[0]


def test_a_lender_is_not_scored_on_debt_or_cash_conversion():
    """Scoring a bank on the general set would push all 101 Financial Services
    names to the bottom — not because they are bad but because the wrong
    questions were asked."""
    n = 20
    # Debt and cash-quality inputs are missing entirely, as they are for a bank.
    bank = q_frame(
        n,
        is_financial=[True] * n,
        debt_equity=np.full(n, np.nan),
        interest_cover=np.full(n, np.nan),
        debt_trend=np.full(n, np.nan),
        cfo_to_pat=np.full(n, np.nan),
        fcf_positive=np.full(n, np.nan),
        roce=np.full(n, np.nan),
        opm=np.full(n, np.nan),
    )
    scores = quality.score(bank)
    assert scores.notna().all(), "a bank must still receive a score"
    assert scores.max() > 60, "a good bank must be able to score well"


def test_a_general_company_missing_everything_is_not_scored():
    n = 20
    empty = q_frame(n)
    for column in empty.columns:
        if column not in ("sector", "is_financial"):
            empty[column] = np.nan
    assert quality.score(empty).isna().all()


def v_frame(n=20, **overrides):
    data = {
        "sector": ["Fin"] * n,
        "pe": np.linspace(60, 8, n),
        "pb": np.linspace(9, 0.5, n),
        "ev_ebitda": np.linspace(35, 4, n),
        "ev_sales": np.linspace(12, 0.5, n),
        "pe_5y_median": np.full(n, 25.0),
        "dividend_yield": np.linspace(0, 6, n),
        "margin_expanding": [False] * n,
    }
    data.update(overrides)
    return pd.DataFrame(data, index=[f"S{i:02d}" for i in range(n)])


def test_value_score_rewards_cheapness():
    out = value.score(v_frame())
    assert out.iloc[-1] > out.iloc[0]
    assert out.min() >= 0 and out.max() <= 100


def test_trading_below_its_own_history_scores_well():
    cheap = value.component_scores(v_frame(pe=np.full(20, 12.5)))["own_history"].iloc[0]
    rich = value.component_scores(v_frame(pe=np.full(20, 50.0)))["own_history"].iloc[0]
    assert cheap > rich


# --- lender detection -----------------------------------------------------


def test_a_lender_is_detected_from_either_signal():
    """Only 11 of 19 Financial Services names reported with a bank's P&L in the
    first sample. The other eight were scored as manufacturers, and four were
    excluded for the negative operating cash flow that is just a growing loan
    book."""
    from n500.jobs.compute_fundamental_scores import is_lender

    assert is_lender({"company_type": "financial", "sector": "Financial Services"})
    # An NBFC filing a conventional P&L — 360ONE, ANGELONE, BAJAJFINSV.
    assert is_lender({"company_type": "general", "sector": "Financial Services"})
    # A bank whose sector label is missing but whose statements give it away.
    assert is_lender({"company_type": "financial", "sector": None})
    assert not is_lender({"company_type": "general", "sector": "Chemicals"})


def test_the_snapshot_never_emits_nan():
    """json.dumps writes a bare NaN, which JSON.parse rejects — the page then
    renders empty with only a console error to show for it.

    An earlier version raised on NaN instead of cleaning it, on the theory that
    failing loudly beats failing quietly. That was wrong for this payload: a
    NaN here means the company simply has no target price, which is ordinary
    and is exactly what `null` is for in the types the front end already
    declares. Refusing to export the whole universe because ITC has no target
    would be the worse failure.
    """
    import json

    from n500.jobs.export_snapshot import _clean, serialise

    assert _clean(np.nan) is None
    assert _clean(None) is None
    assert _clean("financial") == "financial"

    text = serialise({"rows": [{"company_type": float("nan"), "pe": 12.5}]})
    assert "NaN" not in text
    assert json.loads(text)["rows"][0] == {"company_type": None, "pe": 12.5}


# --- sanity assertions ----------------------------------------------------


def _fundamentals(**kw):
    defaults = dict(
        symbol="TEST", company_type="general",
        annual=[{"period_end": date(2025, 3, 31), "revenue": 100.0, "opm": 12.0}],
        quarterly=[{"period_end": date(2025, 6, 30), "revenue": 25.0}],
    )
    defaults.update(kw)
    return screener.Fundamentals(**defaults)


def test_a_short_history_is_accepted_not_rejected():
    """The first full sweep discarded 49 companies and every one was
    legitimate — 38 recent listings, six loss-makers, five with few quarters."""
    screener._assert_sane(_fundamentals())


def test_an_extreme_but_real_loss_making_margin_is_accepted():
    """OLAELEC, HONASA and CARTRADE genuinely report margins of several
    hundred percent negative."""
    screener._assert_sane(
        _fundamentals(annual=[
            {"period_end": date(2025, 3, 31), "revenue": 10.0, "opm": -450.0},
        ])
    )


def test_an_empty_table_still_fails_loudly():
    with pytest.raises(screener.ScreenerError, match="parsed to nothing"):
        screener._assert_sane(_fundamentals(annual=[]))
    with pytest.raises(screener.ScreenerError, match="parsed to nothing"):
        screener._assert_sane(_fundamentals(quarterly=[]))


def test_a_column_that_is_not_a_percentage_fails():
    with pytest.raises(screener.ScreenerError, match="not a percentage"):
        screener._assert_sane(
            _fundamentals(annual=[
                {"period_end": date(2025, 3, 31), "revenue": 10.0, "opm": 45000.0},
            ])
        )


def test_shareholding_that_does_not_sum_to_a_whole_fails():
    with pytest.raises(screener.ScreenerError, match="sums to"):
        screener._assert_sane(
            _fundamentals(shareholding=[
                {"period_end": date(2025, 6, 30), "promoter_pct": 30.0, "fii_pct": 10.0},
            ])
        )


# --- reporting basis ------------------------------------------------------


def test_a_stale_consolidated_page_is_not_usable():
    """Colgate's consolidated figures stop at 2010 because it had subsidiaries
    then and reports standalone now. Accepting that page silently loses the
    company's entire recent history."""
    stale = _fundamentals(
        annual=[{"period_end": date(2010, 3, 31), "revenue": 100.0}],
        quarterly=[{"period_end": date(2010, 6, 30), "revenue": 25.0}],
    )
    assert not screener.is_usable(stale, today=date(2026, 9, 2))


def test_an_empty_consolidated_page_is_not_usable():
    """Abbott India files standalone only; its consolidated tables are bare."""
    assert not screener.is_usable(_fundamentals(annual=[]), today=date(2026, 9, 2))
    assert not screener.is_usable(_fundamentals(quarterly=[]), today=date(2026, 9, 2))


def test_a_current_consolidated_page_is_usable():
    current = _fundamentals(
        annual=[{"period_end": date(2026, 3, 31), "revenue": 100.0}],
        quarterly=[{"period_end": date(2026, 6, 30), "revenue": 25.0}],
    )
    assert screener.is_usable(current, today=date(2026, 9, 2))


def test_a_company_that_simply_has_not_filed_yet_is_still_usable():
    """A March year-end filed in May is 17 months stale by the following
    August, which is normal and must not trigger a pointless second fetch."""
    lagging = _fundamentals(
        annual=[{"period_end": date(2025, 3, 31), "revenue": 100.0}],
        quarterly=[{"period_end": date(2026, 6, 30), "revenue": 25.0}],
    )
    assert screener.is_usable(lagging, today=date(2026, 8, 1))
