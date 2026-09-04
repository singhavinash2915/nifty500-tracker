"""The revision pillar, checked against series built by hand.

The point of these is that SUE is easy to write and easy to get subtly wrong:
compare to the wrong quarter and you measure seasonality, include the latest
difference in its own benchmark and every surprise shrinks toward zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from n500.scoring import revision


def steady(base: float = 100.0, quarters: int = 12, growth: float = 0.0) -> list[float]:
    """A series with a fixed seasonal shape and a constant yearly step."""
    shape = [1.0, 0.9, 1.1, 1.2]
    return [
        base * shape[i % 4] * (1.0 + growth) ** (i // 4) for i in range(quarters)
    ]


class TestSue:
    def test_a_company_exactly_on_trend_surprises_by_nothing(self):
        # Every year-on-year difference identical, so the drift explains the
        # latest one completely. Noise is zero, which is undefined rather than
        # an infinite surprise.
        values = [100, 90, 110, 120] * 3
        values = [v + 10 * (i // 4) for i, v in enumerate(values)]
        assert revision.sue(values) is None

    def test_a_beat_is_positive_and_a_miss_is_negative(self):
        base = steady(growth=0.10)
        beat = base[:-1] + [base[-1] * 1.30]
        miss = base[:-1] + [base[-1] * 0.70]
        assert revision.sue(beat) > 1.0
        assert revision.sue(miss) < -1.0

    def test_the_comparison_is_seasonal_not_sequential(self):
        # Q2 is always the weak quarter. A sequential model would read the
        # normal Q1 -> Q2 fall as a miss every single year.
        values = steady(quarters=12)
        assert revision.sue(values) is None  # no noise at all: undefined, not a miss

        noisy = [v * (1.0 + 0.01 * ((i % 3) - 1)) for i, v in enumerate(steady(quarters=12))]
        # Seasonally flat with small wobble — the surprise must stay small.
        assert abs(revision.sue(noisy)) < 3.0

    def test_the_latest_difference_is_excluded_from_its_own_benchmark(self):
        values = steady(growth=0.05)
        jumped = values[:-1] + [values[-1] * 1.5]

        # If the last difference were included in the drift and the standard
        # deviation, it would partly explain itself and the score would shrink.
        with_leak = _sue_leaking(jumped)
        assert revision.sue(jumped) > with_leak

    def test_short_history_scores_nothing(self):
        assert revision.sue([100, 110, 120, 130, 140]) is None

    def test_the_extreme_tail_is_clipped(self):
        # Mild noise so the standard deviation is defined but small, then a
        # print several hundred times the trend — the shape of a company
        # returning from a loss, which without a clip would out-rank every
        # genuine beat in the index put together.
        values = [v * (1.0 + 0.02 * ((i % 3) - 1)) for i, v in enumerate(steady(growth=0.05))]
        exploded = values[:-1] + [values[-1] * 500]
        assert revision.sue(exploded) == pytest.approx(revision.SUE_CLIP)


def _sue_leaking(values: list[float]) -> float:
    """What SUE would be if the latest quarter helped set its own benchmark."""
    diffs = revision.seasonal_differences(values)
    drift = float(np.mean(diffs[-4:]))
    noise = float(np.std(diffs[-4:], ddof=1))
    return (diffs[-1] - drift) / noise


class TestAcceleration:
    def test_growth_speeding_up_scores_above_growth_slowing_down(self):
        # Two years, flat base. The penultimate quarter grows 20% year on year
        # and the latest grows 35%; the second company does 40% then 25%.
        base = [100.0] * 4 + [100.0] * 2
        speeding = base + [120.0, 135.0]
        slowing = base + [140.0, 125.0]
        assert revision.growth_acceleration(speeding) == pytest.approx(0.15)
        assert revision.growth_acceleration(slowing) == pytest.approx(-0.15)

    def test_a_negative_base_has_no_growth_rate(self):
        values = [-50, -40, -30, -20, 10, 20, 30, 40]
        assert revision.growth_acceleration(values) is None

    def test_short_history_scores_nothing(self):
        assert revision.growth_acceleration([100, 110, 120]) is None


class TestConsistency:
    def test_counts_quarters_that_grew_year_on_year(self):
        values = [100, 100, 100, 100, 110, 90, 120, 130]
        assert revision.consistency(values) == pytest.approx(0.75)

    def test_needs_a_full_year_of_comparisons(self):
        assert revision.consistency([100, 110, 120, 130, 140]) is None


class TestMarginRevision:
    def test_reports_the_change_in_points(self):
        assert revision.margin_revision([10, 10, 10, 10, 13]) == pytest.approx(3.0)

    def test_needs_five_quarters(self):
        assert revision.margin_revision([10, 11, 12, 13]) is None


class TestScore:
    def frame(self) -> pd.DataFrame:
        beater = revision.build_metrics(
            {
                "quarterly_pat": steady(growth=0.05)[:-1] + [steady(growth=0.05)[-1] * 1.4],
                "quarterly_revenue": steady(growth=0.05)[:-1] + [steady(growth=0.05)[-1] * 1.3],
                "quarterly_opm": [12, 12, 12, 12, 15],
            }
        )
        misser = revision.build_metrics(
            {
                "quarterly_pat": steady(growth=0.05)[:-1] + [steady(growth=0.05)[-1] * 0.6],
                "quarterly_revenue": steady(growth=0.05)[:-1] + [steady(growth=0.05)[-1] * 0.7],
                "quarterly_opm": [12, 12, 12, 12, 9],
            }
        )
        rows = []
        for symbol, metrics in (("BEAT", beater), ("MISS", misser)):
            rows.append({"symbol": symbol, "sector": "Capital Goods", **metrics})
        return pd.DataFrame(rows).set_index("symbol")

    def test_the_beat_outranks_the_miss(self):
        scores = revision.score(self.frame())
        assert scores.loc["BEAT"] > scores.loc["MISS"]

    def test_a_company_with_no_quarters_scores_nothing(self):
        frame = pd.DataFrame(
            [{"symbol": "NEW", "sector": "IT", **revision.build_metrics({})}]
        ).set_index("symbol")
        assert pd.isna(revision.score(frame).loc["NEW"])
