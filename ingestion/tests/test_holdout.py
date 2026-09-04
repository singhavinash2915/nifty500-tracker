"""The held-out protocol.

What has to be true for the result to mean anything: the split must be on the
rebalance date, the fit must see only training rows, and the direction of each
feature must come from the data rather than from the hypothesis it was written
with. These pin all three.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from n500.backtest import holdout


def panel(seed: int = 0, *, signal: float = 0.0, dates: int = 12, names: int = 60):
    """A synthetic panel where `signal` sets how strongly `good` predicts."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(dates):
        stamp = f"2024-{1 + d % 12:02d}-{15 if d < 12 else 16:02d}"
        for n in range(names):
            good = rng.normal()
            noise = rng.normal()
            rows.append(
                {
                    "as_of": stamp,
                    "symbol": f"SYM{n}",
                    "good": good,
                    "backwards": -good,
                    "useless": rng.normal(),
                    "forward_return": signal * good + noise,
                }
            )
    return pd.DataFrame(rows)


class TestSplit:
    def test_training_is_strictly_before_the_cut(self):
        frame = pd.DataFrame({
            "as_of": ["2024-06-30", "2025-01-01", "2025-06-30"],
            "forward_return": [0.1, 0.2, 0.3],
        })
        train, test = holdout.split(frame, date(2025, 1, 1))
        assert list(train["as_of"]) == ["2024-06-30"]
        assert list(test["as_of"]) == ["2025-01-01", "2025-06-30"]

    def test_the_cut_date_itself_belongs_to_the_test_set(self):
        frame = pd.DataFrame({"as_of": ["2025-01-01"], "forward_return": [0.1]})
        train, test = holdout.split(frame, date(2025, 1, 1))
        assert train.empty and len(test) == 1


class TestFit:
    FEATURES = {"good": +1, "backwards": +1, "useless": +1}

    def test_a_real_signal_is_selected(self):
        fitted = holdout.fit(panel(1, signal=0.6), self.FEATURES, select_t=2.0)
        assert "good" in fitted.selected

    def test_the_direction_comes_from_the_data_not_the_hypothesis(self):
        # `backwards` is passed in with a +1 hypothesis and is the negation of
        # the signal, so an honest fit has to give it -1.
        fitted = holdout.fit(panel(1, signal=0.6), self.FEATURES, select_t=2.0)
        assert fitted.signs["backwards"] == -1
        assert fitted.signs["good"] == +1

    def test_pure_noise_is_selected_only_occasionally(self):
        """Not "never" — that assertion is false, and instructively so.

        The first version of this test asserted that noise is never selected
        and failed on the second seed tried: with twelve dates, a |t| of 2
        comes up on pure noise a few percent of the time, which is exactly what
        a 5% threshold means. Selection on noise is not a bug to be eliminated,
        it is the thing the held-out period exists to catch — see
        `test_a_composite_fitted_on_noise_does_not_predict_on_fresh_noise`.

        So the property worth pinning is the rate, not the absence.
        """
        hits = sum(
            "useless" in holdout.fit(panel(seed, signal=0.0), self.FEATURES, select_t=2.0).selected
            for seed in range(40)
        )
        assert hits <= 8, f"noise selected {hits}/40 times — the threshold is not biting"

    def test_nothing_selected_gives_an_empty_composite(self):
        fitted = holdout.fit(panel(3, signal=0.0), self.FEATURES, select_t=99.0)
        assert fitted.selected == []
        assert holdout.evaluate(panel(3), fitted.composite(panel(3)))["dates"] == 0


class TestComposite:
    def test_a_fitted_composite_predicts_on_fresh_data(self):
        fitted = holdout.fit(panel(4, signal=0.8), TestFit.FEATURES, select_t=2.0)
        fresh = panel(99, signal=0.8)
        assert holdout.evaluate(fresh, fitted.composite(fresh))["ic"] > 0.1

    def test_a_composite_fitted_on_noise_does_not_predict_on_fresh_noise(self):
        # The point of the whole exercise: selection on noise must not survive.
        fitted = holdout.fit(panel(5, signal=0.0), TestFit.FEATURES, select_t=0.0)
        fresh = panel(123, signal=0.0)
        assert abs(holdout.evaluate(fresh, fitted.composite(fresh))["ic"]) < 0.1

    def test_features_are_ranked_per_date_before_averaging(self):
        # A feature on a huge scale must not swamp a 0/1 flag.
        frame = panel(6, signal=0.5)
        frame["huge"] = frame["good"] * 1e6
        fitted = holdout.fit(frame, {"good": +1, "huge": +1}, select_t=0.0)
        scores = fitted.composite(frame)
        assert scores.between(0, 1).all()
        assert scores.notna().all()


class TestEvaluate:
    def test_a_date_with_too_few_names_is_skipped(self):
        small = panel(7, names=5)
        assert holdout.evaluate(small, small["good"])["dates"] == 0

    def test_positive_dates_is_a_fraction(self):
        stats = holdout.evaluate(panel(8, signal=0.9), panel(8, signal=0.9)["good"])
        assert 0.0 <= stats["positive_dates"] <= 1.0
