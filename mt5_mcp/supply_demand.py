"""Supply & Demand zone detection and scoring.

Detects "drop-base-rally" demand zones and "rally-base-drop" supply zones:

1. Find strong impulse candles (body >= ``impulse_mult`` x ATR)
2. Verify the candles before the impulse form a quiet base (small range)
3. Zone bounds = base range (optionally extended to the impulse candle)
4. Merge overlapping same-type zones
5. Score 1-100 from momentum, size, age, touches, broken/retested state
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common import Zone
from indicators import atr

DEMAND = "demand"
SUPPLY = "supply"


def _detect_candidates(
    df: pd.DataFrame, atr_series: pd.Series, base_bars: int, impulse_mult: float
) -> list[dict]:
    n = len(df)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    a = atr_series.values.astype(float)

    bodies = np.abs(closes - opens)
    hhll = highs - lows
    with np.errstate(invalid="ignore"):
        valid = np.isfinite(a) & (a > 0)
        impulses = valid & (bodies >= impulse_mult * a)
        base_range = (
            pd.Series(hhll).rolling(base_bars).max().shift(1).to_numpy()
        )
        base_avg_body = (
            pd.Series(bodies).rolling(base_bars).mean().shift(1).to_numpy()
        )
        ok = (
            impulses
            & np.isfinite(base_range)
            & (base_range <= 1.0 * a)
            & (base_avg_body <= 0.5 * a)
        )
    ok[: base_bars + 1] = False
    ok[n - 1 :] = False

    directions = np.where(closes > opens, 1, -1)
    candidates: list[dict] = []
    for i in np.nonzero(ok)[0]:
        direction = int(directions[i])
        lo = float(np.min(lows[i - base_bars : i]))
        hi = float(np.max(highs[i - base_bars : i]))
        candidates.append(
            {
                "type": DEMAND if direction > 0 else SUPPLY,
                "formation_index": int(i),
                "lower": lo,
                "upper": hi,
                "impulse_atr": float(bodies[i] / a[i]),
                "base_size_atr": float(base_range[i] / a[i]),
            }
        )
    return candidates


def _merge_candidates(candidates: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for cand in sorted(candidates, key=lambda c: c["formation_index"]):
        if not merged:
            merged.append(cand)
            continue
        last = merged[-1]
        if cand["type"] == last["type"] and cand["lower"] <= last["upper"]:
            last["lower"] = min(last["lower"], cand["lower"])
            last["upper"] = max(last["upper"], cand["upper"])
            last["impulse_atr"] = max(last["impulse_atr"], cand["impulse_atr"])
            last["base_size_atr"] = min(last["base_size_atr"], cand["base_size_atr"])
            last["touch_count"] = last.get("touch_count", 0) + 1
        else:
            merged.append(cand)
    return merged


def _evaluate_zone(
    zone: dict, df: pd.DataFrame, atr_series: pd.Series, current_index: int
) -> Zone:
    low, high = zone["lower"], zone["upper"]
    formation = zone["formation_index"]
    a = float(atr_series.iloc[formation])
    if not np.isfinite(a) or a <= 0:
        a = float(atr_series.dropna().median()) or 1.0

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    seg_highs = highs[formation + 1 : current_index]
    seg_lows = lows[formation + 1 : current_index]
    seg_closes = closes[formation + 1 : current_index]
    overlaps = (seg_lows <= high) & (seg_highs >= low)

    broken = False
    if zone["type"] == DEMAND:
        violated = seg_closes < low - 0.25 * a
    else:
        violated = seg_closes > high + 0.25 * a
    first_violation = int(violated.nonzero()[0][0]) if violated.any() else None
    if first_violation is not None:
        broken = True
        overlaps = overlaps[:first_violation]

    touches = int(overlaps.sum())
    last_touch = (
        int(formation + 1 + overlaps.nonzero()[0][-1]) if touches else None
    )

    age_bars = current_index - 1 - formation
    momentum = min(float(zone["impulse_atr"]) / 1.5, 1.0)
    size = float(zone["base_size_atr"])
    if size <= 0:
        size_score = 0.5
    elif 0.15 <= size <= 0.5:
        size_score = 1.0
    elif size < 0.15:
        size_score = 0.6 + size / 0.15 * 0.4
    else:
        size_score = max(0.3, 1.0 - (size - 0.5) / 2.0)
    age_score = max(0.0, 1.0 - age_bars / 300.0)
    if touches <= 2:
        touch_score = 1.0
    elif touches <= 4:
        touch_score = 0.7
    else:
        touch_score = 0.4

    strength = 100.0 * (
        0.35 * momentum + 0.25 * size_score + 0.20 * age_score + 0.20 * touch_score
    )
    if broken:
        strength *= 0.3
    strength = float(np.clip(strength, 1.0, 100.0))

    fresh = touches <= 1 and not broken
    retested = touches >= 1
    return Zone(
        type=zone["type"],
        upper=high,
        lower=low,
        strength=round(strength, 1),
        touches=touches,
        fresh=fresh,
        broken=broken,
        retested=retested,
        confidence=round(strength, 1),
        age_bars=age_bars,
        formation_time=df.iloc[formation]["time"],
        last_touch_index=last_touch,
        base_size_atr=round(size, 3),
        impulse_atr=round(float(zone["impulse_atr"]), 3),
    )


def detect_zones(
    df: pd.DataFrame,
    atr_period: int = 14,
    base_bars: int = 4,
    impulse_mult: float = 1.5,
    lookback: int = 400,
    min_strength: float = 0.0,
) -> list[Zone]:
    """Detect and score supply/demand zones. Sorted by strength (highest first)."""
    if df is None or len(df) < base_bars + 30:
        return []
    frame = df.tail(int(lookback)).reset_index(drop=True)
    if frame.empty:
        return []
    atr_series = atr(frame, atr_period)
    current_index = len(frame)
    candidates = _detect_candidates(
        frame, atr_series, int(base_bars), float(impulse_mult)
    )
    merged = _merge_candidates(candidates)
    zones = [_evaluate_zone(z, frame, atr_series, current_index) for z in merged]
    zones.sort(key=lambda z: (-z.strength, -z.formation_time.timestamp()))
    return [z for z in zones if z.strength >= min_strength]


def nearest_zone(zones: list[Zone], price: float) -> Zone | None:
    """Zone closest to the given price (by distance to its near edge)."""
    if not zones:
        return None
    return min(zones, key=lambda z: min(abs(price - z.lower), abs(price - z.upper)))
