"""Single-candle price action pattern features (causal, current bar only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_price_action(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]

    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    out["body_range_ratio"] = (body / rng).fillna(0.0)
    out["body_direction"] = np.sign(c - o)

    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - l
    out["upper_wick"] = (upper_wick / rng).fillna(0.0)
    out["lower_wick"] = (lower_wick / rng).fillna(0.0)

    bullish = c > o
    prev_bearish = df["close"].shift(1) < df["open"].shift(1)
    engulf_bull = bullish & prev_bearish & (c >= h.shift(1)) & (o <= l.shift(1))
    out["bullish_engulfing"] = engulf_bull.astype(int)

    bearish = c < o
    prev_bullish = df["close"].shift(1) > df["open"].shift(1)
    engulf_bear = bearish & prev_bullish & (c <= l.shift(1)) & (o >= h.shift(1))
    out["bearish_engulfing"] = engulf_bear.astype(int)

    body_frac = body / rng
    wick_frac_upper = upper_wick / rng
    wick_frac_lower = lower_wick / rng

    pin_bull = bullish & (wick_frac_lower >= 0.55) & (wick_frac_upper <= 0.15) & (body_frac <= 0.3)
    pin_bear = bearish & (wick_frac_upper >= 0.55) & (wick_frac_lower <= 0.15) & (body_frac <= 0.3)
    out["pin_bar_bull"] = pin_bull.astype(int)
    out["pin_bar_bear"] = pin_bear.astype(int)

    hammer = (body_frac <= 0.3) & (wick_frac_lower >= 0.6) & (wick_frac_upper <= 0.1)
    out["hammer"] = hammer.astype(int)

    shooting_star = (body_frac <= 0.3) & (wick_frac_upper >= 0.6) & (wick_frac_lower <= 0.1)
    out["shooting_star"] = shooting_star.astype(int)

    inside = (h <= h.shift(1)) & (l >= l.shift(1)) & (rng.notna()) & (rng.shift(1).notna())
    out["inside_bar"] = inside.astype(int)
    return out
