"""The ownership pillar.

Two things worth guarding: that a company with no promoter is scored on what
does apply to it rather than dropped or defaulted, and that the disclosure lag
is real, because shareholding rows carry no filing date of their own and the
lag is the only thing standing between this and a look-ahead.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from n500.scoring import ownership


class TestDelta:
    def test_reports_percentage_points(self):
        assert ownership.delta([51.0, 51.5, 52.0, 52.5, 53.0], 4) == pytest.approx(2.0)

    def test_needs_enough_quarters(self):
        assert ownership.delta([51.0, 52.0], 4) is None

    def test_missing_quarters_are_skipped_not_zeroed(self):
        # A None between two readings must not be read as a holding of zero,
        # which would show as a fifty-point promoter exit.
        assert ownership.delta([51.0, None, 52.0], 1) == pytest.approx(1.0)


class TestDisclosureLag:
    def test_a_quarter_is_not_public_on_the_day_it_ends(self):
        assert ownership.disclosed_by(date(2026, 6, 30)) > date(2026, 6, 30)

    def test_the_lag_covers_the_sebi_deadline(self):
        # LODR Regulation 31 allows 21 days; the constant must not be shorter.
        assert (ownership.disclosed_by(date(2026, 6, 30)) - date(2026, 6, 30)).days >= 21


class TestBuildMetrics:
    def test_a_promoterless_company_reports_no_promoter_delta(self):
        metrics = ownership.build_metrics(
            {
                "promoter_history": [0.0, 0.0, 0.0, 0.0, 0.0],
                "has_promoter": False,
                "fii_history": [30.0, 31.0, 32.0, 33.0, 34.0],
                "dii_history": [10.0, 10.0, 11.0, 11.0, 12.0],
            }
        )
        assert metrics["promoter_delta_4q"] is None
        assert metrics["fii_delta_4q"] == pytest.approx(4.0)


class TestScore:
    def frame(self) -> pd.DataFrame:
        rows = [
            # Promoter creeping up and FIIs adding.
            ("ACCUM", True, [50.0, 50.5, 51.0, 51.5, 52.0], [10.0, 11.0, 12.0, 13.0, 14.0], [5.0] * 5),
            # Everything drifting the other way.
            ("EXIT", True, [60.0, 59.5, 59.0, 58.5, 58.0], [20.0, 19.0, 18.0, 17.0, 16.0], [5.0] * 5),
            # No promoter at all, institutions adding hard.
            ("PROF", False, [0.0] * 5, [25.0, 27.0, 29.0, 31.0, 33.0], [8.0] * 5),
        ]
        records = []
        for symbol, has, promoter, fii, dii in rows:
            records.append(
                {
                    "symbol": symbol,
                    "sector": "IT",
                    **ownership.build_metrics(
                        {
                            "promoter_history": promoter,
                            "has_promoter": has,
                            "fii_history": fii,
                            "dii_history": dii,
                        }
                    ),
                }
            )
        return pd.DataFrame(records).set_index("symbol")

    def test_accumulation_outranks_distribution(self):
        scores = ownership.score(self.frame())
        assert scores.loc["ACCUM"] > scores.loc["EXIT"]

    def test_a_promoterless_company_is_still_scored(self):
        scores = ownership.score(self.frame())
        assert not pd.isna(scores.loc["PROF"])

    def test_scores_stay_inside_the_scale(self):
        scores = ownership.score(self.frame())
        assert scores.between(0.0, 100.0).all()
