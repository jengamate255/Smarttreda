from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone, time

from trading_ai.config.settings import SESSIONS, Timeframe


def _make_ohlcv(prices: list[float], timeframe_seconds: int = 3600) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(prices)
    dates = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=n,
        freq=f"{timeframe_seconds}s",
    )
    closes = np.array(prices)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + np.abs(closes - opens) * 0.3 + 0.0001
    lows = np.minimum(opens, closes) - np.abs(closes - opens) * 0.3 - 0.0001
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.ones(n) * 100,
    }, index=dates)


def _detect_swings(df: pd.DataFrame, lookback: int = 5) -> dict:
    """Detect swing highs and lows."""
    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(df) - lookback):
        if all(highs[i] > highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, lookback + 1)):
            swing_highs.append((df.index[i], highs[i]))
        if all(lows[i] < lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, lookback + 1)):
            swing_lows.append((df.index[i], lows[i]))
    return {"highs": swing_highs, "lows": swing_lows}


def _detect_bos(swings: dict) -> str | None:
    """Detect break of structure from swing points."""
    if len(swings["highs"]) < 2 or len(swings["lows"]) < 2:
        return None
    recent_highs = [h[1] for h in swings["highs"][-2:]]
    recent_lows = [l[1] for l in swings["lows"][-2:]]
    if recent_highs[-1] > recent_highs[-2]:
        return "BULLISH_BOS"
    if recent_lows[-1] < recent_lows[-2]:
        return "BEARISH_BOS"
    return None


def _classify_volatility(df: pd.DataFrame, window: int = 20) -> str:
    """Classify volatility regime based on ATR relative to historical."""
    if len(df) < window + 1:
        return "unknown"
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    current_atr = atr.iloc[-1]
    mean_atr = atr.mean()
    std_atr = atr.std()
    if current_atr > mean_atr + 2 * std_atr:
        return "high"
    if current_atr < mean_atr - std_atr:
        return "low"
    return "normal"


def _classify_session(utc_time: datetime) -> list[str]:
    """Classify which trading session(s) a UTC time falls into."""
    t = utc_time.time()
    return [name for name, window in SESSIONS.items() if window.contains(t)]


class TestSwingDetection:
    def test_finds_swing_highs(self):
        prices = [1.1000 + i * 0.001 for i in range(20)] + \
                 [1.1020 - i * 0.001 for i in range(20)]
        df = _make_ohlcv(prices)
        swings = _detect_swings(df, lookback=3)
        assert len(swings["highs"]) >= 1

    def test_finds_swing_lows(self):
        prices = [1.1050 - i * 0.001 for i in range(20)] + \
                 [1.1030 + i * 0.001 for i in range(20)]
        df = _make_ohlcv(prices)
        swings = _detect_swings(df, lookback=3)
        assert len(swings["lows"]) >= 1

    def test_flat_market_fewer_swings(self):
        prices = [1.1000] * 50
        df = _make_ohlcv(prices)
        swings = _detect_swings(df, lookback=5)
        assert len(swings["highs"]) == 0
        assert len(swings["lows"]) == 0


class TestBOSDetection:
    def test_bullish_bos(self):
        swings = {
            "highs": [("t1", 1.10), ("t2", 1.11), ("t3", 1.12)],
            "lows": [("t1", 1.09), ("t2", 1.08)],
        }
        assert _detect_bos(swings) == "BULLISH_BOS"

    def test_bearish_bos(self):
        swings = {
            "highs": [("t1", 1.12), ("t2", 1.11)],
            "lows": [("t1", 1.09), ("t2", 1.08), ("t3", 1.07)],
        }
        assert _detect_bos(swings) == "BEARISH_BOS"

    def test_no_bos(self):
        swings = {
            "highs": [("t1", 1.10), ("t2", 1.10)],
            "lows": [("t1", 1.09), ("t2", 1.09)],
        }
        assert _detect_bos(swings) is None

    def test_insufficient_data(self):
        assert _detect_bos({"highs": [("t1", 1.10)], "lows": []}) is None


class TestVolatilityClassification:
    def test_high_volatility(self):
        normal = np.random.default_rng(42).normal(0, 0.0005, 50)
        prices = np.cumsum(normal) + 1.1000
        df = _make_ohlcv(list(prices))
        # Inject a spike at the end
        df.iloc[-1, df.columns.get_loc("high")] = df["close"].iloc[-1] + 0.01
        df.iloc[-1, df.columns.get_loc("low")] = df["close"].iloc[-1] - 0.01
        result = _classify_volatility(df, window=10)
        assert result in ("high", "normal")

    def test_low_volatility(self):
        prices = [1.1000 + i * 0.00001 for i in range(50)]
        df = _make_ohlcv(prices)
        result = _classify_volatility(df, window=10)
        assert result in ("low", "normal")

    def test_insufficient_data(self):
        prices = [1.1000, 1.1001, 1.1002]
        df = _make_ohlcv(prices)
        result = _classify_volatility(df, window=20)
        assert result == "unknown"


class TestSessionClassification:
    def test_asian_session(self):
        t = datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)
        sessions = _classify_session(t)
        assert "asian" in sessions

    def test_london_session(self):
        t = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        sessions = _classify_session(t)
        assert "london" in sessions

    def test_new_york_session(self):
        t = datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc)
        sessions = _classify_session(t)
        assert "new_york" in sessions

    def test_overlap_session(self):
        t = datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc)
        sessions = _classify_session(t)
        assert "overlap" in sessions

    def test_no_session(self):
        t = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)
        sessions = _classify_session(t)
        assert "asian" not in sessions
        assert "london" not in sessions
        assert "new_york" not in sessions
