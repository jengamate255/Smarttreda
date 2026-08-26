"""Baseline strategies for out-of-sample comparison.

The AI must beat these (at least the same-SL/TP random entry baseline) on
out-of-sample data before being considered successful.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _stop_levels(df: pd.DataFrame, cfg: dict, side: str, atr_col: str = "atr") -> tuple[float, float]:
    return 0.0, 0.0


def ema_cross_strategy(df: pd.DataFrame, fast: int = 20, slow: int = 50, spread_pips: float = 0.8) -> list[dict]:
    """EMA cross: long when fast > slow, short when fast < slow, one position."""
    signals: list[dict] = []
    ema_f = df["close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=slow, adjust=False).mean()
    cross = (ema_f > ema_s).astype(int).diff()
    time = pd.to_datetime(df["time"], utc=True)
    for i in range(1, len(df)):
        if cross.iloc[i] == 1:
            signals.append({"time": time.iloc[i], "side": "BUY", "sl": None, "tp": None,
                            "spread_pips": spread_pips})
        elif cross.iloc[i] == -1:
            signals.append({"time": time.iloc[i], "side": "SELL", "sl": None, "tp": None,
                            "spread_pips": spread_pips})
    return signals


def random_entry_strategy(
    df: pd.DataFrame,
    seed: int = 0,
    spread_pips: float = 0.8,
    entry_fraction: float = 0.05,
) -> list[dict]:
    """Random entries with fixed SL/TP (ATR-based), matching the label design."""
    rng = np.random.default_rng(seed)
    atr = df["atr"] if "atr" in df.columns else (df["high"] - df["low"]).rolling(14).mean()
    time = pd.to_datetime(df["time"], utc=True)
    signals: list[dict] = []
    n = len(df)
    for i in range(n):
        if rng.random() > entry_fraction:
            continue
        side = "BUY" if rng.random() < 0.5 else "SELL"
        sl = df["close"].iloc[i] - 1.5 * atr.iloc[i] if side == "BUY" else df["close"].iloc[i] + 1.5 * atr.iloc[i]
        tp = df["close"].iloc[i] + 4.5 * atr.iloc[i] if side == "BUY" else df["close"].iloc[i] - 4.5 * atr.iloc[i]
        signals.append({"time": time.iloc[i], "side": side, "sl": float(sl), "tp": float(tp),
                        "spread_pips": spread_pips})
    return signals


def supply_demand_strategy(df: pd.DataFrame, cfg: dict | None = None, spread_pips: float = 0.8) -> list[dict]:
    """Simple supply/demand strategy: enter at demand retest with SL below zone,
    or short at supply retest with SL above zone. Uses the engineered features."""
    cfg = cfg or {}
    signals: list[dict] = []
    time = pd.to_datetime(df["time"], utc=True)
    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float) if "atr" in df.columns else np.full(len(df), 1e-9)

    min_dist = float(cfg.get("sd_min_dist_atr", 0.0))
    max_dist = float(cfg.get("sd_max_dist_atr", 2.0))
    min_touches = int(cfg.get("sd_min_touches", 1))
    min_strength = float(cfg.get("sd_min_strength", 1.0))

    for i in range(1, len(df)):
        c = close[i]
        a = atr[i] if atr[i] > 0 else 1e-9
        demand_cols = ["nearest_demand_dist_atr", "demand_touches", "demand_strength"]
        supply_cols = ["nearest_supply_dist_atr", "supply_touches", "supply_strength"]
        if all(col in df.columns for col in demand_cols):
            d_dist = df["nearest_demand_dist_atr"].iloc[i]
            if (
                not np.isnan(d_dist)
                and min_dist <= d_dist <= max_dist
                and df["demand_touches"].iloc[i] >= min_touches
                and df["demand_strength"].iloc[i] >= min_strength
            ):
                zone_bottom = c - d_dist * a
                signals.append({
                    "time": time.iloc[i], "side": "BUY",
                    "sl": float(zone_bottom - 0.5 * a), "tp": float(c + 4.5 * a),
                    "spread_pips": spread_pips,
                })
        if all(col in df.columns for col in supply_cols):
            s_dist = df["nearest_supply_dist_atr"].iloc[i]
            if (
                not np.isnan(s_dist)
                and min_dist <= s_dist <= max_dist
                and df["supply_touches"].iloc[i] >= min_touches
                and df["supply_strength"].iloc[i] >= min_strength
            ):
                zone_top = c + s_dist * a
                signals.append({
                    "time": time.iloc[i], "side": "SELL",
                    "sl": float(zone_top + 0.5 * a), "tp": float(c - 4.5 * a),
                    "spread_pips": spread_pips,
                })
    return signals
