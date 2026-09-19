"""Tests for ZoneAgent - supply/demand zone detection."""

import sys
import os
import math
from datetime import datetime, timezone

# Ensure we import from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.zone_agent import ZoneAgent
from mt5_mcp.domain_models import ZoneModel


def _make_candles(prices: list[tuple[float, float, float, float]]) -> list[NormalizedCandle]:
    """Create candles from (open, high, low, close) tuples."""
    candles = []
    for i, (o, h, l, c) in enumerate(prices):
        candles.append(NormalizedCandle(
            timestamp=datetime(2024, 1, 1, i, tzinfo=timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=o,
            high=h,
            low=l,
            close=c,
            volume=100,
            spread=2,
        ))
    return candles


class TestZoneAgentDetection:
    """Test zone detection."""

    def test_demand_zone_detected(self):
        """Demand zone: base candle + bullish impulse."""
        # Need many small candles to establish avg_body, then a base + large impulse
        # Base candles should be small, impulse should be 2x+ larger
        prices = [
            (1.1000, 1.1005, 1.0995, 1.1003),  # 0 - small
            (1.1003, 1.1008, 1.0998, 1.1006),  # 1 - small
            (1.1006, 1.1011, 1.1001, 1.1009),  # 2 - small
            (1.1009, 1.1014, 1.1004, 1.1012),  # 3 - small
            (1.1012, 1.1017, 1.1007, 1.1015),  # 4 - small
            (1.1015, 1.1018, 1.1012, 1.1014),  # 5 - BASE: tiny body (0.0004), range 0.0006
            (1.1014, 1.1040, 1.1010, 1.1038),  # 6 - BULLISH IMPULSE: body 0.0024, range 0.0030
            (1.1038, 1.1050, 1.1030, 1.1045),  # 7 - follow through
        ]
        candles = _make_candles(prices)
        agent = ZoneAgent()
        zones = agent.detect_zones(candles, "EURUSD", "H1")
        demand_zones = [z for z in zones if z.type == "demand"]
        assert len(demand_zones) > 0, f"No demand zones detected. Zones found: {len(zones)}"

    def test_supply_zone_detected(self):
        """Supply zone: base candle + bearish impulse."""
        prices = [
            (1.1000, 1.1005, 1.0995, 1.1003),  # 0 - small
            (1.1003, 1.1008, 1.0998, 1.1006),  # 1 - small
            (1.1006, 1.1011, 1.1001, 1.1009),  # 2 - small
            (1.1009, 1.1014, 1.1004, 1.1012),  # 3 - small
            (1.1012, 1.1017, 1.1007, 1.1015),  # 4 - small
            (1.1015, 1.1018, 1.1012, 1.1014),  # 5 - BASE: tiny body
            (1.1014, 1.1018, 1.0990, 1.0992),  # 6 - BEARISH IMPULSE: body 0.0022, range 0.0028
            (1.0992, 1.1000, 1.0980, 1.0985),  # 7 - follow through
        ]
        candles = _make_candles(prices)
        agent = ZoneAgent()
        zones = agent.detect_zones(candles, "EURUSD", "H1")
        supply_zones = [z for z in zones if z.type == "supply"]
        assert len(supply_zones) > 0, f"No supply zones detected. Zones found: {len(zones)}"

    def test_no_zone_small_body(self):
        """No zone when body is too large (not a base)."""
        prices = [
            (1.1000, 1.1005, 1.0995, 1.1003),  # 0 - small
            (1.1003, 1.1008, 1.0998, 1.1006),  # 1 - small
            (1.1006, 1.1011, 1.1001, 1.1009),  # 2 - small
            (1.1009, 1.1014, 1.1004, 1.1012),  # 3 - small
            (1.1000, 1.1050, 1.0950, 1.1045),  # 4 - LARGE body (not base)
            (1.1045, 1.1090, 1.1040, 1.1085),  # 5 - Bullish impulse
        ]
        candles = _make_candles(prices)
        agent = ZoneAgent()
        zones = agent.detect_zones(candles, "EURUSD", "H1")
        # Should not detect demand zone since candle 4 is not a base
        assert len(zones) == 0

    def test_empty_candles(self):
        """No zones with insufficient data."""
        agent = ZoneAgent()
        zones = agent.detect_zones([], "EURUSD", "H1")
        assert zones == []


class TestZoneAgentQuality:
    """Test zone quality scoring."""

    def test_quality_score_near_zone(self):
        """Higher score when price is near zone."""
        zone = ZoneModel(
            symbol="EURUSD",
            timeframe="H1",
            zone_high=1.1050,
            zone_low=1.1000,
            type="demand",
        )
        agent = ZoneAgent()
        score = agent.zone_quality_score(zone, current_price=1.1025)
        assert score >= 0.7  # Should score well - price is within zone

    def test_quality_score_far_zone(self):
        """Lower score when price is far from zone."""
        zone = ZoneModel(
            symbol="EURUSD",
            timeframe="H1",
            zone_high=1.1050,
            zone_low=1.1000,
            type="demand",
        )
        agent = ZoneAgent()
        score = agent.zone_quality_score(zone, current_price=1.1500)
        assert score <= 0.7  # Should score lower - price is far


class TestZoneAgentDeduplication:
    """Test zone deduplication."""

    def test_dedupe_overlapping_zones(self):
        """Overlapping zones of same type should be merged."""
        prices = [
            (1.1000, 1.1005, 1.0995, 1.1003),  # 0 - small
            (1.1003, 1.1008, 1.0998, 1.1006),  # 1 - small
            (1.1006, 1.1011, 1.1001, 1.1009),  # 2 - small
            (1.1009, 1.1014, 1.1004, 1.1012),  # 3 - small
            (1.1012, 1.1017, 1.1007, 1.1015),  # 4 - small
            (1.1015, 1.1018, 1.1012, 1.1014),  # 5 - BASE
            (1.1014, 1.1040, 1.1010, 1.1038),  # 6 - IMPULSE
            (1.1038, 1.1041, 1.1035, 1.1037),  # 7 - BASE
            (1.1037, 1.1060, 1.1033, 1.1058),  # 8 - IMPULSE
        ]
        candles = _make_candles(prices)
        agent = ZoneAgent()
        zones = agent.detect_zones(candles, "EURUSD", "H1")
        # Deduplication should reduce duplicates
        assert len(zones) <= 3
