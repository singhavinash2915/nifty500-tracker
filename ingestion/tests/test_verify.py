"""The invariants that run after a nightly build.

Each test here reconstructs a fault that actually reached production and shows
the check catching it. That is the only way to know an invariant is worth its
line count: an assertion nobody has ever seen fail is a guess about what could
go wrong, and the three faults below were not on anybody's list of guesses.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from n500.jobs import verify


class FakeDb:
    """Canned rows, keyed by table. Only `select` is used by the checks."""

    dry_run = False

    def __init__(self, **tables):
        self._tables = tables

    def select(self, table, columns="*", *, where=None, since=None, page_size=1000):
        return list(self._tables.get(table, []))


def today() -> str:
    return date.today().isoformat()


def stocks(active: list[str], inactive: list[str] = ()) -> list[dict]:
    return (
        [{"symbol": s, "is_active": True} for s in active]
        + [{"symbol": s, "is_active": False} for s in inactive]
    )


def scores(symbols: list[str], *, on: str | None = None, **overrides) -> list[dict]:
    return [
        {
            "symbol": s, "date": on or today(),
            "blended": 50.0, "conviction": 50.0, "decile": 5, **overrides,
        }
        for s in symbols
    ]


class TestDelistedNamesInTheScreener:
    """The worst fault of the evening, and the one nothing would have caught.

    312 companies that left the index are kept so the backtest can see the ones
    that failed rather than only the survivors. They are marked inactive, and
    for one run every job downstream of prices scored them anyway — 210 delisted
    names in the screener and the buy list, with position sizes attached, some
    of them no longer trading.
    """

    def test_a_delisted_name_in_the_screener_fails_the_check(self):
        db = FakeDb(
            stocks=stocks(["ABB", "ACC"], inactive=["ALBK", "ANDHRABANK"]),
            scores_daily=scores(["ABB", "ACC", "ALBK"]),
        )
        checks = verify.check_universe(db)
        assert not checks[0].ok
        assert "ALBK" in checks[0].detail

    def test_a_clean_screener_passes(self):
        db = FakeDb(
            stocks=stocks(["ABB", "ACC"], inactive=["ALBK"]),
            scores_daily=scores(["ABB", "ACC"]),
        )
        assert verify.check_universe(db)[0].ok

    def test_an_inactive_name_may_exist_as_long_as_it_is_not_scored(self):
        # Keeping them is the point; scoring them is the bug.
        db = FakeDb(
            stocks=stocks(["ABB"], inactive=[f"GONE{i}" for i in range(300)]),
            scores_daily=scores(["ABB"]),
        )
        assert verify.check_universe(db)[0].ok


class TestScores:
    def named(self, checks, name):
        return next(c for c in checks if c.name == name)

    def test_a_truncated_universe_fails(self):
        # The shape of a silent paging bug: fewer rows than there are stocks,
        # every one of them individually plausible.
        db = FakeDb(scores_daily=scores([f"SYM{i}" for i in range(300)]))
        assert not self.named(verify.check_scores(db), "scored count is sane").ok

    def test_a_full_universe_passes(self):
        db = FakeDb(scores_daily=scores([f"SYM{i}" for i in range(494)]))
        assert self.named(verify.check_scores(db), "scored count is sane").ok

    def test_a_duplicated_symbol_fails(self):
        # What keyset paging exists to prevent: a row returned on two pages.
        db = FakeDb(scores_daily=scores([f"SYM{i}" for i in range(470)] + ["SYM1"]))
        assert not self.named(verify.check_scores(db), "no symbol scored twice").ok

    def test_a_score_outside_the_scale_fails(self):
        db = FakeDb(
            scores_daily=scores([f"SYM{i}" for i in range(470)], conviction=140.0)
        )
        assert not self.named(verify.check_scores(db), "conviction is inside 0-100").ok

    def test_stale_scores_fail(self):
        old = (date.today() - timedelta(days=20)).isoformat()
        db = FakeDb(scores_daily=scores([f"SYM{i}" for i in range(470)], on=old))
        assert not self.named(verify.check_scores(db), "scores are fresh").ok

    def test_a_missing_conviction_column_is_tolerated_but_flagged(self):
        db = FakeDb(
            scores_daily=scores([f"SYM{i}" for i in range(470)], conviction=None)
        )
        checks = verify.check_scores(db)
        assert not self.named(checks, "conviction covers most of the universe").ok


class TestPlans:
    def named(self, checks, name):
        return next(c for c in checks if c.name == name)

    def frame(self, stop, target=None, close=100.0, price_date=None):
        return FakeDb(
            ts_setups=[{"symbol": "ABB", "date": today(),
                        "plan_stop": stop, "plan_target": target}],
            prices_daily=[{"symbol": "ABB", "date": price_date or today(),
                           "adj_close": close}],
        )

    def test_a_stop_above_the_price_fails(self):
        # A stop above the price is not a stop, it is an instruction to sell now.
        checks = verify.check_plans(self.frame(stop=105.0))
        assert not self.named(checks, "every stop is below its price").ok

    def test_a_sensible_stop_passes(self):
        checks = verify.check_plans(self.frame(stop=94.0))
        assert self.named(checks, "every stop is below its price").ok

    def test_a_target_below_the_price_fails(self):
        # The bug the plan tests caught: a support zone handed over as a target.
        checks = verify.check_plans(self.frame(stop=94.0, target=90.0))
        assert not self.named(checks, "every target is above its price").ok

    def test_a_plan_is_judged_against_its_own_date_not_the_latest_price(self):
        """Where this check went wrong on 8 September.

        The zones job had failed the night before, so plans were dated the 7th
        while prices had reached the 8th. A target set above yesterday's close
        is frequently below today's — which is a market moving, not a corrupted
        table — and the check reported 33 failures and drove a banner saying
        "the output is wrong, not merely stale". Telling those two apart is the
        entire job of this file.
        """
        db = FakeDb(
            ts_setups=[{"symbol": "ABB", "date": "2026-09-07",
                        "plan_stop": 94.0, "plan_target": 110.0}],
            prices_daily=[
                {"symbol": "ABB", "date": "2026-09-07", "adj_close": 100.0},
                # A 15% day. The target is now below the price and the stop is
                # still below it, and neither fact says anything is broken.
                {"symbol": "ABB", "date": "2026-09-08", "adj_close": 115.0},
            ],
        )
        checks = verify.check_plans(db)
        assert self.named(checks, "every target is above its price").ok
        assert self.named(checks, "every stop is below its price").ok

    def test_a_stale_plan_is_reported_as_staleness(self):
        # Reported, but not fatal: yesterday's plan is old, not wrong, and the
        # banner should say look rather than stop.
        db = FakeDb(
            ts_setups=[{"symbol": "ABB", "date": "2026-09-07",
                        "plan_stop": 94.0, "plan_target": 110.0}],
            prices_daily=[
                {"symbol": "ABB", "date": "2026-09-07", "adj_close": 100.0},
                {"symbol": "ABB", "date": "2026-09-08", "adj_close": 101.0},
            ],
        )
        stale = self.named(verify.check_plans(db), "plans are as fresh as prices")
        assert not stale.ok
        assert not stale.fatal, "staleness must not read as corruption"


class TestPositions:
    def test_a_holding_in_no_known_symbol_fails(self):
        db = FakeDb(
            positions=[{"symbol": "MADEUP", "exit_date": None, "stop_price": 10}],
            stocks=stocks(["ABB"]),
        )
        checks = verify.check_positions(db)
        assert not checks[0].ok

    def test_a_holding_without_a_stop_warns_rather_than_fails(self):
        # Worth seeing, but it does not make the screener wrong.
        db = FakeDb(
            positions=[{"symbol": "ABB", "exit_date": None, "stop_price": None}],
            stocks=stocks(["ABB"]),
        )
        stopless = next(c for c in verify.check_positions(db) if "stop" in c.name)
        assert not stopless.ok and not stopless.fatal


class TestPrices:
    def test_a_zero_price_fails(self):
        db = FakeDb(prices_daily=[
            {"symbol": "ABB", "date": today(), "adj_close": 0.0},
        ])
        checks = verify.check_prices(db)
        assert not next(c for c in checks if "non-positive" in c.name).ok

    def test_stale_prices_fail(self):
        old = (date.today() - timedelta(days=12)).isoformat()
        db = FakeDb(prices_daily=[{"symbol": "ABB", "date": old, "adj_close": 100.0}])
        assert not next(c for c in verify.check_prices(db) if "fresh" in c.name).ok
