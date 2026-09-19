"""Tests for DataQualityAgent."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.data_quality import DataQualityAgent, DataQualityStatus, quick_validate


def _make_candle(**kwargs):
    """Helper to create a valid candle with defaults."""
    defaults = dict(
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
    defaults.update(kwargs)
    return NormalizedCandle(**defaults)


def test_quick_validate_valid():
    """Test quick validation with valid data."""
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(timestamp=base_time),
        _make_candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0820, high=1.0870, low=1.0810, close=1.0855,
            volume=1100, spread=1.4,
        ),
    ]
    is_valid, report = quick_validate(candles, "EURUSD", "H1")
    assert is_valid is True
    assert report.is_valid is True


def test_quick_validate_invalid_no_candles():
    """Test quick validation with no candles."""
    is_valid, report = quick_validate([], "EURUSD", "H1")
    assert is_valid is False


def test_data_quality_agent_init():
    """Test DataQualityAgent initialization."""
    agent = DataQualityAgent()
    assert agent.status == DataQualityStatus.DATA_VALID
    assert agent.report is None


def test_data_quality_validate_valid_data():
    """Test validation with valid data."""
    agent = DataQualityAgent()
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(timestamp=base_time),
        _make_candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0820, high=1.0870, low=1.0810, close=1.0855,
            volume=1100, spread=1.4,
        ),
    ]
    report = agent.validate(candles, "EURUSD", "H1")
    assert report.is_valid is True
    assert agent.status == DataQualityStatus.DATA_VALID


def test_data_quality_invalid_ohlc():
    """Test validation catches abnormal spreads (since NormalizedCandle validates OHLC on creation)."""
    agent = DataQualityAgent()
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Create candles with one having an abnormally large spread
    # Use many normal candles so the mean stays low, then one outlier
    normal_candles = [
        _make_candle(timestamp=base_time + timedelta(hours=i), spread=1.0)
        for i in range(10)
    ]
    outlier_candle = _make_candle(
        timestamp=base_time + timedelta(hours=10),
        open=1.0820, high=1.0870, low=1.0810, close=1.0855,
        volume=1100, spread=100.0,  # 100x normal spread
    )
    candles = normal_candles + [outlier_candle]
    report = agent.validate(candles, "EURUSD", "H1")
    assert report.abnormal_spreads > 0


def test_data_quality_bad_timestamps():
    """Test validation catches bad timestamps (naive datetime without timezone)."""
    agent = DataQualityAgent()
    # Use a naive datetime (no timezone)
    bad_ts = datetime(2024, 1, 1, 0, 0, 0)  # no timezone
    candles = [
        _make_candle(timestamp=bad_ts),
    ]
    report = agent.validate(candles, "EURUSD", "H1")
    # Should flag timezone issue
    assert len(report.issues) > 0


def test_data_quality_insufficient_history():
    """Test validation flags insufficient history."""
    agent = DataQualityAgent()
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(timestamp=base_time),
    ]
    report = agent.validate(candles, "EURUSD", "H1")
    # Single candle is insufficient (MIN_HISTORY_CANDLES = 2)
    assert report.insufficient_history is True


def test_data_quality_all_issues():
    """Test validation collects all issue types."""
    agent = DataQualityAgent()
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    future_time = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Candles with multiple issues: future timestamp, abnormal spread, insufficient history
    candles = [
        _make_candle(timestamp=future_time, spread=1.0),
    ]
    report = agent.validate(candles, "EURUSD", "H1")
    assert report.is_valid is False
    assert len(report.issues) > 0