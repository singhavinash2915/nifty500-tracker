"""The technicals frame builder, including split adjustment."""

import numpy as np
import pandas as pd
import pytest

from n500 import technicals
from n500.jobs.compute_technicals import adjusted_frame


def price_frame(n=400, start=100.0, drift=0.001):
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series(start * np.cumprod(1 + np.full(n, drift)), index=dates)
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": pd.Series(np.full(n, 1_000_000.0), index=dates),
        },
        index=dates,
    )


def test_compute_returns_every_expected_column():
    out = technicals.compute(price_frame())
    assert list(out.columns) == technicals.TECHNICAL_COLUMNS
    assert len(out) == 400


def test_compute_on_an_empty_frame_is_empty_not_an_error():
    out = technicals.compute(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    assert out.empty


def test_a_steady_uptrend_produces_a_rising_200dma_and_full_stack():
    out = technicals.compute(price_frame())
    last = out.iloc[-1]
    assert last["sma50"] > last["sma200"]
    assert last["sma200_slope"] > 0
    assert last["dist_52w_high"] == pytest.approx(0.0, abs=0.02)


def test_relative_strength_is_positive_when_beating_the_index():
    stock = price_frame(drift=0.002)
    index = price_frame(drift=0.0005)["close"]
    out = technicals.compute(stock, index_close=index)
    assert out["rs_vs_index"].iloc[-1] > 0


def test_relative_strength_is_negative_when_lagging():
    stock = price_frame(drift=0.0002)
    index = price_frame(drift=0.002)["close"]
    out = technicals.compute(stock, index_close=index)
    assert out["rs_vs_index"].iloc[-1] < 0


def test_relative_strength_is_nan_without_a_benchmark():
    out = technicals.compute(price_frame(), index_close=None)
    assert out["rs_vs_index"].isna().all()


def test_relative_strength_survives_a_missing_index_day():
    stock = price_frame()
    index = price_frame()["close"].drop(price_frame().index[200])
    out = technicals.compute(stock, index_close=index)
    # The stock keeps all its bars; the index is forward-filled across the gap.
    assert len(out) == len(stock)
    assert out["rs_vs_index"].notna().sum() > 0


def test_to_rows_serialises_nan_as_none():
    out = technicals.compute(price_frame(n=250))
    rows = technicals.to_rows("TEST", out)
    assert rows[0]["symbol"] == "TEST"
    # sma200 is not computable on the first row.
    assert rows[0]["sma200"] is None
    assert isinstance(rows[-1]["sma50"], float)


# --- split adjustment -----------------------------------------------------


def test_adjusted_frame_restates_ohlc_and_inverts_volume():
    raw = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "open": [100.0, 51.0],
            "high": [110.0, 52.0],
            "low": [90.0, 50.0],
            "close": [100.0, 51.0],
            "adj_close": [50.0, 51.0],     # a 1:2 split entering day two
            "volume": [1000.0, 2000.0],
        }
    )
    out = adjusted_frame(raw)

    # Pre-split bar is halved on price...
    assert out["close"].iloc[0] == 50.0
    assert out["high"].iloc[0] == 55.0
    assert out["low"].iloc[0] == 45.0
    # ...and doubled on volume, so the 20d/100d ratio compares like with like.
    assert out["volume"].iloc[0] == 2000.0
    # Post-split bar is untouched.
    assert out["close"].iloc[1] == 51.0
    assert out["volume"].iloc[1] == 2000.0


def test_adjusted_frame_is_sorted_by_date():
    raw = pd.DataFrame(
        {
            "date": ["2026-01-03", "2026-01-01"],
            "open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
            "close": [1.0, 1.0], "adj_close": [1.0, 1.0], "volume": [1.0, 1.0],
        }
    )
    out = adjusted_frame(raw)
    assert out.index[0] < out.index[1]


def test_a_split_does_not_show_up_as_a_momentum_crash():
    """End-to-end: the reason adjustment exists at all."""
    n = 300
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = np.full(n, 100.0)
    close[n // 2:] = 50.0             # 1:2 split halfway through
    adj = np.full(n, 50.0)            # correctly restated

    raw = pd.DataFrame(
        {
            "date": dates.astype(str),
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "adj_close": adj, "volume": np.full(n, 1000.0),
        }
    )
    out = technicals.compute(adjusted_frame(raw))
    # Flat adjusted price means a flat 6-month return, not -50%.
    assert out["ret_6m"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
