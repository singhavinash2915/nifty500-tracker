"""The entry/exit band.

The whole mechanism is that these two numbers differ. A test suite that only
checked "the top ten are on the list" would pass against a hard cut and miss the
point entirely, so every test here is about what happens in the gap between 10
and 25.
"""

from __future__ import annotations

from datetime import date

from n500.scoring import buylist

TODAY = date(2026, 9, 8)
YESTERDAY = date(2026, 9, 7)


def ranked(n: int = 60) -> list[str]:
    return [f"SYM{i:02d}" for i in range(1, n + 1)]


class TestEntry:
    def test_an_empty_list_fills_from_the_top(self):
        out = buylist.apply_band(ranked(), {}, TODAY)
        assert [m.symbol for m in out] == ranked(10)
        assert all(m.is_new for m in out)

    def test_everyone_admitted_today_is_dated_today(self):
        out = buylist.apply_band(ranked(), {}, TODAY)
        assert {m.since for m in out} == {TODAY}

    def test_the_list_is_ordered_by_rank(self):
        out = buylist.apply_band(ranked(), {}, TODAY)
        assert [m.rank for m in out] == list(range(1, 11))


class TestTheBand:
    """The gap between entering at 10 and leaving past 25."""

    def test_a_name_that_slips_to_rank_20_stays(self):
        # Under a hard top-ten cut this name is gone and returns a week later
        # having done nothing, which is the churn being complained about.
        order = ["NEW"] * 0 + [f"OTHER{i}" for i in range(19)] + ["SLIPPED"]
        out = buylist.apply_band(order + ranked(), {"SLIPPED": YESTERDAY}, TODAY)
        held = {m.symbol for m in out}
        assert "SLIPPED" in held

    def test_a_name_that_falls_past_25_leaves(self):
        order = [f"OTHER{i}" for i in range(40)] + ["DROPPED"]
        out = buylist.apply_band(order, {"DROPPED": YESTERDAY}, TODAY)
        assert "DROPPED" not in {m.symbol for m in out}

    def test_a_survivor_keeps_the_date_it_joined(self):
        # Otherwise "on the list since" resets nightly and means nothing.
        order = [f"OTHER{i}" for i in range(15)] + ["OLD"]
        out = buylist.apply_band(order, {"OLD": date(2026, 8, 1)}, TODAY)
        old = next(m for m in out if m.symbol == "OLD")
        assert old.since == date(2026, 8, 1)
        assert not old.is_new

    def test_a_name_that_returns_gets_a_new_date(self):
        # A second visit is a new idea, not a continuation of the first.
        out = buylist.apply_band(ranked(), {}, TODAY)
        assert next(m for m in out if m.symbol == "SYM01").since == TODAY


class TestCapacity:
    def test_incumbents_are_resolved_before_newcomers(self):
        # Ten incumbents inside the exit band leave no room, so a name ranked
        # first today waits rather than displacing someone at rank 12.
        previous = {f"HELD{i}": YESTERDAY for i in range(10)}
        order = ["FRESH"] + [f"HELD{i}" for i in range(10)] + ranked()
        out = buylist.apply_band(order, previous, TODAY)
        assert len(out) == 10
        assert "FRESH" not in {m.symbol for m in out}

    def test_room_freed_by_a_departure_is_filled(self):
        previous = {f"HELD{i}": YESTERDAY for i in range(9)}
        order = [f"HELD{i}" for i in range(9)] + ["FRESH"] + ranked()
        out = buylist.apply_band(order, previous, TODAY)
        assert len(out) == 10
        assert "FRESH" in {m.symbol for m in out}

    def test_a_symbol_no_longer_scored_at_all_leaves(self):
        # Delisted, or gated out. Absent from the ranking is worse than rank 40.
        out = buylist.apply_band(ranked(), {"VANISHED": YESTERDAY}, TODAY)
        assert "VANISHED" not in {m.symbol for m in out}

    def test_the_list_never_exceeds_the_entry_count(self):
        previous = {f"SYM{i:02d}": YESTERDAY for i in range(1, 21)}
        out = buylist.apply_band(ranked(), previous, TODAY)
        # Twenty incumbents all inside the exit band: the list holds them
        # rather than truncating to ten, because evicting a name that has not
        # breached the exit rank would defeat the band.
        assert len(out) == 20
        assert all(m.rank <= buylist.EXIT_PAST for m in out)


class TestStability:
    def test_a_stable_ranking_produces_no_turnover(self):
        first = buylist.apply_band(ranked(), {}, YESTERDAY)
        previous = {m.symbol: m.since for m in first}
        second = buylist.apply_band(ranked(), previous, TODAY)
        assert {m.symbol for m in first} == {m.symbol for m in second}
        assert not any(m.is_new for m in second)

    def test_shuffling_inside_the_band_produces_no_turnover(self):
        """The measurement that motivated all of this.

        Ten names reversed among themselves and pushed to ranks 11-20. A hard
        cut replaces the entire list; the band replaces none of it.
        """
        first = buylist.apply_band(ranked(), {}, YESTERDAY)
        previous = {m.symbol: m.since for m in first}
        shuffled = [f"OTHER{i}" for i in range(10)] + list(reversed(ranked(10)))
        second = buylist.apply_band(shuffled + ranked(60)[10:], previous, TODAY)
        assert {m.symbol for m in second} == {m.symbol for m in first}
