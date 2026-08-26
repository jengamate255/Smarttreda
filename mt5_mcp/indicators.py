"""Technical indicators implemented with pandas/numpy (no TA-Lib required).

All functions accept a DataFrame with ``open/high/low/close`` columns and
return Series or DataFrames aligned to the input index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=int(period), min_periods=1).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=int(period), adjust=False, min_periods=1).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    return result.fillna(100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram}
    )


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    middle = sma(close, period)
    std = close.rolling(int(period), min_periods=1).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return pd.DataFrame({"upper": upper, "middle": middle, "lower": lower})


def vwap(df: pd.DataFrame, rolling: int | None = None) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    volume = df["tick_volume"].astype(float).replace(0.0, np.nan).fillna(1.0)
    pv = typical * volume
    if rolling:
        cum_pv = pv.rolling(int(rolling), min_periods=1).sum()
        cum_v = volume.rolling(int(rolling), min_periods=1).sum()
    else:
        cum_pv = pv.cumsum()
        cum_v = volume.cumsum()
    return cum_pv / cum_v.replace(0.0, np.nan)


def _swings(values: np.ndarray, left: int, right: int, find_max: bool) -> list[int]:
    """Indices of confirmed fractal pivots (identical result to the old loop)."""
    n = len(values)
    if n < left + right + 1:
        return []
    windows = np.lib.stride_tricks.sliding_window_view(values, left + right + 1)
    extremes = windows.max(axis=1) if find_max else windows.min(axis=1)
    centers = windows[:, left]
    if find_max:
        unique = np.equal(windows, extremes[:, None]).sum(axis=1) == 1
        hit = (centers == extremes) & unique
    else:
        unique = np.equal(windows, extremes[:, None]).sum(axis=1) == 1
        hit = (centers == extremes) & unique
    return [int(i) for i in np.nonzero(hit)[0] + left]


def swing_highs(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[int]:
    """Indices of confirmed swing highs (fractal pivots)."""
    return _swings(df["high"].to_numpy(), int(left), int(right), True)


def swing_lows(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[int]:
    return _swings(df["low"].to_numpy(), int(left), int(right), False)


def volume(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    vol = df["tick_volume"].astype(float)
    avg = vol.rolling(int(period), min_periods=1).mean()
    return pd.DataFrame(
        {"volume": vol, "average": avg, "ratio": vol / avg.replace(0.0, np.nan)}
    )
