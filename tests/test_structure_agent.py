"""Tests for StructureAgent - market structure analysis."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.structure_agent import StructureAgent
from mt5_mcp.domain_models import StructureModel


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


class TestStructureAgentSwings:

    def test_swing_high_detection(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0995, 1.1010),
            (1.1010, 1.1030, 1.1005, 1.1025),
            (1.1020, 1.1025, 1.1010, 1.1015),
            (1.1015, 1.1020, 1.1005, 1.1010),
        ]
        candles = _make_candles(prices)
        agent = StructureAgent(swing_lookback=2)
        swing_highs, swing_lows = agent._find_swings(candles)
        assert len(swing_highs) > 0
        assert any(abs(price - 1.1030) < 0.0001 for _, price in swing_highs)

    def test_swing_low_detection(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1010, 1.0985, 1.0990),
            (1.0990, 1.1000, 1.0970, 1.0975),
            (1.0980, 1.0995, 1.0975, 1.0990),
            (1.0990, 1.1005, 1.0985, 1.1000),
        ]
        candles = _make_candles(prices)
        agent = StructureAgent(swing_lookback=2)
        swing_highs, swing_lows = agent._find_swings(candles)
        assert len(swing_lows) > 0
        assert any(abs(price - 1.0970) < 0.0001 for _, price in swing_lows)


class TestStructureAgentClassification:

    def test_uptrend_classification(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0995, 1.1010),
            (1.1010, 1.1030, 1.1005, 1.1025),
            (1.1020, 1.1025, 1.1010, 1.1015),
            (1.1015, 1.1020, 1.1005, 1.1010),
            (1.1010, 1.1015, 1.1000, 1.1005),
            (1.1005, 1.1010, 1.0995, 1.1000),
            (1.1000, 1.1020, 1.0995, 1.1015),
            (1.1015, 1.1040, 1.1010, 1.1035),
            (1.1030, 1.1035, 1.1020, 1.1025),
            (1.1025, 1.1030, 1.1015, 1.1020),
            (1.1020, 1.1045, 1.1015, 1.1040),
        ]
        candles = _make_candles(prices)
        agent = StructureAgent(swing_lookback=2)
        structure = agent.analyze(candles, "EURUSD", "H1")
        assert structure.trend != "unknown"

    def test_downtrend_classification(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1020, 1.1000, 1.1015),
            (1.1015, 1.1020, 1.0998, 1.1000),
            (1.1000, 1.1005, 1.0998, 1.1002),
            (1.1002, 1.1025, 1.1000, 1.1020),
            (1.1020, 1.1022, 1.1010, 1.1012),
            (1.1012, 1.1015, 1.0988, 1.0990),
            (1.0990, 1.0995, 1.0978, 1.0980),
            (1.0980, 1.0985, 1.0972, 1.0975),
            (1.0975, 1.0980, 1.0974, 1.0978),
            (1.0978, 1.1010, 1.0976, 1.1005),
            (1.1005, 1.1008, 1.0985, 1.0988),
            (1.0988, 1.0990, 1.0965, 1.0968),
            (1.0968, 1.0972, 1.0960, 1.0968),
        ]
        candles = _make_candles(prices)
        agent = StructureAgent(swing_lookback=2)
        structure = agent.analyze(candles, "EURUSD", "H1")
        assert structure.trend == "downtrend"


class TestStructureAgentHelpers:

    def test_is_trending(self):
        agent = StructureAgent()
        uptrend = StructureModel(symbol="EURUSD", timeframe="H1", trend="uptrend")
        ranging = StructureModel(symbol="EURUSD", timeframe="H1", trend="ranging")
        assert agent.is_trending(uptrend)
        assert not agent.is_trending(ranging)

    def test_trend_direction(self):
        agent = StructureAgent()
        uptrend = StructureModel(symbol="EURUSD", timeframe="H1", trend="uptrend")
        downtrend = StructureModel(symbol="EURUSD", timeframe="H1", trend="downtrend")
        ranging = StructureModel(symbol="EURUSD", timeframe="H1", trend="ranging")
        assert agent.trend_direction(uptrend) == "bullish"
        assert agent.trend_direction(downtrend) == "bearish"
        assert agent.trend_direction(ranging) == "neutral"

    def test_has_structure_break(self):
        agent = StructureAgent()
        structure = StructureModel(symbol="EURUSD", timeframe="H1")
        assert not agent.has_structure_break(structure)
        structure.set_bos(1.1050)
        assert agent.has_structure_break(structure)
