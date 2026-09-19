"""Tests for PriceActionAgent - pattern detection."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.price_action_agent import PriceActionAgent


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


class TestPinBarDetection:

    def test_bullish_pin_bar(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0990, 1.1008),
            (1.1008, 1.1020, 1.0980, 1.1015),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "pin_bar"
        assert result.is_bullish

    def test_bearish_pin_bar(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.0995, 1.1000, 1.0985, 1.0992),
            (1.0992, 1.1040, 1.0988, 1.0995),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "pin_bar"
        assert result.is_bearish

    def test_no_pin_bar_large_body(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0995, 1.1010),
            (1.1000, 1.1030, 1.0980, 1.1028),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        if result is not None:
            assert result.pattern != "pin_bar"


class TestEngulfingDetection:

    def test_bullish_engulfing(self):
        prices = [
            (1.1010, 1.1015, 1.0995, 1.0998),
            (1.1010, 1.1015, 1.0995, 1.0998),
            (1.0995, 1.1020, 1.0990, 1.1015),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "engulfing"
        assert result.is_bullish

    def test_bearish_engulfing(self):
        prices = [
            (1.0995, 1.1010, 1.0990, 1.1008),
            (1.0995, 1.1010, 1.0990, 1.1008),
            (1.1010, 1.1015, 1.0985, 1.0988),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "engulfing"
        assert result.is_bearish


class TestShootingStarDetection:

    def test_shooting_star(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0995, 1.1010),
            (1.1002, 1.1040, 1.0998, 1.1005),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern in ("shooting_star", "pin_bar")
        assert result.is_bearish


class TestInsideBarDetection:

    def test_inside_bar(self):
        prices = [
            (1.1000, 1.1020, 1.0980, 1.1015),
            (1.1010, 1.1015, 1.0985, 1.1010),
            (1.1005, 1.1012, 1.0995, 1.1008),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "inside_bar"


class TestBreakoutDetection:

    def test_bullish_breakout(self):
        prices = [
            (1.1000, 1.1020, 1.0980, 1.1015),
            (1.1010, 1.1018, 1.0982, 1.1012),
            (1.1015, 1.1035, 1.1010, 1.1030),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "breakout"
        assert result.is_bullish

    def test_bearish_breakout(self):
        prices = [
            (1.1000, 1.1020, 1.0980, 1.1015),
            (1.1010, 1.1018, 1.0982, 1.1012),
            (1.1010, 1.1020, 1.0970, 1.0975),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is not None
        assert result.pattern == "breakout"
        assert result.is_bearish


class TestPriceActionEdgeCases:

    def test_insufficient_candles(self):
        candles = _make_candles([(1.1000, 1.1010, 1.0990, 1.1005)])
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is None

    def test_zero_range_candle(self):
        prices = [
            (1.1000, 1.1010, 1.0990, 1.1005),
            (1.1005, 1.1015, 1.0995, 1.1010),
            (1.1005, 1.1005, 1.1005, 1.1005),
        ]
        candles = _make_candles(prices)
        agent = PriceActionAgent()
        result = agent.analyze(candles, "EURUSD", "H1")
        assert result is None or result.pattern == "inside_bar"
