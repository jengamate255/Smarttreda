#!/usr/bin/env python3
"""Generate realistic synthetic OHLCV data for all supported symbols and timeframes.

Produces 2 years of M1, M5, M15, H1, H4, D1 data for EURUSD, GBPUSD, USDJPY,
GBPJPY, XAUUSD. Includes realistic trends, ranges, and volatility regimes.
Saves as CSV files in data/ directory.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOLS = {
    "EURUSD": {"base": 1.1000, "pip": 0.0001, "daily_vol": 0.006, "spread_pips": 1.0},
    "GBPUSD": {"base": 1.2700, "pip": 0.0001, "daily_vol": 0.008, "spread_pips": 1.5},
    "USDJPY": {"base": 149.50, "pip": 0.01, "daily_vol": 0.50, "spread_pips": 1.0},
    "GBPJPY": {"base": 189.50, "pip": 0.01, "daily_vol": 0.80, "spread_pips": 2.0},
    "XAUUSD": {"base": 2000.00, "pip": 0.01, "daily_vol": 20.00, "spread_pips": 3.0},
}

TIMEFRAMES = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

YEARS = 2
START_DATE = datetime(2023, 1, 1, tzinfo=timezone.utc)


def generate_regime(n: int, rng: np.random.Generator) -> np.ndarray:
    """Generate a market regime series: 0=trending_up, 1=trending_down, 2=ranging."""
    regimes = np.zeros(n, dtype=int)
    current = 2  # start ranging
    for i in range(n):
        if rng.random() < 0.02:  # 2% chance of regime change
            current = rng.choice([0, 1, 2])
        regimes[i] = current
    return regimes


def generate_ohlcv(
    symbol: str,
    timeframe: str,
    tf_seconds: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate realistic OHLCV data for a symbol and timeframe."""
    info = SYMBOLS[symbol]
    base_price = info["base"]
    pip = info["pip"]
    daily_vol = info["daily_vol"]

    # Scale volatility by timeframe
    tf_factor = np.sqrt(tf_seconds / 86400)
    candle_vol = daily_vol * tf_factor

    # Total candles
    total_seconds = YEARS * 365 * 86400
    n = total_seconds // tf_seconds

    # Generate regimes
    regimes = generate_regime(n, rng)

    # Generate price path with regime-dependent drift
    prices = np.zeros(n)
    prices[0] = base_price
    for i in range(1, n):
        regime = regimes[i]
        if regime == 0:  # trending up
            drift = candle_vol * 0.05
        elif regime == 1:  # trending down
            drift = -candle_vol * 0.05
        else:  # ranging
            drift = 0.0
        change = rng.normal(drift, candle_vol)
        prices[i] = prices[i - 1] + change * base_price

    # Generate OHLC from close prices
    opens = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)
    closes = prices.copy()
    volumes = np.zeros(n)
    spreads = np.zeros(n)

    opens[0] = closes[0]
    for i in range(1, n):
        opens[i] = closes[i - 1] + rng.normal(0, candle_vol * 0.1) * base_price

    for i in range(n):
        body = abs(closes[i] - opens[i])
        wick_up = abs(rng.exponential(body * 0.5 + candle_vol * 0.2 * base_price))
        wick_down = abs(rng.exponential(body * 0.5 + candle_vol * 0.2 * base_price))
        highs[i] = max(opens[i], closes[i]) + wick_up
        lows[i] = min(opens[i], closes[i]) - wick_down
        volumes[i] = rng.uniform(100, 2000)
        # Spread varies by regime
        base_spread = info["spread_pips"] * pip
        if regimes[i] == 2:  # ranging = tighter spreads
            spreads[i] = base_spread * rng.uniform(0.5, 1.5)
        else:
            spreads[i] = base_spread * rng.uniform(1.0, 3.0)

    # Build datetime index
    dates = pd.date_range(start=START_DATE, periods=n, freq=f"{tf_seconds}s", tz=timezone.utc)

    df = pd.DataFrame({
        "open": np.round(opens, 5),
        "high": np.round(highs, 5),
        "low": np.round(lows, 5),
        "close": np.round(closes, 5),
        "volume": np.round(volumes, 0).astype(int),
        "spread": np.round(spreads / pip, 1),  # spread in pips
    }, index=dates)
    df.index.name = "time"

    # Inject realistic gaps (weekends for intraday, news gaps)
    if tf_seconds < 86400:
        # Remove weekend candles
        weekday = df.index.weekday
        df = df[weekday < 5]

        # Inject occasional news gaps (0.5% of candles)
        n_gaps = max(1, int(n * 0.005))
        gap_indices = rng.choice(len(df), size=n_gaps, replace=False)
        for idx in sorted(gap_indices, reverse=True):
            gap_size = rng.integers(2, 8)
            if idx + gap_size < len(df):
                df = pd.concat([df.iloc[:idx], df.iloc[idx + gap_size:]])

    return df


def main():
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    for symbol in SYMBOLS:
        sym_dir = output_dir / symbol
        sym_dir.mkdir(exist_ok=True)

        for tf, tf_seconds in TIMEFRAMES.items():
            seed = hash((symbol, tf)) % (2**31)
            rng = np.random.default_rng(seed)

            print(f"Generating {symbol} {tf}...")
            df = generate_ohlcv(symbol, tf, tf_seconds, rng)

            filepath = sym_dir / f"{symbol}_{tf}.csv"
            df.to_csv(filepath)
            total_files += 1
            print(f"  -> {filepath} ({len(df)} candles)")

    print(f"\nDone! Generated {total_files} CSV files in {output_dir}")


if __name__ == "__main__":
    main()
