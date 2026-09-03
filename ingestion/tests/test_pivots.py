"""SPL / SPH pivots — a faithful port of structural_poc.pine.

The first test is hand-traced bar by bar against the Pine source; if the port
drifts, the screener's zones stop matching the TradingView chart and every
disagreement becomes undebuggable.
"""

import pandas as pd
import pytest

from n500.zones.pivots import (
    PivotKind,
    find_pivots,
    resistance_pivots,
    support_pivots,
    visible_at,
)


def frame(bars):
    """bars: list of (high, low, close)."""
    dates = pd.bdate_range("2026-01-01", periods=len(bars))
    return pd.DataFrame(
        {
            "high": [b[0] for b in bars],
            "low": [b[1] for b in bars],
            "close": [b[2] for b in bars],
        },
        index=dates,
    )


HAND_TRACED = [
    (10, 8, 9),     # 0 anchor
    (11, 9, 10),    # 1 qualifies for SPL (high>10, close>9) -> count 1
    (9, 7, 8),      # 2 does not qualify; range low becomes 7
    (12, 9, 11),    # 3 qualifies -> count 2 -> SPL at the range low (7 @ bar 2)
    (11, 8, 10),    # 4 qualifies for SPH (low<9, close<11) -> count 1
    (13, 9, 12),    # 5 does not qualify (low 9 not < 9); range high becomes 13
    (10, 7, 9),     # 6 qualifies -> count 2 -> SPH at the range high (13 @ bar 5)
]


def test_hand_traced_sequence():
    pivots = find_pivots(frame(HAND_TRACED))
    assert len(pivots) == 2

    spl, sph = pivots
    assert spl.kind is PivotKind.SPL
    assert (spl.index, spl.price, spl.confirmed_index) == (2, 7.0, 3)

    assert sph.kind is PivotKind.SPH
    assert (sph.index, sph.price, sph.confirmed_index) == (5, 13.0, 6)


def test_two_qualifying_bars_need_not_be_consecutive():
    """The counter does not reset on a non-qualifying bar — bar 2 sits between
    the two qualifiers and must not break the count."""
    pivots = find_pivots(frame(HAND_TRACED))
    assert pivots[0].confirmed_index == 3


def test_one_qualifying_bar_confirms_nothing():
    pivots = find_pivots(frame([(10, 8, 9), (11, 9, 10), (9, 7, 8)]))
    assert pivots == []


def test_pivots_strictly_alternate():
    bars = HAND_TRACED + [
        (14, 9, 13),   # qualifies for SPL again
        (8, 6, 7),
        (15, 10, 14),  # second qualifier -> SPL
    ]
    kinds = [p.kind for p in find_pivots(frame(bars))]
    assert kinds == [PivotKind.SPL, PivotKind.SPH, PivotKind.SPL]


def test_a_pivot_is_confirmed_after_it_happened():
    """The heart of the point-in-time rule: the low printed at bar 2, but
    nothing knew it was a pivot until bar 3."""
    spl = find_pivots(frame(HAND_TRACED))[0]
    assert spl.index < spl.confirmed_index
    assert spl.bars_to_confirm == 1


def test_visible_at_hides_unconfirmed_pivots():
    pivots = find_pivots(frame(HAND_TRACED))
    # At bar 2 the low exists but has not been confirmed by Bar2 yet.
    assert visible_at(pivots, 2) == []
    assert len(visible_at(pivots, 3)) == 1
    assert len(visible_at(pivots, 6)) == 2


def test_support_and_resistance_split():
    pivots = find_pivots(frame(HAND_TRACED))
    assert len(support_pivots(pivots)) == 1
    assert len(resistance_pivots(pivots)) == 1


def test_a_flat_series_produces_no_pivots():
    assert find_pivots(frame([(10, 8, 9)] * 30)) == []


def test_a_pure_uptrend_produces_only_spls():
    # Every bar makes a higher high and higher close, so the SPH search never
    # gets a qualifying bar and the sequence stalls after the first SPL.
    bars = [(10 + i, 8 + i, 9 + i) for i in range(20)]
    kinds = [p.kind for p in find_pivots(frame(bars))]
    assert kinds == [PivotKind.SPL]


def test_too_short_a_frame_is_empty_not_an_error():
    assert find_pivots(frame([(10, 8, 9)])) == []


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="high"):
        find_pivots(pd.DataFrame({"close": [1.0, 2.0]}))


def test_start_offset_moves_the_anchor():
    # Anchoring at bar 3 makes bar 3 the reference, so the earlier structure
    # is ignored entirely.
    late = find_pivots(frame(HAND_TRACED), start=3)
    assert all(p.index >= 3 for p in late)
