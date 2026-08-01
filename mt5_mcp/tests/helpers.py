"""Synthetic candle series builders shared by tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def build_frame(
    closes: list[float],
    wick: float = 0.0002,
    volume: int = 100,
    start: datetime | None = None,
) -> pd.DataFrame:
    start = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    n = len(closes)
    closes = [float(c) for c in closes]
    opens = [closes[0]] + closes[:-1]
    highs = [
        max(o, c) + wick * (0.3 + (i % 3) * 0.3)
        for i, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]
    lows = [
        min(o, c) - wick * (0.3 + (i % 2) * 0.3)
        for i, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]
    times = [start + timedelta(hours=i) for i in range(n)]
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": [volume] * n,
            "spread": [2] * n,
            "real_volume": [volume * 10] * n,
        }
    )


def _trend_leg(
    count: int, start: float, step: float, wiggle: float = 0.0015, period: int = 3
) -> list[float]:
    closes: list[float] = []
    price = start
    for i in range(count):
        price += step + wiggle * np.sin(i / period)
        closes.append(price)
    return closes


def uptrend_then_base_rally(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = _trend_leg(250, 1.2000, 0.0004)
    price = closes[-1]
    for _ in range(4):
        closes.append(price + rng.uniform(-0.00005, 0.00005))
    base_high = max(closes[-4:])
    price = base_high
    for _ in range(2):
        price += 0.003
        closes.append(price)
    closes.append(price - 0.0025)
    closes.append(price - 0.0050)
    closes.append(closes[-1] - 0.0013)  # dip into the zone (retest)
    closes.append(closes[-1] + 0.0020)  # bullish engulfing closing above the dip
    return build_frame(closes, wick=0.0001)


def downtrend_then_base_drop() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    closes = _trend_leg(250, 1.3000, -0.0004)
    price = closes[-1]
    for _ in range(4):
        closes.append(price + rng.uniform(-0.00005, 0.00005))
    price = max(closes[-4:])
    for _ in range(2):
        price -= 0.005
        closes.append(price)
    return build_frame(closes, wick=0.0001)


def rising_series(n: int = 300, step: float = 0.0005) -> pd.DataFrame:
    return build_frame(_trend_leg(n, 1.1000, step), wick=0.0001)
