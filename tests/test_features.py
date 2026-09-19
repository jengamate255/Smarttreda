"""Tests for feature engine - deterministic feature computation."""

from __future__ import annotations

import numpy as np
from datetime import datetime, timezone
from mt5_mcp.features import (
    returns,
    log_returns,
    atr,
    rolling_volatility,
    range_pct,
    true_range,
    session_range,
    previous_day_high_low,
    previous_week_high_low,
    swing_points,
    vwap,
    FeatureBag,
)
from mt5_mcp.data_schema import NormalizedCandle


def test_returns():
    """Test simple returns feature."""
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
    r = returns(candle)
    expected = (1.0825 - 1.0800) / 1.0800
    assert abs(r - expected) < 1e-10


def test_log_returns():
    """Test log returns feature."""
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
    lr = log_returns(candle, previous_close=1.0800)
    expected = np.log(1.0825 / 1.0800)
    assert abs(lr - expected) < 1e-10


def test_atr():
    """Test Average True Range."""
    tr = atr(high=1.0850, low=1.0790, close=1.0825, prev_close=1.0800)
    # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    tr1 = 1.0850 - 1.0790  # 0.006
    tr2 = abs(1.0850 - 1.0800)  # 0.005
    tr3 = abs(1.0790 - 1.0800)  # 0.001
    expected = max(tr1, tr2, tr3)
    assert abs(tr - expected) < 1e-10


def test_range_pct():
    """Test range percentage feature."""
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
    rp = range_pct(candle)
    body = abs(1.0825 - 1.0800)  # 0.0025
    tr = 1.0850 - 1.0790  # 0.006
    expected = body / tr
    assert abs(rp - expected) < 1e-10


def test_true_range():
    """Test true range component."""
    tr = true_range(high=1.0850, low=1.0790, prev_close=1.0800)
    tr1 = 1.0850 - 1.0790  # 0.006
    tr2 = abs(1.0850 - 1.0800)  # 0.005
    tr3 = abs(1.0790 - 1.0800)  # 0.001
    expected = max(tr1, tr2, tr3)
    assert abs(tr - expected) < 1e-10


def test_feature_bag_creation():
    """Test FeatureBag creation and update."""
    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800 + i * 0.001,
            high=1.0850 + i * 0.001,
            low=1.0790 + i * 0.001,
            close=1.0825 + i * 0.001,
            volume=1000 + i * 10,
            spread=1.5,
        )
        for i in range(5)
    ]

    bag = FeatureBag()
    bag.update_from_series(candles)

    # Check features were computed
    assert bag.volatility_annual >= 0
    assert bag.range_pct >= 0  # noqa: E701 - body room
    assert bag.prev_day_high >= 0
    assert bag.prev_week_high >= 0
    assert bag.swing_high >= 0
    assert bag.vwap >= 0
    assert bag.volume_feature >= 0


def test_previous_day_high_low():
    """Test previous day high/low detection."""
    candles = [
        NormalizedCandle(
            timestamp=datetime(2024, 1, 1, 23, 0, 0, tzinfo=timezone.utc),
            symbol="EURUSD",
            timeframe="D1",
            open=1.0800,
            high=1.0850,
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
        ),
        NormalizedCandle(
            timestamp=datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc),
            symbol="EURUSD",
            timeframe="D1",
            open=1.0820,
            high=1.0870,
            low=1.0810,
            close=1.0855,
            volume=1200,
            spread=1.4,
        ),
    ]

    high, low = previous_day_high_low(candles)
    # With 2 D1 candles, the function returns d1_candles[-2] (first candle) as "previous"
    assert high == 1.0850
    assert low == 1.0790


def test_swing_points():
    """Test swing high/low detection."""
    # Create a series with a clear swing high in the middle
    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0810,
            low=1.0790,
            close=1.0805,
            volume=1000,
            spread=1.5,
        ),
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0805,
            high=1.0850,  # swing high
            low=1.0800,
            close=1.0820,
            volume=1100,
            spread=1.5,
        ),
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0820,
            high=1.0830,
            low=1.0810,
            close=1.0815,
            volume=950,
            spread=1.5,
        ),
    ]

    sp = swing_points(candles, lookback=1)
    # The middle candle should be identified as swing high
    assert sp["swing_high"] > 0
    assert sp["swing_low"] >= 0


def test_vwap():
    """Test VWAP calculation."""
    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0850,
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
            real_volume=5000,
        ),
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0820,
            high=1.0870,
            low=1.0810,
            close=1.0850,
            volume=1200,
            spread=1.4,
            real_volume=6000,
        ),
    ]

    v = vwap(candles)
    assert v > 0
    assert v < 1.1  # Sanity check for EURUSD


def test_rolling_volatility():
    """Test rolling volatility computation."""
    # Create trending price series with valid OHLC
    closes = [1.0800 + i * 0.0005 for i in range(50)]
    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=c - 0.0002,
            high=c + 0.0003,
            low=c - 0.0003,
            close=c,
            volume=1000,
            spread=1.5,
        )
        for c in closes
    ]

    rv = rolling_volatility(candles, window=20)
    assert rv > 0
    # Annualized vol of a trending series should be reasonable
    assert rv < 2.0  # 200% annualized vol is very high


def test_session_range():
    """Test session range calculation."""
    candles = [
        NormalizedCandle(
            timestamp=datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc),  # London open
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0850,
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
        ),
        NormalizedCandle(
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),  # London midday
            symbol="EURUSD",
            timeframe="H1",
            open=1.0820,
            high=1.0860,
            low=1.0810,
            close=1.0845,
            volume=1100,
            spread=1.4,
        ),
        NormalizedCandle(
            timestamp=datetime(2024, 1, 1, 16, 0, 0, tzinfo=timezone.utc),  # London close
            symbol="EURUSD",
            timeframe="H1",
            open=1.0840,
            high=1.0870,
            low=1.0830,
            close=1.0855,
            volume=1050,
            spread=1.5,
        ),
    ]

    sr = session_range(candles, session_hours=(8, 17))
    assert sr > 0