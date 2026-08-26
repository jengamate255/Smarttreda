"""Tests for the technical indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from helpers import build_frame

from indicators import (
    atr,
    bollinger,
    ema,
    macd,
    rsi,
    sma,
    swing_highs,
    swing_lows,
    vwap,
)


def test_sma_known_value():
    frame = build_frame([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert sma(frame["close"], 5).iloc[-1] == pytest.approx(8.0)
    assert sma(frame["close"], 2).iloc[-1] == pytest.approx(9.5)


def test_ema_matches_pandas_ewm():
    frame = build_frame([float(i) for i in range(1, 51)])
    expected = frame["close"].ewm(span=10, adjust=False, min_periods=1).mean()
    assert ema(frame["close"], 10).iloc[-1] == pytest.approx(expected.iloc[-1])


def test_rsi_bounds_and_extremes():
    rising = build_frame([1.0 + i * 0.1 for i in range(30)])
    assert rsi(rising["close"], 14).iloc[-1] == pytest.approx(100.0)
    falling = build_frame([10.0 - i * 0.1 for i in range(30)])
    assert rsi(falling["close"], 14).iloc[-1] == pytest.approx(0.0)
    flat = build_frame([1.0] * 30)
    assert 0.0 <= rsi(flat["close"], 14).iloc[-1] <= 100.0


def test_atr_flat_series_is_zero():
    frame = build_frame([1.5] * 40, wick=0.0000)
    assert atr(frame).iloc[-1] == 0.0


def test_macd_shape():
    frame = build_frame([1.0 + i * 0.001 for i in range(100)])
    result = macd(frame["close"])
    assert list(result.columns) == ["macd", "signal", "histogram"]
    assert result["histogram"].iloc[-1] == pytest.approx(
        result["macd"].iloc[-1] - result["signal"].iloc[-1]
    )


def test_bollinger_ordering():
    frame = build_frame([1.0 + (i % 5) * 0.01 for i in range(100)])
    bands = bollinger(frame["close"])
    assert (bands["upper"] >= bands["middle"]).all()
    assert (bands["middle"] >= bands["lower"]).all()


def test_vwap_constant_price():
    frame = build_frame([2.0] * 50, wick=0.0)
    assert vwap(frame).iloc[-1] == pytest.approx(2.0)


def test_swings_on_zigzag():

    closes = []
    for k in range(10):
        closes.append(1.0 + k * 0.05)
    closes.append(1.44)  # gap-down reversal bar
    for k in range(8):
        closes.append(1.44 - (k + 1) * 0.05)
    closes.append(0.96)  # gap-up reversal bar
    for k in range(8):
        closes.append(0.96 + (k + 1) * 0.05)
    frame = build_frame(closes, wick=0.0)
    frame.loc[9, "high"] = 1.46  # peak bar wick above both neighbours
    frame.loc[19, "low"] = 0.94  # trough bar wick below both neighbours
    highs = swing_highs(frame, left=3, right=3)
    lows = swing_lows(frame, left=3, right=3)
    assert highs == [9]
    assert lows == [19]


def _naive_swings(values, left, right, find_max):
    n = len(values)
    out = []
    for i in range(left, n - right):
        window = values[i - left : i + right + 1]
        extreme = window.max() if find_max else window.min()
        unique = (window == extreme).sum() == 1
        if (find_max and values[i] == extreme and unique) or (
            not find_max and values[i] == extreme and unique
        ):
            out.append(i)
    return out


def test_vectorized_swings_match_naive():
    rng = np.random.default_rng(11)
    n = 200
    high = 100 + np.cumsum(rng.normal(0, 0.6, n)) + rng.uniform(0, 0.5, n)
    low = high - rng.uniform(0.2, 0.9, n)
    df = pd.DataFrame({"high": high, "low": low})
    for left, right in [(5, 5), (3, 5), (5, 3), (1, 1), (8, 2)]:
        assert swing_highs(df, left, right) == _naive_swings(high, left, right, True)
        assert swing_lows(df, left, right) == _naive_swings(low, left, right, False)


def test_swings_empty_when_too_short():
    df = pd.DataFrame({"high": [1.0, 2.0, 3.0], "low": [0.9, 1.9, 2.9]})
    assert swing_highs(df, 5, 5) == []
    assert swing_lows(df, 5, 5) == []
