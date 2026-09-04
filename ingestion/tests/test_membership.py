"""Point-in-time index membership.

The rule under test is one line — take the most recent snapshot at or before
the date — and getting it wrong in either direction is the difference between
a backtest that means something and one that has read the answer.
"""

from __future__ import annotations

from datetime import date

import pytest

from n500.backtest.pointintime import Membership
from n500.sources import index_history


def rows(*pairs) -> list[dict]:
    out = []
    for when, symbols in pairs:
        for symbol in symbols:
            out.append({"symbol": symbol, "week_start": when, "index_name": "NIFTY500"})
    return out


class TestMembersAt:
    def frame(self) -> Membership:
        return Membership(
            rows(
                ("2019-02-01", ["OLD", "BOTH"]),
                ("2024-02-07", ["BOTH", "NEW"]),
                ("2026-08-27", ["BOTH", "NEW", "NEWEST"]),
            )
        )

    def test_a_date_between_snapshots_uses_the_earlier_one(self):
        # The whole point. A 2023 rebalance must not see the 2024 list, which
        # already knows which companies grew into the index.
        assert self.frame().members_at(date(2023, 6, 30)) == {"OLD", "BOTH"}

    def test_a_company_that_left_is_still_a_member_before_it_left(self):
        assert "OLD" in self.frame().members_at(date(2020, 1, 1))
        assert "OLD" not in self.frame().members_at(date(2025, 1, 1))

    def test_a_company_that_joined_later_is_not_a_member_earlier(self):
        assert "NEWEST" not in self.frame().members_at(date(2025, 1, 1))
        assert "NEWEST" in self.frame().members_at(date(2026, 9, 1))

    def test_the_snapshot_date_itself_counts_as_at_or_before(self):
        assert self.frame().members_at(date(2024, 2, 7)) == {"BOTH", "NEW"}

    def test_a_date_after_every_snapshot_uses_the_last_one(self):
        assert self.frame().members_at(date(2030, 1, 1)) == {"BOTH", "NEW", "NEWEST"}

    def test_a_date_before_every_snapshot_falls_back_to_the_earliest(self):
        # Stale, and the alternative is scoring nothing at all. It carries no
        # information about the test period either way, which is what matters.
        assert self.frame().members_at(date(2015, 1, 1)) == {"OLD", "BOTH"}

    def test_dates_arrive_as_strings_or_dates_alike(self):
        as_dates = Membership(
            [{"symbol": "A", "week_start": date(2024, 1, 1), "index_name": "NIFTY500"}]
        )
        assert as_dates.members_at(date(2025, 1, 1)) == {"A"}


class TestNoHistory:
    def test_an_empty_membership_passes_everything_through(self):
        empty = Membership([])
        assert not empty.has_history
        assert empty.members_at(date(2024, 1, 1)) is None

    def test_history_is_reported_when_present(self):
        assert Membership(rows(("2024-01-01", ["A"]))).has_history


class TestSnapshotParsing:
    HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"

    def body(self, n: int = 500) -> str:
        return "".join(
            f"Company {i} Ltd.,Capital Goods,SYM{i},EQ,INE{i:09d}\n" for i in range(n)
        )

    def test_a_well_formed_list_parses(self):
        out = index_history.parse(self.HEADER + self.body())
        assert len(out) == 500
        assert out[0].symbol == "SYM0"
        assert out[0].isin == "INE000000000"

    def test_a_truncated_list_is_rejected(self):
        with pytest.raises(index_history.MembershipError, match="expected ~500"):
            index_history.parse(self.HEADER + self.body(12))

    def test_a_changed_header_is_rejected_loudly(self):
        with pytest.raises(index_history.MembershipError, match="unexpected header"):
            index_history.parse("Name,Ticker\nFoo,BAR\n")

    def test_the_timestamp_becomes_the_snapshot_date(self):
        assert index_history.snapshot_date("20240207233933") == date(2024, 2, 7)
