"""The rank explanation and the market backdrop.

The rank explanation is only worth showing if it is exactly the score taken
apart, so the first tests pin that identity down. The backdrop is only worth
showing beside the study's numbers if it is measured the way the study
measured, so the rest check its behaviour on markets whose answer is known.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from n500.scoring import conviction, regime


def cross_section(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(index=[f"S{i:03d}" for i in range(n)])
    for name in conviction.WEIGHTS:
        if name.endswith(("_at_resistance", "_breakout")) or name.startswith("rejected"):
            frame[name] = (rng.random(n) < 0.08).astype(float)     # rare 0/1 events
        else:
            frame[name] = rng.normal(size=n)
    frame.iloc[::17, 3] = np.nan                                    # some missing inputs
    return frame


def old_score(frame: pd.DataFrame) -> pd.Series:
    """The implementation before the refactor, kept to prove nothing moved."""
    ranks, available = [], pd.Series(0, index=frame.index, dtype="int64")
    for name, sign in conviction.WEIGHTS.items():
        values = pd.to_numeric(frame[name], errors="coerce") * sign
        ranks.append(values.rank(pct=True))
        available += values.notna().astype("int64")
    mean = pd.concat(ranks, axis=1).mean(axis=1, skipna=True) * 100.0
    return mean.where(available >= conviction.MIN_FEATURES).round(2)


class TestRankContributions:
    def test_the_refactor_did_not_move_a_single_score(self):
        frame = cross_section()
        pd.testing.assert_series_equal(conviction.score(frame), old_score(frame))

    def test_contributions_add_up_to_the_score_above_fifty(self):
        frame = cross_section()
        score = conviction.score(frame)
        total = conviction.rank_contributions(frame).sum(axis=1) + 50
        ok = score.notna()
        assert np.allclose(total[ok], score[ok], atol=0.01)

    def test_a_rare_flag_is_worth_much_more_to_a_stock_that_has_it(self):
        frame = cross_section()
        points = conviction.rank_contributions(frame)["false_breakout"]
        has = frame["false_breakout"] == 1
        assert points[has].min() > 3
        assert points[~has].max() < 0 and points[~has].min() > -1

    def test_top_drivers_are_the_largest_lifts_only(self):
        points = pd.Series({"headroom": 6.1, "tm_score": -2.0, "value_score": 1.2,
                            "false_breakout": 3.8, "doji_at_resistance": 0.4})
        assert conviction.top_drivers(points) == [
            ["headroom", 6.1], ["false_breakout", 3.8], ["value_score", 1.2]]

    def test_a_lift_that_rounds_to_nothing_is_not_a_driver(self):
        # EMBASSY on 7 October listed "zone respect +0.0" as a top driver.
        points = pd.Series({"resistance_strength": 4.7, "tm_score": 3.0, "zone_respect": 0.03})
        assert conviction.top_drivers(points) == [["resistance_strength", 4.7], ["tm_score", 3.0]]

    def test_a_stock_with_nothing_lifting_it_has_no_drivers(self):
        assert conviction.top_drivers(pd.Series({"headroom": -1.0})) == []


class TestFadingShare:
    def test_share_of_the_lift_from_signals_that_fade(self):
        points = pd.Series({"tm_score": 3.0, "value_score": 1.0, "headroom": 4.0,
                            "false_breakout": -2.0})
        assert regime.fading_share(points) == 0.5          # (3 + 1) / (3 + 1 + 4)

    def test_nothing_lifting_means_no_share(self):
        assert regime.fading_share(pd.Series({"tm_score": -1.0})) is None

    def test_the_fading_set_is_what_the_study_found(self):
        assert set(regime.FADING_IN_WEAK) == {"tm_score", "value_score", "margin_revision"}


def market(daily: list[float], symbols: int = 40, seed: int = 3) -> pd.DataFrame:
    """Long-format prices where every stock follows the given daily returns."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=len(daily) + 1)
    rows = []
    for i in range(symbols):
        path = 100 * np.cumprod([1.0] + [1 + r + rng.normal(0, 0.002) for r in daily])
        rows += [{"symbol": f"S{i}", "date": d, "adj_close": p} for d, p in zip(dates, path)]
    return pd.DataFrame(rows)


class TestBackdrop:
    def test_a_steady_rise_is_a_strong_market_at_its_high(self):
        b = regime.measure(market([0.002] * 300))
        assert b.regime == "strong"
        assert b.breadth > 0.9
        assert b.drawdown > -0.02
        assert b.vs_200dma > 0

    def test_a_rise_then_a_fall_is_a_weak_market_off_its_high(self):
        b = regime.measure(market([0.002] * 250 + [-0.004] * 80))
        assert b.regime == "weak"
        assert b.breadth < 0.5
        assert b.drawdown < -0.2
        assert b.return_3m < 0

    def test_breadth_is_counted_in_floats_never_booleans(self):
        """Guards the cause of a real miscount rather than its symptom.

        Averaging a row that mixed numpy booleans (which add as a logical OR)
        reported 40.3% breadth on a day with 42.1%. The exact failure depends
        on pandas' internal layout and would not reproduce on synthetic data,
        so this checks the invariant the fix rests on: every value is a float.
        """
        wide = pd.DataFrame({"A": [1.0 + i for i in range(260)],
                             "B": [300.0 - i for i in range(260)]})
        wide.iloc[250:, 1] = np.nan                     # B stopped trading
        wide.iloc[5, 0] = np.nan                        # A has a gap in history
        above = regime.above_200dma(wide)
        assert set(above.dtypes) == {np.dtype("float64")}
        assert above.iloc[-1]["A"] == 1.0 and np.isnan(above.iloc[-1]["B"])

    def test_breadth_counts_only_stocks_that_traded(self):
        up = market([0.002] * 300, symbols=20)
        down = market([-0.002] * 300, symbols=20, seed=5)
        down["symbol"] = "DN_" + down["symbol"]
        gone = market([0.002] * 300, symbols=10, seed=9)
        gone["symbol"] = "GONE_" + gone["symbol"]
        gone = gone[gone["date"] < gone["date"].iloc[150]]
        b = regime.measure(pd.concat([up, down, gone]))
        assert b.breadth == pytest.approx(0.5, abs=0.05)

    def test_the_threshold_is_half_of_stocks(self):
        assert regime.WEAK_BREADTH == 0.5

    def test_too_little_history_is_refused_not_guessed(self):
        with pytest.raises(ValueError, match="252 sessions"):
            regime.measure(market([0.001] * 100))

    def test_the_row_carries_the_history_for_its_own_regime(self):
        row = regime.measure(market([0.002] * 250 + [-0.004] * 80)).to_row()
        assert row["regime"] == "weak"
        assert row["hist_months"] == 16
        assert row["hist_top"] - row["hist_avg"] == pytest.approx(0.06)

    def test_the_recorded_study_matches_its_published_edges(self):
        # The docstring and the dashboard quote these edges; the constants
        # must not drift from them.
        weak, strong = regime.HISTORY["weak"], regime.HISTORY["strong"]
        assert weak["top"] - weak["avg"] == pytest.approx(0.060)
        assert strong["top"] - strong["avg"] == pytest.approx(0.094)
        assert weak["months"] + strong["months"] == 38
