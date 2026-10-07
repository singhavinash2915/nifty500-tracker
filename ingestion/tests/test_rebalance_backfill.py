"""New index members get their history the night they join.

The September 2026 rebalance added 28 companies. The nightly loads ten days of
prices for every symbol it tracks, so each arrived with ten days and stayed
unscored, the scorer wanting 220, until the history was loaded by hand.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from n500 import pricecache
from n500.jobs.load_prices import needs_backfill

TODAY = date(2026, 10, 7)
WINDOW = 10


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def pick(stats, active=None):
    return needs_backfill(stats, set(stats if active is None else active), today=TODAY,
                          window_days=WINDOW, min_bars=220)


class TestWhoGetsBackfilled:
    def test_a_member_whose_only_history_is_the_nightly_window(self):
        # Joined the index a few nights ago: every bar it has came from the
        # nightly top-up, and nothing older was ever asked for.
        assert pick({"JOINED": (6, ago(9))}) == {"JOINED"}

    def test_a_member_with_no_bars_at_all(self):
        # The three REITs: in the universe, never priced.
        assert pick({}, active={"EMBASSY"}) == {"EMBASSY"}

    def test_an_established_member_is_left_alone(self):
        assert pick({"RELIANCE": (1143, ago(1700))}) == set()

    def test_a_short_history_that_was_already_backfilled_is_not_refetched(self):
        # Listed eight months ago and backfilled: short because it is new.
        # Its first bar is long outside the window, so it is not re-scanned
        # every night until it reaches 220 bars.
        assert pick({"MEESHO": (200, ago(290))}) == set()

    def test_a_fresh_listing_is_rescanned_only_while_its_first_bar_is_recent(self):
        assert pick({"IPO": (5, ago(7))}) == {"IPO"}
        assert pick({"IPO": (20, ago(30))}) == set()

    def test_a_company_that_left_the_index_is_not_backfilled(self):
        assert pick({"GONE": (6, ago(9))}, active=set()) == set()

    def test_enough_bars_is_enough_however_recent(self):
        assert pick({"FULL": (220, ago(9))}) == set()


class TestBarStats:
    @pytest.fixture(autouse=True)
    def cache_in_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("N500_PRICE_CACHE", str(tmp_path / "prices.sqlite"))

    def test_counts_and_first_dates_per_symbol(self):
        conn = pricecache._connect()
        conn.executemany(
            "INSERT INTO prices_daily (symbol, date, close) VALUES (?, ?, 1.0)",
            [("A", "2026-01-02"), ("A", "2026-01-05"), ("B", "2026-03-01")])
        conn.commit(); conn.close()
        assert pricecache.bar_stats() == {
            "A": (2, date(2026, 1, 2)), "B": (1, date(2026, 3, 1))}


def test_the_mirror_overlap_covers_the_nightly_window():
    """At 7 days, the 10-day rows of a newly joined company fell outside the
    overlap, the counts disagreed, and the whole mirror was rebuilt."""
    assert pricecache.OVERLAP_DAYS > WINDOW
