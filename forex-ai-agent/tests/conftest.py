"""Shared test fixtures: synthetic OHLCV frames and config."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import Config

BASE_CONFIG = {
    "project": {"feature_version": "test-v1"},
    "symbols": ["EURUSD"],
    "timeframes": ["M15"],
    "features": {
        "ema_periods": [20, 50],
        "rsi_period": 14,
        "atr_period": 14,
        "swing_window": 3,
        "htf_context": {"M15": {"H1": "setup", "H4": "regime"}},
    },
    "labels": {
        "stop_mode": "fixed_pips",
        "fixed_stop_pips": 20.0,
        "rr_ratios": [2, 3],
        "primary_rr": 3,
        "max_lookahead_bars": 48,
        "spread_pips": 0.8,
        "commission_pips": 0.2,
    },
    "sessions": {"asian": [0, 8], "london": [7, 16], "new_york": [12, 21]},
    "supply_demand": {"min_impulse_atr": 1.0, "impulse_lookback_bars": 5,
                      "zone_depth_atr": 0.5, "max_touch_distance_atr": 0.5},
    "model": {"splits": {
        "train_start": "2020-01-01", "train_end": "2021-12-31",
        "validation_start": "2022-01-01", "validation_end": "2022-12-31",
        "test_start": "2023-01-01", "test_end": "2023-12-31",
        "walk_forward_start": "2024-01-01", "walk_forward_end": "2099-12-31",
    }},
    "models": {"root": "tmp_test_data/models"},
    "backtest": {"max_simultaneous_trades": 1, "cooldown_bars": 5},
    "risk": {"default_percent": 0.5, "supported_percents": [0.25, 0.5, 1.0],
             "max_daily_loss_percent": 2.0, "max_drawdown_percent": 20.0},
    "data": {"root": "tmp_test_data"},
}


def make_ohlcv(
    n: int = 400,
    seed: int = 7,
    start: str = "2023-01-01",
    timeframe: str = "M15",
    drift: float = 0.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = {"M15": 900, "M5": 300, "H1": 3600, "H4": 14400}[timeframe]
    times = pd.date_range(start, periods=n, freq=f"{dt}s", tz="UTC")
    close = np.exp(np.cumsum(rng.normal(drift / n, 0.0003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    rng2 = np.random.default_rng(seed + 1)
    high = np.maximum(open_, close) + rng2.uniform(0, 0.0002, n)
    low = np.minimum(open_, close) - rng2.uniform(0, 0.0002, n)
    spread = rng2.uniform(0.4, 1.5, n)
    return pd.DataFrame({
        "time": times,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": rng.integers(100, 1000, n),
        "spread": spread,
        "real_volume": np.nan,
    })


def make_deterministic_ohlcv() -> pd.DataFrame:
    """Hand-crafted candles: trend up then down, with a huge bullish impulse."""
    times = pd.date_range("2023-01-01", periods=60, freq="900s", tz="UTC")
    data = []
    price = 1.0000
    for i, t in enumerate(times):
        if i < 30:
            price = 1.0000 + i * 0.0005
            o, c = price - 0.0002, price
        elif i < 35:
            o = price
            price = price + 0.0030
            c = price
        else:
            o = price
            price = price - 0.0004 * (i - 34)
            c = price
        h = max(o, c) + 0.0002
        l = min(o, c) - 0.0002
        data.append({"time": t, "open": o, "high": h, "low": l, "close": c,
                     "tick_volume": 500, "spread": 0.8, "real_volume": np.nan})
    return pd.DataFrame(data)


@pytest.fixture
def cfg() -> Config:
    return Config(data=dict(BASE_CONFIG))


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return make_ohlcv()
