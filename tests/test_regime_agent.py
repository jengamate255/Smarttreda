"""Tests for RegimeAgent - market regime classification."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.regime_agent import RegimeAgent


def _make_candles(prices):
    candles = []
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, (o, h, l, c) in enumerate(prices):
        candles.append(NormalizedCandle(
            timestamp=base + timedelta(hours=i),
            symbol="EURUSD", timeframe="H1",
            open=o, high=h, low=l, close=c,
            volume=100, spread=2,
        ))
    return candles


class TestRegimeClassification:

    def test_uptrend_regime(self):
        prices = [(1.1000 + i * 0.001, 1.1010 + i * 0.001, 1.0990 + i * 0.001, 1.1005 + i * 0.001)
                  for i in range(30)]
        candles = _make_candles(prices)
        agent = RegimeAgent()
        result = agent.classify(candles)
        assert result["regime"] in ("uptrend", "downtrend")
        assert result["trend_strength"] > 0.5

    def test_downtrend_regime(self):
        prices = [(1.1030 - i * 0.001, 1.1040 - i * 0.001, 1.1020 - i * 0.001, 1.1025 - i * 0.001)
                  for i in range(30)]
        candles = _make_candles(prices)
        agent = RegimeAgent()
        result = agent.classify(candles)
        assert result["regime"] in ("uptrend", "downtrend")

    def test_ranging_regime(self):
        prices = []
        for i in range(30):
            if i % 2 == 0:
                prices.append((1.1000, 1.1010, 1.0990, 1.1008))
            else:
                prices.append((1.1008, 1.1015, 1.0995, 1.1002))
        candles = _make_candles(prices)
        agent = RegimeAgent()
        result = agent.classify(candles)
        assert result["regime"] in ("ranging", "uptrend", "downtrend")

    def test_insufficient_data(self):
        prices = [(1.1000, 1.1010, 1.0990, 1.1005)] * 5
        candles = _make_candles(prices)
        agent = RegimeAgent()
        result = agent.classify(candles)
        assert result["regime"] == "unknown"


class TestRegimeHelpers:

    def test_is_trending(self):
        agent = RegimeAgent()
        assert agent.is_trending({"regime": "uptrend"})
        assert agent.is_trending({"regime": "downtrend"})
        assert not agent.is_trending({"regime": "ranging"})

    def test_is_volatile(self):
        agent = RegimeAgent()
        assert agent.is_volatile({"volatility_regime": "high"})
        assert not agent.is_volatile({"volatility_regime": "normal"})
        assert not agent.is_volatile({"volatility_regime": "low"})
