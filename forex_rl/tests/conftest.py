"""Shared test fixtures for forex_rl test suite."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import yaml
from pathlib import Path

from forex_rl.src.environment.forex_env import EnvConfig, ForexTradingEnv


@pytest.fixture(scope="session")
def synthetic_ohlcv() -> pd.DataFrame:
    """Generate 2000-bar synthetic OHLCV DataFrame with UTC DatetimeIndex."""
    rng = np.random.default_rng(42)
    n = 2000
    base_price = 1.1000
    vol = 0.0005
    dt = 1.0 / 252.0

    closes = np.empty(n, dtype=np.float64)
    closes[0] = base_price
    for i in range(1, n):
        closes[i] = closes[i - 1] * np.exp(
            vol * np.sqrt(dt) * rng.standard_normal()
        )

    intra_vol = vol * np.sqrt(dt) * 0.5
    highs = closes * (1.0 + np.abs(rng.standard_normal(n)) * intra_vol)
    lows = closes * (1.0 - np.abs(rng.standard_normal(n)) * intra_vol)
    opens = np.empty(n, dtype=np.float64)
    opens[0] = closes[0]
    opens[1:] = closes[:-1] * (1.0 + rng.standard_normal(n - 1) * intra_vol * 0.3)
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))
    volumes = rng.integers(100, 5000, size=n).astype(np.float64)

    idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    idx = idx.tz_localize(None)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )
    df.index.name = "time"
    return df


@pytest.fixture(scope="session")
def sample_config() -> dict:
    """Load the default config.yaml."""
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def sample_env(synthetic_ohlcv: pd.DataFrame) -> ForexTradingEnv:
    """Create a ForexTradingEnv with synthetic data."""
    cfg = EnvConfig(
        lookback=64,
        initial_balance=10000.0,
        risk_per_trade=0.005,
        max_positions=3,
        max_episode_steps=200,
    )
    return ForexTradingEnv(synthetic_ohlcv, config=cfg)
