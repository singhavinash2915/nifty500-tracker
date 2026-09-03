"""The nightly orchestrator and the serialisation guard."""

import math

import pytest

from n500 import serialise
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
