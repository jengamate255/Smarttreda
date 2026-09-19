"""Tests for normalized market data schema."""

from __future__ import annotations

from datetime import datetime, timezone
from mt5_mcp.data_schema import (
    NormalizedCandle,
    SymbolInfo,
    DataQualityReport,
    ALL_TIMEFRAMES,
    TIMEFRAME_H1,
    TIMEFRAME_D1,
    TIMEFRAME_W1,
    CANDLE_FIELDS,
)


def test_normalized_candle_creation():
    """Test creating a NormalizedCandle with valid data."""
    candle = NormalizedCandle(
        timestamp=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe="H1",
        open=1.0800,
        high=1.0850,
        low=1.0790,
        close=1.0825,
        volume=1000,
        spread=1.5,
    )
    assert candle.symbol == "EURUSD"
    assert candle.timeframe == "H1"
    assert candle.is_bullish is True  # close > open
    assert candle.is_bearish is False


def test_normalized_candle_bearish():
    """Test creating a bearish candle."""
    candle = NormalizedCandle(
        timestamp=datetime.now(timezone.utc),
        symbol="EURUSD",
        timeframe="H1",
        open=1.0850,
        high=1.0870,
        low=1.0800,
        close=1.0810,
        volume=1000,
        spread=1.5,
    )
    assert candle.is_bullish is False
    assert candle.is_bearish is True


def test_normalized_candle_invalid_high():
    """Test that invalid high raises ValueError."""
    try:
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0850,
            high=1.0800,  # high below open and close - invalid
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_normalized_candle_invalid_low():
    """Test that invalid low raises ValueError."""
    try:
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0850,
            low=1.0880,  # low above open and close - invalid
            close=1.0825,
            volume=1000,
            spread=1.5,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_symbol_info():
    """Test SymbolInfo creation."""
    info = SymbolInfo(
        symbol="EURUSD",
        asset_class="forex",
        point=0.00001,
        digits=5,
        trade_mode=0,
        filling_mode=2,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        tick_size=0.00001,
        tick_value=1.0,
        contract_size=100000,
    )
    assert info.symbol == "EURUSD"
    assert info.point == 0.00001


def test_data_quality_report():
    """Test DataQualityReport creation."""
    report = DataQualityReport(
        symbol="EURUSD",
        timeframe="H1",
        is_valid=True,
    )
    assert report.symbol == "EURUSD"
    assert report.timeframe == "H1"
    assert report.is_valid is True


def test_data_quality_report_invalid():
    """Test DataQualityReport with issues."""
    report = DataQualityReport(
        symbol="EURUSD",
        timeframe="H1",
        is_valid=False,
        issues=["bad timestamp", "duplicate candle"],
        missing_candles=5,
        duplicate_candles=2,
    )
    assert report.is_valid is False
    assert len(report.issues) == 2


def test_all_timeframes():
    """Test that all timeframe constants are defined."""
    from mt5_mcp.data_schema import TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_H1
    assert TIMEFRAME_M1 == "M1"
    assert TIMEFRAME_M5 == "M5"
    assert TIMEFRAME_H1 == "H1"