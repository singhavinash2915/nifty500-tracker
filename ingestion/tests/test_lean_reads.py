"""The nightly's smaller reads must give the same answers as the big ones."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from n500 import db as dbmod, pricecache, tablecache
from n500.jobs.compute_scores import _setup_window
from n500.scoring import conviction


@pytest.fixture
def dry(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DRYRUN_DIR", tmp_path)
    return dbmod.Db(force_dry_run=True), tmp_path


def write(path, table, rows):
    (path / f"{table}.json").write_text(json.dumps(rows))


class TestLatestPerSymbol:
    ROWS = [
        {"symbol": "A", "date": "2026-10-01", "v": 1},
        {"symbol": "A", "date": "2026-11-03", "v": 2},
        {"symbol": "B", "date": "2026-11-03", "v": 3},
        {"symbol": "HALTED", "date": "2026-10-20", "v": 4},
        {"symbol": "HALTED", "date": "2026-10-22", "v": 5},
        {"symbol": "ANCIENT", "date": "2026-01-01", "v": 6},
    ]

    def by_symbol(self, db, symbols, since="2026-10-10"):
        return {r["symbol"]: r["v"]
                for r in db.latest_per_symbol("t", symbols, since=since)}

    def test_matches_taking_the_last_row_of_the_window(self, dry):
        db, path = dry
        write(path, "t", self.ROWS)
        got = self.by_symbol(db, ["A", "B", "HALTED", "ANCIENT", "NEVER"])
        window = pd.DataFrame(self.ROWS)
        window = window[window["date"] >= "2026-10-10"]
        slow = window.sort_values("date").groupby("symbol").tail(1)
        assert got == dict(zip(slow["symbol"], slow["v"]))

    def test_a_suspended_stock_keeps_its_last_row(self, dry):
        db, path = dry
        write(path, "t", self.ROWS)
        assert self.by_symbol(db, ["HALTED"])["HALTED"] == 5

    def test_a_stale_table_returns_nothing_rather_than_old_rows(self, dry):
        db, path = dry
        write(path, "t", self.ROWS)
        assert self.by_symbol(db, [], since="2026-12-01") == {}


def setup_row(symbol, day, **kw):
    row = {"symbol": symbol, "date": day, "ts_score": 50.0, "setup_status": "none",
           "headroom": 1.0, "false_breakout": None}
    for name in conviction.EVENT_FEATURES[1:]:
        row[name] = False
    return {**row, **kw}


class TestSetupWindow:
    def test_carrying_events_gives_the_same_answer_as_the_whole_window(self, dry):
        db, path = dry
        rows = [
            setup_row("A", "2026-09-01", doji_at_resistance=True),   # fired long ago
            setup_row("A", "2026-10-01"),
            setup_row("A", "2026-11-03", headroom=2.5),
            setup_row("B", "2026-09-15", false_breakout={"level": 100}),
            setup_row("B", "2026-11-03", ts_score=70.0),
            setup_row("C", "2026-11-03", rejected_at_resistance=True),   # fires today
            setup_row("D", "2026-11-03"),
            setup_row("E", "2026-10-25", hanging_man_at_resistance=True),  # none today
        ]
        write(path, "ts_setups", rows)
        symbols = ["A", "B", "C", "D", "E"]

        def carried(frame):
            frame = frame.copy()
            frame["false_breakout"] = frame["false_breakout"].notna()
            return conviction.carry_events(frame).sort_index()

        lean = carried(_setup_window(db, symbols, since="2026-08-01"))
        whole = carried(pd.DataFrame(rows))
        columns = ["date", "ts_score", "setup_status", "headroom", *conviction.EVENT_FEATURES]
        pd.testing.assert_frame_equal(lean[columns], whole[columns], check_dtype=False)

    def test_a_flags_only_row_never_replaces_the_latest_row(self, dry):
        db, path = dry
        write(path, "ts_setups", [setup_row("C", "2026-11-03", doji_at_resistance=True, ts_score=61.0)])
        frame = _setup_window(db, ["C"], since="2026-08-01")
        assert len(frame) == 1 and frame.iloc[0]["ts_score"] == 61.0


class CountingDb:
    dry_run = False

    def __init__(self, rows, started="t1"):
        self.rows, self.started, self.reads, self.counts = rows, started, 0, 0

    def last_started(self, job):
        return self.started

    def count(self, table):
        self.counts += 1
        return len(self.rows)

    def select(self, table, columns="*", **kw):
        self.reads += 1
        return [dict(r) for r in self.rows]


class TestWeeklyTable:
    @pytest.fixture(autouse=True)
    def cache_in_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tablecache, "CACHE_DIR", tmp_path / "tables")

    def test_the_second_night_reads_nothing(self):
        db = CountingDb([{"a": 1}])
        assert tablecache.weekly_table(db, "t") == [{"a": 1}]
        assert tablecache.weekly_table(db, "t") == [{"a": 1}]
        assert db.reads == 1

    def test_a_new_fundamentals_run_invalidates_it(self):
        db = CountingDb([{"a": 1}])
        tablecache.weekly_table(db, "t")
        db.rows, db.started = [{"a": 2}], "t2"
        assert tablecache.weekly_table(db, "t") == [{"a": 2}]

    def test_a_changed_row_count_invalidates_it(self):
        # A restore writes without a load_fundamentals run.
        db = CountingDb([{"a": 1}])
        tablecache.weekly_table(db, "t")
        db.rows = [{"a": 1}, {"a": 2}]
        assert len(tablecache.weekly_table(db, "t")) == 2

    def test_a_damaged_file_is_only_a_read(self):
        db = CountingDb([{"a": 1}])
        tablecache.CACHE_DIR.mkdir(parents=True)
        (tablecache.CACHE_DIR / "t.json.gz").write_bytes(b"not gzip")
        assert tablecache.weekly_table(db, "t") == [{"a": 1}]


class TestEnsure:
    @pytest.fixture(autouse=True)
    def cache_in_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("N500_PRICE_CACHE", str(tmp_path / "p.sqlite"))
        monkeypatch.setattr(pricecache, "_level_at", None)

    ROW = {"symbol": "A", "date": "2026-11-03", "open": 1, "high": 1, "low": 1,
           "close": 1, "adj_close": 1, "volume": 1}

    def test_syncs_once_while_the_table_has_not_moved(self):
        db = CountingDb([self.ROW])
        pricecache.ensure(db)
        before = db.reads
        pricecache.ensure(db)
        pricecache.ensure(db)
        assert db.reads == before

    def test_syncs_again_when_rows_arrive(self):
        db = CountingDb([self.ROW])
        pricecache.ensure(db)
        db.rows = [self.ROW, {**self.ROW, "date": "2026-11-04"}]
        pricecache.ensure(db)
        assert pricecache.row_count() == 2
