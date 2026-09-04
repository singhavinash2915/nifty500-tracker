"""Bhavcopy parsing and, more importantly, corporate-action adjustment.

An unadjusted 1:2 split reads as a 50% crash and would poison every momentum
figure in the model, so the adjustment path is tested harder than the parser.
"""

from datetime import date

import pytest

from n500.sources import bhavcopy
from n500.sources.bhavcopy import BhavcopyError, Quote

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,"
    "XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,"
    "LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
    "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty\n"
)


def row(symbol="TEST", *, series="EQ", kind="STK", o=100, h=110, l=95, c=105, pc=104, vol=1000):
    return (
        f"2026-09-02,2026-09-02,CM,NSE,{kind},1,INE000A01001,{symbol},{series},"
        f",,,,{symbol} LTD,{o},{h},{l},{c},{c},{pc},,{c},,,{vol},1000.0,10,F1,1\n"
    )


def make_file(n=1500, **kw):
    return HEADER + "".join(row(f"SYM{i:04d}", **kw) for i in range(n))


def test_parses_equities_only():
    text = HEADER + row("HDFCBANK") + row("SGBJUN28", series="GB") + row(
        "NIFTY", kind="IDX"
    ) + "".join(row(f"S{i}") for i in range(1500))
    quotes = bhavcopy.parse(text)
    assert "HDFCBANK" in quotes
    assert "SGBJUN28" not in quotes    # government bond series
    assert "NIFTY" not in quotes       # index, not a stock


def test_rejects_a_file_missing_columns():
    with pytest.raises(BhavcopyError, match="missing columns"):
        bhavcopy.parse("TradDt,TckrSymb\n2026-09-02,HDFCBANK\n")


def test_rejects_a_suspiciously_short_file():
    with pytest.raises(BhavcopyError, match="expected at least"):
        bhavcopy.parse(make_file(5))


def test_rejects_a_close_outside_the_days_range():
    text = HEADER + row("BROKEN", c=200, h=110, l=95) + "".join(
        row(f"S{i}") for i in range(1500)
    )
    with pytest.raises(BhavcopyError, match="outside"):
        bhavcopy.parse(text)


def test_rejects_a_file_stamped_with_the_wrong_date():
    with pytest.raises(BhavcopyError, match="stamped"):
        bhavcopy.parse(make_file(), on=date(2026, 1, 1))


# --- corporate actions ----------------------------------------------------


def q(day, close, prev_close=None, *, open_=None, symbol="TEST"):
    price_open = close if open_ is None else open_
    return Quote(
        symbol=symbol,
        date=date(2026, 1, day),
        open=price_open, high=max(price_open, close), low=min(price_open, close),
        close=close, prev_close=prev_close if prev_close is not None else close,
        volume=1000, isin="INE000A01001",
    )


def test_ordinary_days_need_no_adjustment():
    quotes = [q(1, 100), q(2, 102), q(3, 101)]
    assert [r["adj_close"] for r in bhavcopy.adjust(quotes)] == [100.0, 102.0, 101.0]
    assert bhavcopy.corporate_actions(quotes) == []


def test_prvsclsgpric_is_not_used_to_detect_actions():
    """NSE reports the raw previous close there, not an adjusted one —
    BAJFINANCE's 1:10 split showed ClsPric 938.00 beside PrvsClsgPric 9331.00.
    Detection must come from the close-to-close ratio instead."""
    quotes = [q(1, 9331), q(2, 938, prev_close=9331)]
    actions = bhavcopy.corporate_actions(quotes)
    assert len(actions) == 1
    assert actions[0][1] == pytest.approx(0.1)


def test_a_one_for_two_split_halves_the_history():
    quotes = [q(1, 100), q(2, 100), q(3, 50.24)]
    rows = bhavcopy.adjust(quotes)
    assert [r["close"] for r in rows] == [100.0, 100.0, 50.24]
    # Snapped to exactly 0.5, not the noisy observed 0.5024.
    assert [r["adj_close"] for r in rows] == [50.0, 50.0, 50.24]


def test_adjustment_makes_the_split_return_correct():
    """The point of the whole exercise: a 1:2 split plus a small gain must read
    as a small gain, not as a 50% crash."""
    quotes = [q(1, 100), q(2, 100), q(3, 51)]
    rows = bhavcopy.adjust(quotes)
    raw = rows[2]["close"] / rows[1]["close"] - 1
    adjusted = rows[2]["adj_close"] / rows[1]["adj_close"] - 1
    assert raw == pytest.approx(-0.49)
    assert adjusted == pytest.approx(0.02)


def test_two_splits_compound():
    quotes = [q(1, 100), q(2, 50), q(3, 25)]
    assert [r["adj_close"] for r in bhavcopy.adjust(quotes)] == [25.0, 25.0, 25.0]


def test_ordinary_volatility_is_not_an_action():
    # An 8% fall is a bad day, not a bonus issue.
    quotes = [q(1, 100), q(2, 92)]
    assert bhavcopy.corporate_actions(quotes) == []
    assert [r["adj_close"] for r in bhavcopy.adjust(quotes)] == [100.0, 92.0]


def test_a_reverse_split_is_detected():
    quotes = [q(1, 10), q(2, 100)]
    actions = bhavcopy.corporate_actions(quotes)
    assert len(actions) == 1
    assert actions[0][1] == pytest.approx(10.0)


def test_a_circuit_limited_crash_is_not_adjusted_away():
    """INDUSINDBK fell 27% on the derivatives accounting scandal: it opened at
    exactly 0.9000 — the 10% lower circuit — and slid intraday. Detecting on
    the close would have snapped that to a ratio and erased a real drawdown."""
    quotes = [q(1, 900.50), q(2, 655.95, open_=810.45)]

    assert bhavcopy.corporate_actions(quotes) == []
    assert [r["adj_close"] for r in bhavcopy.adjust(quotes)] == [900.50, 655.95]

    unexplained = bhavcopy.unexplained_moves(quotes)
    assert len(unexplained) == 1
    assert unexplained[0][1] == pytest.approx(0.7284, abs=1e-4)


def test_a_split_is_detected_from_the_open_even_if_the_day_rallies():
    """SIEMENS opened at 0.4971 of the previous close on its ex-date, then
    rallied 14.8% during the session. The close alone would misread the ratio."""
    quotes = [q(1, 4928.15), q(2, 2812.45, open_=2450.00)]
    actions = bhavcopy.corporate_actions(quotes)
    assert len(actions) == 1
    assert actions[0][1] == pytest.approx(0.5)


def test_a_recognised_action_is_not_reported_as_unexplained():
    assert bhavcopy.unexplained_moves([q(1, 100), q(2, 50)]) == []


def test_snapping_tidies_the_ratio_but_is_not_the_safeguard():
    """The plausible set is dense, so nearly any value finds a match. What
    separates a split from a crash is the size of the opening gap."""
    from n500.sources.bhavcopy import _snap
    assert _snap(0.5024) == pytest.approx(0.5)
    assert _snap(0.1005) == pytest.approx(0.1)


def test_adjust_sorts_by_date():
    quotes = [q(3, 50), q(1, 100), q(2, 100)]
    rows = bhavcopy.adjust(quotes)
    assert [r["date"] for r in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_adjust_handles_an_empty_history():
    assert bhavcopy.adjust([]) == []


# --- series inclusion and gap guarding ------------------------------------


def test_surveillance_series_are_kept_as_the_same_stock():
    """ITI spent 61 straight sessions in BE. Dropping those punched a hole in
    its history that later read as a corporate action."""
    text = HEADER + row("ITI", series="BE") + row("SMEC", series="SM") + "".join(
        row(f"S{i}") for i in range(1500)
    )
    quotes = bhavcopy.parse(text)
    assert "ITI" in quotes       # surveillance, but still the same equity
    assert "SMEC" not in quotes  # SME platform, a different instrument


def test_bz_series_is_kept_too():
    text = HEADER + row("WATCHED", series="BZ") + "".join(row(f"S{i}") for i in range(1500))
    assert "WATCHED" in bhavcopy.parse(text)


def test_no_action_is_inferred_across_a_gap_in_history():
    """A three-month hole must not be read as a split: the two closes either
    side of a suspension are unrelated."""
    quotes = [
        Quote("T", date(2026, 1, 10), 440, 445, 435, 442.70, 441, 1000, "INE000A01001"),
        Quote("T", date(2026, 4, 15), 250, 258, 249, 221.35, 254.0, 1000, "INE000A01001"),
    ]
    assert bhavcopy.corporate_actions(quotes) == []
    assert bhavcopy.unexplained_moves(quotes) == []


def test_an_action_over_a_long_weekend_is_still_detected():
    # Friday to Tuesday is four calendar days — inside the tolerance.
    assert len(bhavcopy.corporate_actions([q(2, 100), q(6, 50)])) == 1


def test_candidate_days_include_weekends():
    """NSE trades on Budget day, 1 February, even on a Saturday. Skipping it
    made the next session's PrvsClsgPric look like a 2-4% corporate action."""
    from datetime import date as d
    from n500.jobs.load_prices import trading_day_candidates

    days = trading_day_candidates(7, end=d(2025, 2, 3))
    assert d(2025, 2, 1) in days       # Saturday — Budget session
    assert d(2025, 2, 2) in days       # Sunday — will 404 and be skipped


def test_a_miss_recorded_before_the_day_ended_is_not_believed(tmp_path, monkeypatch):
    """A probe at 09:11 on a trading morning wrote a permanent "no session"
    marker for that day. The pipeline would have sat one day behind for good,
    and a skipped date looks exactly like a holiday in the logs."""
    import os, time
    from datetime import date as d, datetime, timedelta

    monkeypatch.setattr(bhavcopy, "CACHE_DIR", tmp_path)
    session = d(2026, 9, 3)
    marker = bhavcopy._miss_path(session)
    marker.touch()

    # Recorded during the session's own morning: not to be trusted.
    morning = datetime.combine(session, datetime.min.time()) + timedelta(hours=9)
    os.utime(marker, (morning.timestamp(), morning.timestamp()))
    assert not bhavcopy._miss_is_settled(session)

    # Recorded the next day, once the day was genuinely over.
    after = datetime.combine(session, datetime.min.time()) + timedelta(days=1, hours=2)
    os.utime(marker, (after.timestamp(), after.timestamp()))
    assert bhavcopy._miss_is_settled(session)


def test_no_marker_means_no_cached_miss(tmp_path, monkeypatch):
    from datetime import date as d

    monkeypatch.setattr(bhavcopy, "CACHE_DIR", tmp_path)
    assert not bhavcopy._miss_is_settled(d(2026, 9, 3))


class TestSnapPrefersTheSimplestFit:
    """Nearest is the wrong test when the candidate set is crowded.

    But only outside the exact band — a candidate the stock opened almost
    exactly on is evidence in its own right, and simplicity must not override
    it. Both halves are pinned here against real observed factors.
    """

    def test_an_almost_exact_match_wins_even_when_it_is_baroque(self):
        # SBC opened at a factor of 0.72702. 8/11 is 0.03% away and 5/7 is
        # simpler but 1.8% away; simplicity-first picked 5/7 and was wrong.
        assert bhavcopy._snap(0.72702) == pytest.approx(8 / 11)

    def test_an_exact_common_ratio_is_not_replaced_by_a_simpler_one(self):
        # KPIGREEN opened at exactly 0.7. 5/7 is a simpler fraction; taking it
        # would be a regression.
        assert bhavcopy._snap(0.7) == pytest.approx(0.7)

    def test_a_clean_split_beats_a_closer_compound_ratio(self):
        # TATASTEEL's 1:10 on 28 July 2022: opened 98.10 against a previous
        # close of 959.40. 7/68 (a 1:4 split with a 10:7 bonus) is nearer, and
        # picking it restated history 2.9% too high.
        assert bhavcopy._snap(98.1 / 959.4) == pytest.approx(0.1)

    def test_an_exact_ratio_still_snaps_to_itself(self):
        for ratio in (0.5, 0.2, 0.1, 0.25):
            assert bhavcopy._snap(ratio) == pytest.approx(ratio)

    def test_noise_around_a_half_snaps_to_a_half(self):
        assert bhavcopy._snap(0.5023) == pytest.approx(0.5)
        assert bhavcopy._snap(0.4930) == pytest.approx(0.5)

    def test_a_gap_between_plausible_ratios_returns_none(self):
        # The candidate set is dense, so genuine gaps are narrow — 0.9395 sits
        # between 14/15 and 15/16 and matches neither within tolerance. Only
        # 101 of 2,850 sampled factors fail to match at all, which is the
        # crowding that makes preferring the simplest fit necessary rather than
        # merely tidy.
        assert bhavcopy._snap(0.9395) is None


class TestLegacyLayout:
    """The pre-2024 bhavcopy, which names its columns differently."""

    HEADER = (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        "TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
    )

    def rows(self, n: int = 1300) -> str:
        body = "".join(
            f"SYM{i},EQ,100,105,99,104,104,98,1000,100000,"
            f"02-JAN-2023,50,INE{i:09d},\n"
            for i in range(n)
        )
        return self.HEADER + body

    def test_it_is_detected_from_the_header_not_the_date(self):
        quotes = bhavcopy.parse(self.rows())
        assert len(quotes) == 1300
        assert quotes["SYM0"].date == date(2023, 1, 2)
        assert quotes["SYM0"].close == pytest.approx(104.0)

    def test_the_day_month_year_timestamp_is_parsed(self):
        quotes = bhavcopy.parse(self.rows(), on=date(2023, 1, 2))
        assert quotes["SYM7"].date == date(2023, 1, 2)

    def test_a_thin_file_is_rejected(self):
        with pytest.raises(bhavcopy.BhavcopyError, match="legacy layout"):
            bhavcopy.parse(self.rows(10))

    def test_the_url_switches_at_the_udiff_cut(self):
        assert "historical" in bhavcopy._archive_url(date(2023, 12, 29))
        assert "BhavCopy_NSE_CM" in bhavcopy._archive_url(date(2024, 1, 1))
