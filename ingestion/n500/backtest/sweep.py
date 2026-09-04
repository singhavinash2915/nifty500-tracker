"""Search the weight space against the decile curve, not against returns.

Optimising a backtest for its headline return is how overfitting happens: with
fourteen overlapping rebalances in one market regime, some weight combination
will always produce a flattering portfolio, and it will mean nothing. The
objective here is instead whether the score *ranks* — whether a higher decile
reliably earned a better forward return across the whole cross-section. That is
a property of thousands of observations rather than of twenty picks, and it is
the thing that has to be true for the screener to be worth using at all.

Even so, a grid search on this much data finds noise. Three habits keep it
honest:

  * the whole surface is reported, not the peak, because a broad plateau of
    decent weights is a finding and a lone spike is an artefact;
  * every candidate is re-measured on the first and second halves of the
    period separately, and a combination that only works in one half is
    flagged rather than crowned;
  * the incumbent 45/20/35 is always scored alongside, so "better than what we
    had" is visible rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import decile_study, rank_quality

# Coarse on purpose. A finer grid does not buy accuracy on this sample, it just
# buys more chances to fit noise.
DEFAULT_GRID = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

MIN_PER_DECILE = 20

# Minimum names on a date before a within-date correlation means anything.
MIN_NAMES_PER_DATE = 30

# Month-end rebalances against a six-month hold: each window shares five months
# with the next one, so consecutive dates are not independent draws. Treating
# them as independent is what makes a naive standard error too small — the
# Newey-West correction below widens it by however much the per-date ICs are
# actually autocorrelated, which is the honest bar for "significant".
OVERLAP_LAG = 5


def newey_west_se(values: np.ndarray, *, lag: int = OVERLAP_LAG) -> float:
    """Standard error of a mean under serial correlation, Bartlett-weighted.

    With overlapping holding periods the ordinary standard error of the mean
    assumes fourteen independent observations where there are closer to three.
    This is the standard fix, and it matters: the naive t on the resistance
    features ran between 2.8 and 7, and the corrected one moves several of them
    across the line in both directions.
    """
    n = len(values)
    if n < 4:
        return float("nan")
    errors = values - values.mean()
    variance = float(errors @ errors) / n
    for k in range(1, min(lag, n - 1) + 1):
        gamma = float(errors[k:] @ errors[:-k]) / n
        variance += 2.0 * (1.0 - k / (lag + 1)) * gamma
    return float(np.sqrt(max(variance, 1e-12) / n))


def information_coefficient(panel: pd.DataFrame, scores: pd.Series) -> dict:
    """Mean within-date rank correlation of score against forward return.

    The primary objective, and a correction to an earlier mistake. Ranking the
    ten decile *medians* against the decile number looked like a measure of
    ordering, but ten noisy points produce a rank correlation with enormous
    variance: a stock-level signal of +0.035 came back as +0.89 at decile
    level, and a grid search on that statistic was optimising noise into a
    confident-sounding recommendation.

    The information coefficient measures the same idea against every
    observation instead of ten summaries, and it comes with a standard error,
    so "this weighting is better" can be checked rather than asserted.
    Correlations are computed within each rebalance date and then averaged —
    pooling dates would let a strong month outrank a weak one and measure the
    calendar.
    """
    frame = panel.assign(score=scores).dropna(subset=["score", "forward_return"])
    per_date: list[float] = []
    for _, group in frame.groupby("as_of"):
        if len(group) < MIN_NAMES_PER_DATE:
            continue
        rho = group["score"].rank().corr(group["forward_return"].rank())
        if np.isfinite(rho):
            per_date.append(float(rho))

    if len(per_date) < 3:
        return {"ic": np.nan, "ic_se": np.nan, "ic_t": np.nan, "dates": len(per_date)}

    values = np.array(per_date)
    mean = float(values.mean())
    # Two standard errors, and the wider one is the one to believe. The naive
    # version treats each rebalance as an independent draw; they overlap by five
    # months, so `nw` is what any claim of significance has to clear.
    naive = float(values.std(ddof=1) / np.sqrt(len(values)))
    nw = newey_west_se(values)
    return {
        "ic": mean,
        "ic_se": nw if np.isfinite(nw) else naive,
        "ic_se_naive": naive,
        "ic_t": mean / nw if np.isfinite(nw) and nw > 0 else np.nan,
        "ic_t_naive": mean / naive if naive > 0 else np.nan,
        "ic_positive_dates": float((values > 0).mean()),
        "dates": len(values),
    }


# Every feature worth putting on trial individually, with the direction the
# theory behind it predicts. `sign` is +1 where high values should mean high
# forward returns and -1 where they should mean low ones; the reported IC is
# always signed as stated, so a feature that works reads positive whichever way
# round it is measured, and one that works *backwards* reads clearly negative.
FEATURES: dict[str, int] = {
    "quality_score": +1,
    "value_score": +1,
    "revision_score": +1,
    "ownership_score": +1,
    "tm_score": +1,
    "ts_score": +1,
    "sue_pat": +1,
    "sue_revenue": +1,
    "accel_pat": +1,
    "margin_revision": +1,
    "consistency": +1,
    "promoter_delta_4q": +1,
    "fii_delta_4q": +1,
    "dii_delta_4q": +1,
    # The resistance work. A strong level overhead should cap the move; room to
    # it should help; a failed breakout should hurt.
    "resistance_strength": -1,
    "headroom": +1,
    "false_breakout": -1,
    "rejected_at_resistance": -1,
    "zone_respect": +1,
    "zone_strength": +1,
    # Not a signal — a check that the liquidity gate is not quietly a size bet.
    "turnover_60d_cr": +1,
}


def feature_ic(panel: pd.DataFrame, features: dict[str, int] | None = None) -> pd.DataFrame:
    """Information coefficient for each feature on its own.

    This is the honest test of a new idea, and a different question from the
    weight sweep. The sweep asks which *combination* ranks best, which on
    fourteen overlapping dates will always find something; this asks whether one
    number, measured across every observation, carries any information at all.
    A feature that cannot clear a t of 2 on its own has not earned a weight,
    however good the story behind it.

    Rows come back ordered by t, and `signed` is the correlation oriented the
    way the theory says it should point.
    """
    rows = []
    for name, sign in (features or FEATURES).items():
        if name not in panel:
            continue
        values = pd.to_numeric(panel[name], errors="coerce")
        if values.notna().sum() < MIN_NAMES_PER_DATE:
            continue
        stats = information_coefficient(panel, values * sign)
        rows.append(
            {
                "feature": name,
                "expected_sign": "+" if sign > 0 else "-",
                "n": int(values.notna().sum()),
                "ic": stats["ic"],
                "ic_se": stats["ic_se"],
                "t": stats["ic_t"],
                "t_naive": stats.get("ic_t_naive"),
                "dates": stats["dates"],
                "positive_dates": stats.get("ic_positive_dates", np.nan),
            }
        )

    frame = pd.DataFrame(rows)
    return frame.sort_values("t", ascending=False).reset_index(drop=True) if len(frame) else frame


PILLARS = ("quality", "value", "revision", "ownership", "technical")


@dataclass(frozen=True)
class Candidate:
    quality: float
    value: float
    technical: float
    revision: float = 0.0
    ownership: float = 0.0

    @property
    def label(self) -> str:
        """Three parts while the newer pillars are unused, five once they are.

        Keeping the short form means every label in the existing sweep output
        still reads the same, so results from before the two new pillars
        existed remain comparable to results from after.
        """
        head = f"{self.quality:.0f}/{self.value:.0f}/{self.technical:.0f}"
        if self.revision or self.ownership:
            return f"{head}/{self.revision:.0f}/{self.ownership:.0f}"
        return head

    def as_dict(self) -> dict[str, float]:
        return {
            "quality": self.quality,
            "value": self.value,
            "technical": self.technical,
            "revision": self.revision,
            "ownership": self.ownership,
        }


def grid(
    step_values: list[int] = None, *, include: list["Candidate"] | None = None
) -> list[Candidate]:
    """Every combination summing to 100, plus any candidate named explicitly.

    `include` exists so the incumbent weighting is always measured even when it
    does not sit on the grid — 45/20/35 is not a multiple of ten, and a sweep
    that cannot see the thing it is meant to improve on is not much of a test.
    """
    values = step_values or DEFAULT_GRID
    out: list[Candidate] = []
    for q in values:
        for v in values:
            t = 100 - q - v
            if t < 0 or t not in values:
                continue
            out.append(Candidate(float(q), float(v), float(t)))

    seen = {c.label for c in out}
    for candidate in include or []:
        if candidate.label not in seen:
            out.append(candidate)
    return out


def blend_panel(panel: pd.DataFrame, candidate: Candidate) -> pd.Series:
    """Re-blend a pre-scored panel. The cheap half of the search."""
    weighted = pd.Series(0.0, index=panel.index)
    available = pd.Series(0.0, index=panel.index)
    for column, weight in (
        ("quality_score", candidate.quality),
        ("value_score", candidate.value),
        ("technical", candidate.technical),
        ("revision_score", candidate.revision),
        ("ownership_score", candidate.ownership),
    ):
        if weight <= 0 or column not in panel:
            continue
        values = pd.to_numeric(panel[column], errors="coerce")
        weighted += values.fillna(0.0) * weight
        available += values.notna().astype("float64") * weight
    return weighted / available.replace(0.0, np.nan)


def deciles_for(panel: pd.DataFrame, scores: pd.Series) -> pd.DataFrame:
    """Rank within each rebalance date, never across them.

    Pooling dates would let a bull month's scores outrank a bear month's, which
    measures the calendar rather than the stock.
    """
    frame = panel.assign(score=scores)
    frame = frame[frame["score"].notna()]
    if frame.empty:
        return pd.DataFrame()

    def bucket(scores: pd.Series) -> pd.Series:
        # A date with too few names cannot be cut into ten meaningful buckets.
        if len(scores) < 10:
            return pd.Series(np.nan, index=scores.index)
        return pd.qcut(scores.rank(method="first"), 10, labels=False) + 1

    # transform on the score column rather than apply on the frame: apply
    # returns a DataFrame when a group yields all-NaN, and warns about
    # operating on the grouping column.
    frame["decile"] = frame.groupby("as_of")["score"].transform(bucket)
    return frame


def evaluate(panel: pd.DataFrame, candidate: Candidate) -> dict:
    scored = deciles_for(panel, blend_panel(panel, candidate))
    if scored.empty:
        return {}

    study = decile_study(scored)
    verdict = rank_quality(study)
    if "median_rho" not in verdict:
        return {}

    top = study[study["decile"] == study["decile"].max()]
    bottom = study[study["decile"] == study["decile"].min()]

    return {
        "weights": candidate.label,
        "quality": candidate.quality,
        "value": candidate.value,
        "technical": candidate.technical,
        "revision": candidate.revision,
        "ownership": candidate.ownership,
        **information_coefficient(panel, blend_panel(panel, candidate)),
        "median_rho": verdict["median_rho"],
        "hit_rho": verdict["hit_rate_rho"],
        "spread_pp": verdict["top_minus_bottom_median"] * 100,
        "top_median": float(top.iloc[0]["median"]) if not top.empty else np.nan,
        "top_hit25": float(top.iloc[0]["hit_rate_25pct"]) if not top.empty else np.nan,
        "top_p10": float(top.iloc[0]["p10"]) if not top.empty else np.nan,
        "bottom_hit25": float(bottom.iloc[0]["hit_rate_25pct"]) if not bottom.empty else np.nan,
        "verdict": verdict["verdict"],
    }


def split_halves(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(panel["as_of"].unique())
    if len(dates) < 4:
        return panel, panel.iloc[0:0]
    cut = dates[len(dates) // 2]
    return panel[panel["as_of"] < cut], panel[panel["as_of"] >= cut]


def search(
    panel: pd.DataFrame,
    candidates: list[Candidate] | None = None,
    *,
    include: list[Candidate] | None = None,
) -> pd.DataFrame:
    """Score every candidate on the whole period and on each half."""
    candidates = candidates or grid(include=include)
    first, second = split_halves(panel)

    rows = []
    for candidate in candidates:
        overall = evaluate(panel, candidate)
        if not overall:
            continue
        early = evaluate(first, candidate) if not first.empty else {}
        late = evaluate(second, candidate) if not second.empty else {}
        overall["rho_first_half"] = early.get("median_rho", np.nan)
        overall["rho_second_half"] = late.get("median_rho", np.nan)
        # A combination that ranks in one half and not the other has told us
        # about that half, not about the strategy.
        overall["ic_first_half"] = early.get("ic", np.nan)
        overall["ic_second_half"] = late.get("ic", np.nan)
        # Stability is judged on the information coefficient, not on the
        # decile statistic, for the reason given in its docstring.
        overall["stable"] = bool(
            np.isfinite(overall["ic_first_half"])
            and np.isfinite(overall["ic_second_half"])
            and overall["ic_first_half"] > 0
            and overall["ic_second_half"] > 0
        )
        rows.append(overall)

    return pd.DataFrame(rows)
