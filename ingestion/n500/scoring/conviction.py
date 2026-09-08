"""The one score that was validated on data it was not fitted to.

Where this came from
--------------------
`run_holdout` fitted a composite on 2023-2024 and scored it once on 2025-2026.
Features were selected on a training t-statistic of 2, each one's direction was
taken from the training data rather than from the hypothesis it was written
with, and the result was an equal-weighted mean of within-date percentile ranks.
Out of sample it scored **IC +0.168, t +5.67**, keeping 87% of its training
strength, and 13 of 13 selected features kept their direction.

Everything else in this project was measured on the data it was found in.

Why it is frozen
----------------
`WEIGHTS` below is a transcription of one fit, not a thing to be re-derived.
Four of the thirteen faded to noise out of sample — margin_revision, zone
respect, value and momentum all came back inside t = 2 — and dropping them
would obviously produce a better number. It would also be selection on the test
set, which turns a held-out period back into a training set and destroys the
only honest estimate here. So they stay, at equal weight, exactly as validated.

The same rule forbids tuning the weights, adding a feature that looks good in
the 2025-2026 column, or re-running the fit with a different threshold. If this
is to be improved, it has to be against data that does not exist yet — the
pipeline adds a rebalance a month.

What it is not
--------------
Not a replacement for the gates. A red flag still excludes outright, and this
score has no opinion about a business whose profit never becomes cash.

Not diversified. Eight of the thirteen are the overhead family and they
correlate about 0.17 with each other against 0.03 with everything else, so
+0.168 is roughly one strong effect measured several ways rather than thirteen
independent ones.

Not large. An IC of 0.168 means the ranking is right somewhat more often than
it is wrong. It is a tilt, not a forecast, and it only pays through many
positions held with disciplined sizing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Feature -> direction, exactly as the training fit produced them. The comment
# on each line is its out-of-sample result, recorded so nobody has to re-run
# anything to see which parts are carrying the score and which are ballast.
WEIGHTS: dict[str, int] = {
    "false_breakout": +1,                     # test IC +0.149, t +9.91
    "headroom": -1,                           # test IC +0.167, t +9.87
    "hanging_man_at_resistance": +1,          # test IC +0.046, t +7.47
    "rejected_at_resistance": +1,             # test IC +0.076, t +6.05
    "shooting_star_at_resistance": +1,        # test IC +0.049, t +5.74
    "bearish_engulfing_at_resistance": +1,    # test IC +0.040, t +3.96
    "doji_at_resistance": +1,                 # test IC +0.042, t +3.48
    "ownership_score": -1,                    # test IC +0.055, t +2.97
    "resistance_strength": +1,                # test IC +0.053, t +2.35
    # Faded out of sample. Kept because removing them would be fitting to the
    # held-out period, which is the one thing that must not happen to it.
    "tm_score": +1,                           # test IC +0.053, t +1.76
    "zone_respect": +1,                       # test IC +0.019, t +0.51
    "margin_revision": -1,                    # test IC +0.012, t +0.50
    "value_score": +1,                        # test IC +0.004, t +0.12
}

# The composite was validated on dates with a full cross-section. A stock
# missing most of its inputs has not been scored, it has been guessed at.
MIN_FEATURES = 7

# How long a one-bar event stays true.
#
# Six of the thirteen features are single-bar flags, and four of them fire on
# fewer than 3% of observations. A doji printed yesterday or it did not; today it
# has not, and a different set of names has one instead. Scored that way, only
# **10%** of the top ten survived to the next rebalance — nine names replaced
# every month, on a system whose holding period is six.
#
# That is a construction error rather than a tuning problem. A stock that failed
# a breakout two months ago is still in the situation the signal describes; the
# flag expiring overnight was throwing information away as well as churning the
# list. Carrying each event forward for a quarter takes top-ten survival from
# 10% to about 32% on the training period, and the information coefficient goes
# *up* rather than down.
#
# Sixty-three sessions is a quarter, half the holding period. The improvement
# kept rising out to six months in testing and the number here is deliberately
# not the peak of that curve: this is a fix for turnover, which needs no forward
# returns to justify, and the coefficient is a bonus measured on training data
# alone.
EVENT_MEMORY_SESSIONS = 63

# The flags this applies to. Everything else — a score, a distance, a ratio —
# is already a state and changes gradually on its own.
EVENT_FEATURES = (
    "false_breakout",
    "rejected_at_resistance",
    "doji_at_resistance",
    "hanging_man_at_resistance",
    "shooting_star_at_resistance",
    "bearish_engulfing_at_resistance",
)


def carry_events(history: pd.DataFrame, *, sessions: int = EVENT_MEMORY_SESSIONS) -> pd.DataFrame:
    """Collapse a window of daily rows into one row per symbol, events kept.

    `history` is `ts_setups` over the last `sessions` trading days. An event
    counts if it fired anywhere in the window; every other column is taken from
    the most recent row, because a distance or a strength describes now rather
    than a moment that passed.
    """
    if history.empty:
        return history

    latest = (
        history.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
    )
    for name in EVENT_FEATURES:
        if name not in history:
            continue
        # to_numeric first: the column arrives from JSON as object dtype, where
        # fillna silently downcasts and, in a later pandas, returns something
        # else entirely.
        flag = pd.to_numeric(history[name], errors="coerce").fillna(0.0) > 0
        fired = flag.groupby(history["symbol"]).max()
        latest[name] = fired.reindex(latest.index).fillna(False).astype(bool)
    return latest


def score(frame: pd.DataFrame) -> pd.Series:
    """0-100 conviction for one cross-section, indexed by symbol.

    Ranked within the date before averaging, which is what makes a 0/1 candle
    flag and an unbounded ATR distance commensurable at all. Percentile rather
    than z-score because several inputs are binary and heavily skewed, and a
    z-score on a flag that fires 5% of the time is mostly a statement about the
    other 95%.
    """
    ranks: list[pd.Series] = []
    available = pd.Series(0, index=frame.index, dtype="int64")

    for name, sign in WEIGHTS.items():
        if name not in frame:
            continue
        values = pd.to_numeric(frame[name], errors="coerce") * sign
        if values.notna().sum() == 0:
            continue
        # pct ranks land in (0, 1]; NaN stays NaN so it does not count as median.
        ranks.append(values.rank(pct=True))
        available += values.notna().astype("int64")

    if not ranks:
        return pd.Series(np.nan, index=frame.index, dtype="float64")

    mean = pd.concat(ranks, axis=1).mean(axis=1, skipna=True) * 100.0
    return mean.where(available >= MIN_FEATURES).round(2)


def contributions(row: pd.Series) -> dict[str, float]:
    """Each feature's signed contribution for one stock, for explaining a rank.

    A score nobody can interrogate is a score nobody should act on, and this is
    the difference between "ranked 12th" and "ranked 12th because it failed a
    breakout at a strength-84 level three sessions ago".
    """
    out: dict[str, float] = {}
    for name, sign in WEIGHTS.items():
        value = row.get(name)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        out[name] = float(value) * sign
    return out
