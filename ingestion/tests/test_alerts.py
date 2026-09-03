"""Alert rules. Mostly about staying quiet."""

import math

import pytest

from n500.alerts import rules
from n500.alerts.rules import Alert, Severity


def position(**kw):
    base = {"id": 1, "symbol": "TEST", "entry_price": 100.0, "quantity": 10.0,
            "stop_price": 90.0, "target_price": 150.0}
    base.update(kw)
    return base


def score(**kw):
    base = {"symbol": "TEST", "decile": 10, "flags": [], "setup_status": "none"}
    base.update(kw)
    return base


# --- missing values -------------------------------------------------------


@pytest.mark.parametrize("absent", [None, float("nan")])
def test_a_missing_target_never_fires(absent):
    """Rows come from pandas, so an absent target is NaN — and every comparison
    against NaN is False, which let this rule sail past its own guard and
    announce that ITC had 'reached 266.30 against a nan target'."""
    assert rules.target_reached(position(target_price=absent), 266.3) is None


@pytest.mark.parametrize("absent", [None, float("nan")])
def test_a_missing_stop_never_fires(absent):
    assert rules.stop_breached(position(stop_price=absent), 10.0) is None


def test_number_coercion_treats_nan_as_absent():
    assert rules._number(float("nan")) is None
    assert rules._number(None) is None
    assert rules._number("not a number") is None
    assert rules._number("12.5") == 12.5
    assert rules._number(3) == 3.0


# --- position rules -------------------------------------------------------


def test_a_breached_stop_is_critical():
    alert = rules.stop_breached(position(), 88.0)
    assert alert.severity is Severity.CRITICAL
    assert "88.00" in alert.message and "90.00" in alert.message


def test_a_stop_exactly_touched_counts_as_breached():
    assert rules.stop_breached(position(), 90.0) is not None


def test_a_price_above_the_stop_is_silent():
    assert rules.stop_breached(position(), 90.01) is None


def test_a_reached_target_reports_the_gain_from_entry():
    alert = rules.target_reached(position(), 155.0)
    assert alert.severity is Severity.ACTION
    assert "55%" in alert.message


def test_a_new_red_flag_on_something_held_is_critical():
    """The most important alert that is not about price: the reason for holding
    has stopped being true."""
    alert = rules.thesis_broken(
        position(), score(flags=[{"name": "cash_conversion", "verdict": "fail"}])
    )
    assert alert.severity is Severity.CRITICAL
    assert "cash conversion" in alert.message


def test_an_unknown_gate_is_not_a_broken_thesis():
    """Promoter pledge is permanently unevaluable; it must not fire nightly."""
    assert rules.thesis_broken(
        position(), score(flags=[{"name": "promoter_pledge", "verdict": "unknown"}])
    ) is None


def test_score_decay_fires_only_below_the_floor():
    assert rules.score_decayed(position(), score(decile=4)) is not None
    assert rules.score_decayed(position(), score(decile=8)) is None
    assert rules.score_decayed(position(), score(decile=None)) is None


# --- transitions, not states ----------------------------------------------


def test_a_setup_that_was_already_triggered_does_not_fire_again():
    """The failure mode is not missing a signal, it is firing so often that the
    one that mattered scrolls past."""
    today = score(setup_status="triggered", confirmations=["rsi_divergence"])
    assert rules.setup_triggered(today, score(setup_status="watching")) is not None
    assert rules.setup_triggered(today, score(setup_status="triggered")) is None


def test_entering_the_top_decile_fires_once():
    assert rules.entered_top_decile(score(decile=10), score(decile=8)) is not None
    assert rules.entered_top_decile(score(decile=10), score(decile=10)) is None
    assert rules.entered_top_decile(score(decile=9), score(decile=8)) is None


def test_a_new_red_flag_fires_only_for_the_flag_that_is_new():
    today = score(flags=[{"name": "loss_making", "verdict": "fail"},
                         {"name": "cash_conversion", "verdict": "fail"}])
    yesterday = score(flags=[{"name": "loss_making", "verdict": "fail"}])
    alert = rules.new_red_flag(today, yesterday)
    assert alert is not None
    assert "cash conversion" in alert.message
    assert "loss making" not in alert.message
    assert rules.new_red_flag(today, today) is None


def test_nothing_fires_without_a_previous_day():
    """On the first run there is no transition to detect, and announcing every
    stock's current state would be the exact noise this design avoids."""
    assert rules.for_screen(score(decile=10), None) == []


# --- ranking and dedupe ---------------------------------------------------


def test_alerts_are_ordered_by_how_much_they_should_interrupt():
    ordered = rules.rank([
        Alert("B", "info_rule", Severity.INFO, ""),
        Alert("A", "stop", Severity.CRITICAL, ""),
        Alert("C", "act", Severity.ACTION, ""),
    ])
    assert [a.symbol for a in ordered] == ["A", "C", "B"]


def test_the_dedupe_key_identifies_the_event_not_the_night():
    a = Alert("X", "stop_breached", Severity.CRITICAL, "", {"dedupe": 7})
    b = Alert("X", "stop_breached", Severity.CRITICAL, "different wording", {"dedupe": 7})
    assert a.dedupe_key == b.dedupe_key


def test_a_different_underlying_event_is_not_suppressed():
    a = Alert("X", "new_red_flag", Severity.INFO, "", {"dedupe": "loss_making"})
    b = Alert("X", "new_red_flag", Severity.INFO, "", {"dedupe": "cash_conversion"})
    assert rules.suppress_seen([a, b], {a.dedupe_key}) == [b]


def test_position_rules_need_a_score_row():
    assert rules.for_position(position(), None, 100.0) == []
