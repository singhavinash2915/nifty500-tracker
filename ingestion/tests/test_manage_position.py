"""The one rule about stops: they move up or not at all.

Worth a test of its own because the rule exists to defend against a person
rather than a bug. Lowering a stop always happens at the same moment — price
approaching the level, an excellent reason suddenly available for giving it more
room — and by then the argument for it is very persuasive.
"""

from __future__ import annotations

import pytest

from n500.jobs.manage_position import decide_stop_change


class TestDecideStopChange:
    def test_raising_a_stop_is_allowed(self):
        apply, why = decide_stop_change(4300.0, 5214.61)
        assert apply and why is None

    def test_lowering_a_stop_is_refused(self):
        apply, why = decide_stop_change(5214.61, 4300.0)
        assert not apply
        assert "moves up or not at all" in why

    def test_force_allows_lowering_and_is_the_only_way(self):
        assert decide_stop_change(5214.61, 4300.0, force=True)[0]

    def test_a_position_with_no_stop_accepts_any_stop(self):
        # Nothing to lower, and a defined stop always beats no stop.
        apply, why = decide_stop_change(None, 100.0)
        assert apply and why is None

    def test_an_unchanged_stop_is_not_rewritten(self):
        apply, why = decide_stop_change(3628.36, 3628.36)
        assert not apply and why == "already there"

    def test_a_hair_of_floating_point_drift_counts_as_unchanged(self):
        apply, _ = decide_stop_change(3628.36, 3628.36 + 1e-12)
        assert not apply

    def test_a_missing_suggestion_is_reported_rather_than_applied(self):
        apply, why = decide_stop_change(4300.0, None)
        assert not apply
        assert "no suggested stop" in why

    def test_the_refusal_names_both_numbers(self):
        # The message has to be readable in a run over twenty positions.
        _, why = decide_stop_change(500.0, 400.0)
        assert "500" in why and "400" in why

    @pytest.mark.parametrize("current,new,expected", [
        (100.0, 101.0, True),
        (100.0, 99.99, False),
        (0.0, 1.0, True),
    ])
    def test_the_boundary_is_strict(self, current, new, expected):
        assert decide_stop_change(current, new)[0] is expected
