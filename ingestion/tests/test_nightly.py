"""The nightly orchestrator and the serialisation guard."""

import math

import pytest

from n500 import db, serialise
from n500.jobs.run_nightly import Step, plan


def test_every_step_declares_its_prerequisites():
    steps = {s.name: s for s in plan(10, dry_run=True, skip_fundamentals=False)}
    assert steps["scores"].needs == ("technicals", "zones")
    assert steps["alerts"].needs == ("scores",)
    assert steps["universe"].needs == ()


def test_prerequisites_always_come_earlier_in_the_plan():
    """Ordering is a dependency chain, not a preference."""
    order = [s.name for s in plan(10, dry_run=True, skip_fundamentals=False)]
    for step in plan(10, dry_run=True, skip_fundamentals=False):
        for need in step.needs:
            assert order.index(need) < order.index(step.name), f"{need} must precede {step.name}"


def test_the_scrapers_are_allowed_to_fail_and_the_maths_is_not():
    steps = {s.name: s for s in plan(10, dry_run=True, skip_fundamentals=False)}
    assert steps["fundamentals"].tolerate_failure, "an upstream we do not control"
    assert not steps["prices"].tolerate_failure
    assert not steps["scores"].tolerate_failure


def test_fundamentals_can_be_skipped_on_ordinary_nights():
    names = [s.name for s in plan(10, dry_run=True, skip_fundamentals=True)]
    assert "fundamentals" not in names
    # The derived scores still run, from whatever was last fetched.
    assert "fundamental_scores" in names


def test_the_universe_refresh_runs_first_and_always():
    """It is what accumulates index_membership, the only cure for the
    survivorship bias in the backtest."""
    for skip in (True, False):
        assert plan(10, dry_run=True, skip_fundamentals=skip)[0].name == "universe"


def test_dry_run_flag_is_passed_to_every_step():
    for step in plan(10, dry_run=True, skip_fundamentals=False):
        assert "--dry-run" in step.args


# --- the serialisation guard ----------------------------------------------


def test_nan_becomes_null_rather_than_invalid_json():
    """json.dumps writes a bare NaN, which JSON.parse rejects — one missing
    number takes the whole file down and the page renders empty behind a
    caught exception. It happened twice before this helper existed."""
    text = serialise.dumps({"a": float("nan"), "b": 1.5})
    assert "NaN" not in text
    assert '"a": null' in text


def test_infinities_are_cleaned_too():
    text = serialise.dumps({"a": float("inf"), "b": float("-inf")})
    assert "Infinity" not in text


def test_cleaning_reaches_into_nested_structures():
    payload = {"rows": [{"x": float("nan")}, {"y": [1.0, float("nan")]}]}
    cleaned = serialise.clean(payload)
    assert cleaned["rows"][0]["x"] is None
    assert cleaned["rows"][1]["y"] == [1.0, None]


def test_numpy_scalars_survive():
    import numpy as np

    assert serialise.clean(np.float64(1.5)) == 1.5
    assert serialise.clean(np.float64("nan")) is None
    assert serialise.clean(np.bool_(False)) is False


def test_writing_produces_parseable_json(tmp_path):
    import json

    path = serialise.write(tmp_path / "out.json", {"n": float("nan"), "ok": [1, 2]})
    assert json.loads(path.read_text()) == {"n": None, "ok": [1, 2]}


class TestKeysetPaging:
    """Paging by key rather than by offset.

    OFFSET makes the database walk every row it skips, so page 700 of the price
    table costs seven hundred thousand rows of work to return a thousand. That
    is how `export_snapshot` became the one step of ten that failed on a
    statement timeout once the table passed three quarters of a million rows.
    """

    def test_a_single_column_key(self):
        assert db.seek_filter(("symbol",), {"symbol": "ABB"}) == "symbol.gt.ABB"

    def test_a_two_column_key_is_lexicographic(self):
        got = db.seek_filter(("symbol", "date"), {"symbol": "ABB", "date": "2026-01-01"})
        assert got == "symbol.gt.ABB,and(symbol.eq.ABB,date.gt.2026-01-01)"

    def test_a_three_column_key_nests_one_level_further(self):
        got = db.seek_filter(
            ("index_name", "week_start", "symbol"),
            {"index_name": "NIFTY500", "week_start": "2024-02-07", "symbol": "ABB"},
        )
        assert got == (
            "index_name.gt.NIFTY500,"
            "and(index_name.eq.NIFTY500,week_start.gt.2024-02-07),"
            "and(index_name.eq.NIFTY500,week_start.eq.2024-02-07,symbol.gt.ABB)"
        )

    def test_every_key_in_page_order_can_be_expressed(self):
        # A key the filter cannot express would page wrongly, and missing rows
        # look exactly like a quiet day rather than a bug.
        for table, key in db.PAGE_ORDER.items():
            cursor = {column: "x" for column in key}
            assert db.seek_filter(key, cursor), table

    def test_the_clause_count_matches_the_key_length(self):
        for length in (1, 2, 3, 4):
            key = tuple(f"c{i}" for i in range(length))
            got = db.seek_filter(key, {c: "v" for c in key})
            assert got.count(".gt.") == length


# --- idle nights ------------------------------------------------------------

from n500.jobs import run_nightly
from n500.jobs.run_nightly import RECOMPUTE, is_idle


class TestIsIdle:
    def test_no_new_rows_and_no_fundamentals_is_idle(self):
        assert is_idle(796_814, 796_814, {"prices": "ok"})

    def test_new_rows_are_not_idle(self):
        assert not is_idle(796_814, 797_550, {"prices": "ok"})

    def test_a_weekend_backfill_is_not_idle(self):
        # No new session, but a company joined the index and got its history.
        assert not is_idle(796_814, 799_200, {"prices": "ok"})

    def test_a_sunday_fundamentals_refresh_is_new_data(self):
        assert not is_idle(796_814, 796_814, {"fundamentals": "ok"})

    def test_a_failed_fundamentals_scrape_brought_nothing(self):
        assert is_idle(796_814, 796_814, {"fundamentals": "tolerated"})

    def test_an_unreadable_count_means_run_everything(self):
        assert not is_idle(None, 796_814, {})
        assert not is_idle(796_814, None, {})


def test_verify_and_the_loaders_always_run():
    steps = {s.name: s for s in plan(10, dry_run=True, skip_fundamentals=False)}
    for name in ("universe", "prices", "index", "fundamentals", "verify"):
        assert not steps[name].needs_new_data, name
    assert {n for n, s in steps.items() if s.needs_new_data} == set(RECOMPUTE)


class FakeDb:
    def __init__(self, counts):
        self.counts = list(counts)

    def count(self, table):
        return self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]


def run_with(monkeypatch, counts, argv=()):
    ran = []
    monkeypatch.setattr(run_nightly, "Db", lambda **kw: FakeDb(counts))
    monkeypatch.setattr(run_nightly, "run_step", lambda step: (ran.append(step.name), ("ok", None))[1])
    monkeypatch.setattr(run_nightly, "_record", lambda *a, **k: None)
    code = run_nightly.main(["--skip-fundamentals", *argv])
    return code, ran


def test_an_idle_night_runs_the_loaders_and_verify_only_and_succeeds(monkeypatch):
    code, ran = run_with(monkeypatch, [796_814, 796_814])
    assert code == 0, "an idle night is not a failure; it must not notify"
    assert ran == ["universe", "prices", "index", "verify"]


def test_a_night_with_new_prices_runs_everything(monkeypatch):
    code, ran = run_with(monkeypatch, [796_814, 797_550])
    assert code == 0
    assert set(RECOMPUTE) <= set(ran)


def test_force_recomputes_an_idle_night(monkeypatch):
    code, ran = run_with(monkeypatch, [796_814, 796_814], argv=["--force"])
    assert set(RECOMPUTE) <= set(ran)


# --- a fetch that failed is not a night with no prices -----------------------


def test_a_failed_fetch_is_incomplete_not_idle(monkeypatch):
    # 2 November 2026: no network on a background wake, no new rows, exit 0.
    from n500.jobs import load_prices
    monkeypatch.setattr(load_prices, "_failed_days", {"2026-11-02"})
    code, ran = run_with(monkeypatch, [796_814, 796_814])
    assert code == run_nightly.EXIT_INCOMPLETE
    assert "verify" in ran


def test_a_real_failure_outranks_an_incomplete_run(monkeypatch):
    from n500.jobs import load_prices
    monkeypatch.setattr(load_prices, "_failed_days", {"2026-11-02"})
    monkeypatch.setattr(run_nightly, "Db", lambda **kw: FakeDb([1, 2]))
    monkeypatch.setattr(run_nightly, "run_step", lambda step: ("failed", "boom") if step.name == "verify" else ("ok", None))
    monkeypatch.setattr(run_nightly, "_record", lambda *a, **k: None)
    assert run_nightly.main(["--skip-fundamentals"]) == 1


def test_a_weekday_with_no_file_yet_is_pending_not_done(monkeypatch):
    # A run at 19:15 that found no file would have finished the day, and a late
    # publish would have waited until tomorrow.
    from n500.jobs import load_prices
    monkeypatch.setattr(load_prices, "_today_pending", True)
    code, ran = run_with(monkeypatch, [796_814, 796_814])
    assert code == run_nightly.EXIT_PENDING


def test_a_failed_fetch_outranks_pending(monkeypatch):
    from n500.jobs import load_prices
    monkeypatch.setattr(load_prices, "_today_pending", True)
    monkeypatch.setattr(load_prices, "_failed_days", {"2026-11-23"})
    code, _ = run_with(monkeypatch, [796_814, 796_814])
    assert code == run_nightly.EXIT_INCOMPLETE


def test_only_todays_weekday_miss_is_pending(monkeypatch, tmp_path):
    """A 404 for a past day, or for a Saturday, is an answer, not a wait."""
    from datetime import date, timedelta
    from n500.jobs import load_prices as lp
    from n500.sources.bhavcopy import BhavcopyUnavailable

    def collect_with_miss_on(miss):
        def fetch(client, day):
            if day == miss:
                raise BhavcopyUnavailable("404")
            return {}
        monkeypatch.setattr(lp.bhavcopy, "fetch", fetch)
        monkeypatch.setattr(lp.bhavcopy, "_cache_path", lambda d: tmp_path / "x")
        monkeypatch.setattr(lp.time, "sleep", lambda s: None)
        monkeypatch.setattr(lp, "_today_pending", False)
        class Log:
            def error(self, *a): pass
        lp.collect(None, 3, set(), Log(), pause=0)
        return lp.today_pending()

    today = date.today()
    assert collect_with_miss_on(today) == (today.weekday() < 5)
    assert collect_with_miss_on(today - timedelta(days=1)) is False
