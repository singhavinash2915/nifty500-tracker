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
    # Standard error of the mean across dates. Overlapping holding periods make
    # the dates correlated, so this understates the true error — another reason
    # to treat a t of 2 here as suggestive rather than settled.
    se = float(values.std(ddof=1) / np.sqrt(len(values)))
    return {
        "ic": mean,
        "ic_se": se,
        "ic_t": mean / se if se > 0 else np.nan,
        "ic_positive_dates": float((values > 0).mean()),
        "dates": len(values),
    }


@dataclass(frozen=True)
class Candidate:
    quality: float
    value: float
    technical: float

    @property
    def label(self) -> str:
        return f"{self.quality:.0f}/{self.value:.0f}/{self.technical:.0f}"

    def as_dict(self) -> dict[str, float]:
        return {"quality": self.quality, "value": self.value, "technical": self.technical}


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
    ):
        if weight <= 0:
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
