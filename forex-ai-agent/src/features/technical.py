"""Trend / momentum / volatility technical indicators.

All indicators are causal: each row depends only on rows at or before it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def roc(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {f"roc_{p}": close.pct_change(p) for p in periods}
    )


def build_technical(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or {}
    ema_periods = cfg.get("ema_periods", [20, 50, 100, 200])
    rsi_period = cfg.get("rsi_period", 14)
    atr_period = cfg.get("atr_period", 14)
    roc_periods = cfg.get("roc_periods", [1, 5, 10, 20])
    momentum_periods = cfg.get("momentum_periods", [5, 10, 20])
    vol_window = cfg.get("vol_window", 20)
    vol_pct_window = cfg.get("vol_percentile_window", 100)

    out = pd.DataFrame(index=df.index)
    close, high, low = df["close"], df["high"], df["low"]

    for span in ema_periods:
        out[f"ema_{span}"] = ema(close, span)

    # EMA slopes (% change over 1 bar, in ATR units to stay comparable).
    atr_series = atr(df, atr_period)
    for span in ema_periods:
        slope = out[f"ema_{span}"].pct_change()
        out[f"ema_{span}_slope"] = slope / (atr_series / close)
    for a, b in zip(ema_periods, ema_periods[1:]):
        out[f"ema_{a}_{b}_dist"] = (out[f"ema_{a}"] - out[f"ema_{b}"]) / atr_series
    first, last = ema_periods[0], ema_periods[-1]
    out[f"price_dist_ema{first}"] = (close - out[f"ema_{first}"]) / atr_series
    out[f"price_dist_ema{last}"] = (close - out[f"ema_{last}"]) / atr_series

    out["trend_direction"] = (out[f"ema_{first}"] > out[f"ema_{last}"]).astype(int)

    out["rsi"] = rsi(close, rsi_period)
    rocs = roc(close, roc_periods)
    for col in rocs.columns:
        out[col] = rocs[col]
    for p in momentum_periods:
        out[f"momentum_{p}"] = close.pct_change(p)

    body = close - df["open"]
    body_strength = body / (df["high"] - df["low"]).replace(0, np.nan)
    out["candle_body_strength"] = body_strength.fillna(0.0)

    out["atr"] = atr_series
    out["atr_pct"] = atr_series / close
    returns = close.pct_change()
    out["rolling_volatility"] = returns.rolling(vol_window, min_periods=2).std()
    range_series = high - low
    out["range_expansion"] = range_series / range_series.rolling(vol_window, min_periods=2).mean()

    pct = out["atr_pct"].copy()
    out["volatility_percentile"] = pct.rolling(vol_pct_window, min_periods=10).apply(
        lambda x: float((x[:-1] < x[-1]).mean()) if len(x) > 1 else 0.5, raw=True
    )
    return out
