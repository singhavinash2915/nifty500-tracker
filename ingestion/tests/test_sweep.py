"""Weight sweep — mostly guards against fooling ourselves."""

import numpy as np
import pandas as pd
import pytest

from n500.backtest import sweep
from n500.backtest.sweep import Candidate


def panel(n_dates=8, n_symbols=60, seed=0, signal=0.0):
    """A synthetic panel where `signal` controls how much technical predicts."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_dates):
        for s in range(n_symbols):
            technical = rng.uniform(0, 100)
            quality = rng.uniform(0, 100)
            value = rng.uniform(0, 100)
            noise = rng.normal(0, 0.20)
            rows.append(
                {
                    "as_of": f"2025-{d + 1:02d}-28",
                    "symbol": f"S{s:03d}",
                    "sector": "X",
                    "quality_score": quality,
                    "value_score": value,
                    "technical": technical,
                    "setup": "momentum",
                    "forward_return": signal * (technical - 50) / 100 + noise,
                }
            )
    return pd.DataFrame(rows)


# --- grid -----------------------------------------------------------------


def test_grid_combinations_all_sum_to_one_hundred():
    for candidate in sweep.grid():
        assert candidate.quality + candidate.value + candidate.technical == pytest.approx(100.0)


def test_grid_includes_a_candidate_off_the_grid():
    """45/20/35 is not a multiple of ten, and a sweep that cannot see the
    incumbent is not much of a test."""
    incumbent = Candidate(45.0, 20.0, 35.0)
    labels = {c.label for c in sweep.grid(include=[incumbent])}
    assert incumbent.label in labels


def test_grid_does_not_duplicate_an_included_candidate():
    on_grid = Candidate(40.0, 20.0, 40.0)
    labels = [c.label for c in sweep.grid(include=[on_grid])]
    assert labels.count(on_grid.label) == 1


# --- blending -------------------------------------------------------------


def test_blend_renormalises_over_present_pillars():
    frame = pd.DataFrame({"quality_score": [80.0], "value_score": [np.nan], "technical": [40.0]})
    blended = sweep.blend_panel(frame, Candidate(50.0, 25.0, 25.0))
    # Value is missing, so the result is the Q/T mix over their own weights.
    assert blended.iloc[0] == pytest.approx((80 * 50 + 40 * 25) / 75)


def test_a_zero_weight_pillar_is_ignored_entirely():
    frame = pd.DataFrame({"quality_score": [0.0], "value_score": [0.0], "technical": [90.0]})
    assert sweep.blend_panel(frame, Candidate(0.0, 0.0, 100.0)).iloc[0] == pytest.approx(90.0)


# --- decile construction --------------------------------------------------


def test_deciles_are_formed_within_each_date_not_across_them():
    """Pooling dates lets a bull month's scores outrank a bear month's, which
    measures the calendar rather than the stock."""
    frame = pd.DataFrame(
        {
            "as_of": ["2025-01-31"] * 20 + ["2025-02-28"] * 20,
            # February's scores are uniformly higher.
            "quality_score": list(np.linspace(0, 40, 20)) + list(np.linspace(60, 100, 20)),
            "value_score": [50.0] * 40,
            "technical": [50.0] * 40,
            "forward_return": [0.0] * 40,
        }
    )
    scored = sweep.deciles_for(frame, sweep.blend_panel(frame, Candidate(100.0, 0.0, 0.0)))
    for _, group in scored.groupby("as_of"):
        assert set(group["decile"].dropna()) == set(range(1, 11))


def test_a_date_with_too_few_names_gets_no_deciles():
    frame = pd.DataFrame(
        {
            "as_of": ["2025-01-31"] * 5,
            "quality_score": [10.0, 20.0, 30.0, 40.0, 50.0],
            "value_score": [0.0] * 5,
            "technical": [0.0] * 5,
            "forward_return": [0.0] * 5,
        }
    )
    scored = sweep.deciles_for(frame, sweep.blend_panel(frame, Candidate(100.0, 0.0, 0.0)))
    assert scored["decile"].isna().all()


# --- the search itself ----------------------------------------------------


def test_the_sweep_finds_a_pillar_that_genuinely_predicts():
    results = sweep.search(panel(signal=1.2), candidates=[
        Candidate(100.0, 0.0, 0.0), Candidate(0.0, 0.0, 100.0)
    ])
    by_label = results.set_index("weights")["median_rho"]
    assert by_label["0/0/100"] > by_label["100/0/0"]
    assert by_label["0/0/100"] > 0.5


def test_the_sweep_reports_no_ordering_when_nothing_predicts():
    """The honest outcome on noise, and the one the real run produced."""
    results = sweep.search(panel(signal=0.0, seed=7), candidates=[Candidate(50.0, 25.0, 25.0)])
    assert abs(results.iloc[0]["median_rho"]) < 0.7


def test_stability_requires_both_halves_to_rank():
    """A combination that ranks in one half and reverses in the other has told
    us about that half, not about the strategy."""
    frame = panel(n_dates=8, signal=0.0, seed=3)
    early = frame["as_of"] < "2025-05-28"
    # Predictive in the first half, deliberately inverted in the second.
    frame.loc[early, "forward_return"] = (frame.loc[early, "technical"] - 50) / 100
    frame.loc[~early, "forward_return"] = -(frame.loc[~early, "technical"] - 50) / 100

    row = sweep.search(frame, candidates=[Candidate(0.0, 0.0, 100.0)]).iloc[0]
    assert row["ic_first_half"] > 0.5
    assert row["ic_second_half"] < -0.5
    assert not row["stable"]


def test_stability_holds_when_both_halves_agree():
    frame = panel(n_dates=8, signal=1.5, seed=11)
    row = sweep.search(frame, candidates=[Candidate(0.0, 0.0, 100.0)]).iloc[0]
    assert row["stable"]


# --- the information coefficient ------------------------------------------


def test_ic_measures_every_observation_not_ten_decile_medians():
    """The correction that matters: a stock-level signal of +0.035 came back as
    +0.89 from a rank correlation over ten decile medians."""
    frame = panel(n_dates=10, n_symbols=200, signal=0.35, seed=5)
    scores = sweep.blend_panel(frame, Candidate(0.0, 0.0, 100.0))

    stats = sweep.information_coefficient(frame, scores)
    decile_rho = sweep.rank_quality(
        sweep.decile_study(sweep.deciles_for(frame, scores))
    )["median_rho"]

    assert 0 < stats["ic"] < 0.5, "IC stays on the scale of a real correlation"
    assert stats["dates"] == 10
    assert decile_rho > stats["ic"], "the decile statistic overstates by construction"


def test_ic_carries_a_standard_error_and_t():
    frame = panel(n_dates=12, n_symbols=150, signal=0.0, seed=2)
    stats = sweep.information_coefficient(
        frame, sweep.blend_panel(frame, Candidate(0.0, 0.0, 100.0))
    )
    assert abs(stats["ic_t"]) < 3, "pure noise must not look significant"
    assert stats["ic_se"] > 0


def test_ic_is_undefined_with_too_few_dates():
    frame = panel(n_dates=2, n_symbols=50)
    stats = sweep.information_coefficient(
        frame, sweep.blend_panel(frame, Candidate(0.0, 0.0, 100.0))
    )
    assert np.isnan(stats["ic"])


def test_ic_skips_dates_with_too_few_names():
    frame = panel(n_dates=6, n_symbols=10)
    stats = sweep.information_coefficient(
        frame, sweep.blend_panel(frame, Candidate(0.0, 0.0, 100.0))
    )
    assert np.isnan(stats["ic"])


def test_split_halves_divides_by_date():
    frame = panel(n_dates=8)
    first, second = sweep.split_halves(frame)
    assert set(first["as_of"]).isdisjoint(set(second["as_of"]))
    assert max(first["as_of"]) < min(second["as_of"])
