"""Shared utilities: time handling, pip sizes, pips <-> price conversions."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Symbol -> pip size (one standard pip in price units).
# XAUUSD uses 0.10 (a "pip" is 10 cents on 2-3 decimal quoting).
PIP_SIZES = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "USDCHF": 0.0001,
    "USDJPY": 0.01,
    "XAUUSD": 0.10,
}


def pip_size(symbol: str) -> float:
    return PIP_SIZES.get(symbol.upper(), 0.0001)


def pips_to_price(pips: float, symbol: str) -> float:
    return pips * pip_size(symbol)


def price_to_pips(price_diff: float, symbol: str) -> float:
    return price_diff / pip_size(symbol)


def floor_ms(series: pd.Series, bar_seconds: int) -> np.ndarray:
    """Floor a datetime64 series to the given bar interval (UTC aligned)."""
    ts = series.astype("int64").to_numpy() // 1_000_000
    return ((ts // (bar_seconds * 1_000)) * (bar_seconds * 1_000)).astype("datetime64[ms]")


def floor_datetime(value: pd.Timestamp, bar_seconds: int) -> pd.Timestamp:
    ms = int(value.timestamp() * 1000)
    return pd.Timestamp((ms // (bar_seconds * 1000)) * (bar_seconds * 1000), unit="ms", tz="UTC")


def to_utc(series: pd.Series, offset_hours: float = 0.0) -> pd.Series:
    """Normalize a naive timestamp series to tz-aware UTC.

    If the terminal timestamps carry a known broker offset (e.g. +3h), it is
    subtracted first so the result is true UTC.
    """
    if series.dt.tz is not None:
        return series.dt.tz_convert("UTC")
    if offset_hours:
        series = series - pd.Timedelta(hours=offset_hours)
    return series.dt.tz_localize("UTC")


def atr_based_stop(df: pd.DataFrame, atr_multiple: float, symbol: str) -> pd.Series:
    """Return a per-bar SL distance in pips derived from ATR."""
    atr_col = "atr"
    if atr_col not in df.columns:
        raise ValueError("atr column required; build technical features first")
    return (df[atr_col] * atr_multiple / pip_size(symbol)).round(1)


def expected_bar_ms(timeframe: str) -> int:
    mapping = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
    return mapping.get(timeframe.upper(), 900)
