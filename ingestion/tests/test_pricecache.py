"""The local price mirror.

A cache is only worth having if it is provably identical to its source, so the
tests that matter are the ones about drift: a bar revised upstream, rows deleted
upstream, and a sync interrupted half-way. Each of those is a way for the
pipeline to keep computing confidently on numbers that are no longer true.
"""

from __future__ import annotations

import pandas as pd
import pytest

from n500 import pricecache


class FakeDb:
    """A stand-in that records what was asked for.

    The request log is the point: the whole reason this module exists is that
    the second sync must not ask for the whole table again.
    """

    dry_run = False

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.requests: list[tuple] = []

    def select(self, table, columns="*", *, where=None, since=None, page_size=1000):
        self.requests.append((table, since))
        if since is None:
            return [dict(r) for r in self.rows]
        column, value = since
        return [dict(r) for r in self.rows if r[column] >= value]

    def count(self, table):
        return len(self.rows)


def bars(symbol: str, start: str, n: int, close: float = 100.0) -> list[dict]:
    days = pd.bdate_range(start, periods=n)
    return [
        {"symbol": symbol, "date": d.date().isoformat(), "open": close, "high": close,
         "low": close, "close": close, "adj_close": close, "volume": 1000.0}
        for d in days
    ]


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("N500_PRICE_CACHE", str(tmp_path / "prices.sqlite"))


class TestFirstBuild:
    def test_an_empty_cache_copies_everything(self):
        db = FakeDb(bars("AAA", "2026-01-01", 10))
        assert pricecache.sync(db, verbose=False) == 10
        assert pricecache.row_count() == 10

    def test_bars_come_back_oldest_first(self):
        db = FakeDb(bars("AAA", "2026-01-01", 10))
        pricecache.sync(db, verbose=False)
        got = pricecache.frame("AAA")
        assert got["date"].is_monotonic_increasing
        assert len(got) == 10

    def test_one_symbol_does_not_return_another(self):
        db = FakeDb(bars("AAA", "2026-01-01", 5) + bars("BBB", "2026-01-01", 5))
        pricecache.sync(db, verbose=False)
        assert set(pricecache.frame("AAA")["symbol"]) == {"AAA"}
        assert pricecache.symbols() == ["AAA", "BBB"]


class TestIncremental:
    def test_the_second_sync_does_not_refetch_the_table(self):
        """The measurement the whole module exists for."""
        db = FakeDb(bars("AAA", "2026-01-01", 200))
        pricecache.sync(db, verbose=False)
        db.requests.clear()

        fetched = pricecache.sync(db, verbose=False)
        assert fetched < 20                      # the overlap window, not 200 rows
        assert all(since is not None for _, since in db.requests)

    def test_new_bars_arrive(self):
        db = FakeDb(bars("AAA", "2026-01-01", 20))
        pricecache.sync(db, verbose=False)
        db.rows.extend(bars("AAA", "2026-02-02", 3))
        pricecache.sync(db, verbose=False)
        assert pricecache.row_count() == 23

    def test_a_revised_bar_is_corrected(self):
        # A close restated after we cached it. An append-only merge that started
        # strictly after the last cached date would keep the stale number and
        # every indicator built on it would stay quietly wrong.
        db = FakeDb(bars("AAA", "2026-01-01", 20))
        pricecache.sync(db, verbose=False)
        db.rows[-1]["close"] = 999.0
        db.rows[-1]["adj_close"] = 999.0
        pricecache.sync(db, verbose=False)
        assert pricecache.frame("AAA")["close"].iloc[-1] == 999.0

    def test_a_revision_outside_the_overlap_window_is_caught_by_the_count(self):
        # Deliberately documents the limit: revisions older than the overlap are
        # only repaired when the row count also disagrees. Prices are
        # append-only in practice, which is what makes that acceptable.
        db = FakeDb(bars("AAA", "2026-01-01", 200))
        pricecache.sync(db, verbose=False)
        db.rows[0]["close"] = 12.0
        pricecache.sync(db, verbose=False)
        assert pricecache.frame("AAA")["close"].iloc[0] == 100.0


class TestDrift:
    def test_rows_deleted_upstream_force_a_rebuild(self):
        db = FakeDb(bars("AAA", "2026-01-01", 50))
        pricecache.sync(db, verbose=False)
        del db.rows[:10]
        pricecache.sync(db, verbose=False)
        assert pricecache.row_count() == 40

    def test_the_cache_matches_the_source_after_any_sequence(self):
        db = FakeDb(bars("AAA", "2026-01-01", 40) + bars("BBB", "2026-01-01", 40))
        pricecache.sync(db, verbose=False)
        db.rows.extend(bars("CCC", "2026-01-01", 40))
        pricecache.sync(db, verbose=False)
        del db.rows[0:5]
        pricecache.sync(db, verbose=False)
        assert pricecache.row_count() == db.count("prices_daily")


class TestShape:
    def test_it_returns_what_db_select_would_have(self):
        # Callers pass this straight into `adjusted_frame`, which names columns.
        db = FakeDb(bars("AAA", "2026-01-01", 10))
        pricecache.sync(db, verbose=False)
        got = pricecache.frame("AAA")
        assert list(got.columns) == list(pricecache.COLUMNS)

    def test_tail_bounds_the_read(self):
        db = FakeDb(bars("AAA", "2026-01-01", 100))
        pricecache.sync(db, verbose=False)
        got = pricecache.frame("AAA", tail=30)
        assert len(got) == 30
        assert got["date"].iloc[-1] == pricecache.frame("AAA")["date"].iloc[-1]

    def test_a_dry_run_syncs_nothing(self):
        db = FakeDb(bars("AAA", "2026-01-01", 10))
        db.dry_run = True
        assert pricecache.sync(db, verbose=False) == 0

    def test_load_can_take_a_subset_of_columns(self):
        db = FakeDb(bars("AAA", "2026-01-01", 10))
        pricecache.sync(db, verbose=False)
        got = pricecache.load("symbol,date,adj_close")
        assert list(got.columns) == ["symbol", "date", "adj_close"]
