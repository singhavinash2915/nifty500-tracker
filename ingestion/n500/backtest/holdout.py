"""Fit on the early period, decide, then look at the late one exactly once.

Why this is separate from the sweep
-----------------------------------
Every result this project has produced so far was measured on the same data it
was found in. That is not a small caveat. The resistance features were noticed
because they stood out in the panel, their signs were set by what the panel
said, and their significance was then computed on the panel — the same
observations doing all three jobs. Half-sample stability helped, but the halves
were both visible while the decisions were being made.

A held-out test fixes the order of operations. Everything that counts as a
decision — which features to use, which way each points, how to combine them —
is taken from the training period alone. The test period is then scored once,
with no going back. If a result was an artefact of searching, this is where it
disappears.

The one rule that makes it worth anything: **do not tune against the test
output.** A held-out set consulted twice is a training set with extra steps. If
the numbers below disappoint, the honest responses are to accept them or to
gather more data — not to adjust the features and re-run.

What "fit" means here
---------------------
Deliberately almost nothing. Signs come from the sign of each feature's training
IC, and the composite is an equal-weighted average of the ranks of whichever
features cleared a t-statistic threshold in training. No regression, no weight
optimisation, no per-feature scaling — every additional fitted parameter is
another thing that can be fitted to noise, and with 37 rebalances there is no
budget for them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .sweep import MIN_NAMES_PER_DATE, newey_west_se

# A feature has to clear this in training to be selected. Two is the usual bar
# and the overlap correction has already been applied, so it is a real two.
SELECT_T = 2.0


def _t(values: np.ndarray) -> float:
    se = newey_west_se(values)
    return float(values.mean() / se) if se and se > 0 else float("nan")


def ic_series(panel: pd.DataFrame, values: pd.Series) -> np.ndarray:
    """Within-date rank correlation of a score against the forward return."""
    frame = panel.assign(_score=values)
    out: list[float] = []
    for _, group in frame.groupby("as_of"):
        sub = group.dropna(subset=["_score", "forward_return"])
        if len(sub) < MIN_NAMES_PER_DATE:
            continue
        rho = sub["_score"].rank().corr(sub["forward_return"].rank())
        if np.isfinite(rho):
            out.append(float(rho))
    return np.asarray(out)


def evaluate(panel: pd.DataFrame, values: pd.Series) -> dict:
    series = ic_series(panel, values)
    if len(series) < 3:
        return {"ic": np.nan, "t": np.nan, "dates": len(series), "positive_dates": np.nan}
    return {
        "ic": float(series.mean()),
        "t": _t(series),
        "dates": len(series),
        "positive_dates": float((series > 0).mean()),
    }


@dataclass
class Fit:
    """What was learned from training, and nothing else."""

    signs: dict[str, int] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    training: pd.DataFrame = field(default_factory=pd.DataFrame)

    def composite(self, panel: pd.DataFrame) -> pd.Series:
        """Equal-weighted mean of the selected features' within-date ranks.

        Ranked per date before averaging so a feature with a wide raw scale
        cannot dominate one that is a 0/1 flag, and so the composite means the
        same thing on a date with 400 names as on one with 300.
        """
        if not self.selected:
            return pd.Series(np.nan, index=panel.index)

        parts = []
        for name in self.selected:
            signed = pd.to_numeric(panel[name], errors="coerce") * self.signs[name]
            ranked = signed.groupby(panel["as_of"]).rank(pct=True)
            parts.append(ranked)
        return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def fit(panel: pd.DataFrame, features: dict[str, int], *, select_t: float = SELECT_T) -> Fit:
    """Choose features and directions using the training period only.

    `features` supplies candidates; the sign each one is given comes from the
    data rather than from the caller's hypothesis, because the hypotheses in this
    project have a poor record and the point of the exercise is to find out
    whether *that* choice survives being made honestly.
    """
    rows = []
    for name in features:
        if name not in panel:
            continue
        raw = pd.to_numeric(panel[name], errors="coerce")
        if raw.notna().sum() < MIN_NAMES_PER_DATE:
            continue
        stats = evaluate(panel, raw)
        if not np.isfinite(stats["t"]):
            continue
        sign = 1 if stats["ic"] >= 0 else -1
        rows.append(
            {
                "feature": name,
                "train_ic": stats["ic"],
                "train_t": stats["t"],
                "sign": sign,
                # Significance is a property of the magnitude, not the guess.
                "selected": abs(stats["t"]) >= select_t,
                "n": int(raw.notna().sum()),
            }
        )

    training = pd.DataFrame(rows).sort_values("train_t", key=abs, ascending=False)
    selected = training[training["selected"]]["feature"].tolist()
    signs = dict(zip(training["feature"], training["sign"]))
    return Fit(signs=signs, selected=selected, training=training.reset_index(drop=True))


def split(panel: pd.DataFrame, on: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Training is everything strictly before `on`; test is the rest.

    Note what this does *not* do: the six-month forward return of the last
    training rebalance reaches into the test period. Nothing was decided from it
    — the split is on the date the position would have been taken — but the two
    sets are adjacent rather than isolated, and a strict version would drop six
    months between them. With 37 rebalances that costs a fifth of the sample,
    so it is stated here instead.
    """
    stamps = pd.to_datetime(panel["as_of"]).dt.date
    return panel[stamps < on].copy(), panel[stamps >= on].copy()
