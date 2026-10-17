"""Supply/demand zones with causal state transitions (contract C4).

A demand zone = drop-base-rally: `base_bars` of consolidation (range <= 1
ATR, avg body <= 0.5 ATR) followed by a bullish impulse candle with body >=
`impulse_mult` x ATR. Zone bounds = the base bars' extremes. Mirrored for
supply (rally-base-drop).

State is evaluated per bar as zones age: touches (bar overlaps zone), breaks
(close beyond zone by `breach_atr` x ATR). All evaluation is trailing — a
zone's retest/break state at bar b only uses bars in (formation, b].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import ZoneConfig


@dataclass
class Zone:
    zone_id: str
    type: str                  # "supply" | "demand"
    tf: str
    created_index: int          # impulse bar index
    created_time: pd.Timestamp
    upper: float
    lower: float
    width: float
    width_atr: float
    origin: str                # "drop_base_rally" | "rally_base_drop"
    departure_strength_atr: float   # impulse body / ATR
    departure_velocity: float       # impulse close-to-close move / ATR
    volume_context: float           # base avg volume / rolling avg volume
    strength: float = 0.0
    # Causal state at the evaluation horizon:
    retests: int = 0
    mitigations: int = 0
    status: str = "fresh"      # fresh | tested | mitigated | broken
    fresh: bool = True
    last_event_index: int = -1


def detect_zone_candidates(
    df: pd.DataFrame, tf: str, config: ZoneConfig | None = None
) -> list[Zone]:
    """Form zones causally (no future state evaluation yet)."""
    cfg = config or ZoneConfig()
    n = len(df)
    if n < cfg.base_bars + 2:
        return []

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["time"]

    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / cfg.atr_period, adjust=False, min_periods=1).mean().to_numpy()

    base_ranges = pd.Series(h - l).rolling(cfg.base_bars).max().shift(1).to_numpy()
    base_bodies = (
        pd.Series(np.abs(c - o)).rolling(cfg.base_bars).mean().shift(1).to_numpy()
    )
    vol_avg = pd.Series(v).rolling(cfg.atr_period * 4, min_periods=1).mean().to_numpy()

    zones: list[Zone] = []
    start = max(0, n - cfg.lookback) if cfg.lookback > 0 else 0
    for i in range(start, n - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if not np.isfinite(base_ranges[i]) or not np.isfinite(base_bodies[i]):
            continue
        if base_ranges[i] > cfg.max_base_range_atr * a:
            continue
        if base_bodies[i] > cfg.max_base_body_atr * a:
            continue
        body = abs(c[i] - o[i])
        if body < cfg.impulse_mult * a:
            continue
        s = i - cfg.base_bars
        if s < 0:
            continue
        zone_high = float(h[s:i].max())
        zone_low = float(l[s:i].min())
        if zone_high - zone_low <= 0:
            continue
        base_vol_avg = float(v[s:i].mean()) if len(v[s:i]) else 0.0
        vol_ctx = base_vol_avg / vol_avg[i] if vol_avg[i] > 0 else 1.0
        velocity = abs(c[i] - c[i - 1]) / a if i > 0 else 0.0
        if c[i] > o[i]:
            ztype, origin = "demand", "drop_base_rally"
        else:
            ztype, origin = "supply", "rally_base_drop"
        zones.append(
            Zone(
                zone_id=f"{tf}-{ztype}-{i}",
                type=ztype,
                tf=tf,
                created_index=i,
                created_time=pd.Timestamp(t[i]),
                upper=zone_high,
                lower=zone_low,
                width=zone_high - zone_low,
                width_atr=(zone_high - zone_low) / a,
                origin=origin,
                departure_strength_atr=body / a,
                departure_velocity=float(velocity),
                volume_context=float(vol_ctx),
                last_event_index=i,
            )
        )

    # Merge overlapping same-type zones (keep earliest bounds union)
    merged: list[Zone] = []
    for z in zones:
        placed = False
        for m in merged:
            if m.type == z.type and z.lower <= m.upper and z.upper >= m.lower:
                m.upper = max(m.upper, z.upper)
                m.lower = min(m.lower, z.lower)
                m.width = m.upper - m.lower
                m.width_atr = max(m.width_atr, z.width_atr)
                m.departure_strength_atr = max(m.departure_strength_atr, z.departure_strength_atr)
                m.last_event_index = max(m.last_event_index, z.last_event_index)
                placed = True
                break
        if not placed:
            merged.append(z)
    for k, z in enumerate(merged):
        z.zone_id = f"{tf}-{z.type}-{k}-{z.created_index}"
    return merged


def zone_strength(z: Zone) -> float:
    """0-100 heuristic strength from measurable properties."""
    momentum = min(z.departure_strength_atr / 1.5, 1.0)
    size = 1.0 if 0.15 <= z.width_atr <= 0.5 else (max(0.3, 1 - abs(z.width_atr - 0.3)))
    volume = min(max(z.volume_context, 0.0), 2.0) / 2.0
    score = 100 * (0.4 * momentum + 0.3 * size + 0.3 * volume)
    if z.status == "broken":
        score *= 0.3
    elif z.status == "mitigated":
        score *= 0.6
    return float(min(100.0, max(1.0, score)))


def advance_zone_state(
    z: Zone, df: pd.DataFrame, horizon_index: int, config: ZoneConfig | None = None
) -> Zone:
    """Evaluate touches/breaks up to ``horizon_index`` (trailing only)."""
    cfg = config or ZoneConfig()
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / cfg.atr_period, adjust=False, min_periods=1).mean().to_numpy()

    z.retests = 0
    z.mitigations = 0
    z.status = "fresh"
    for b in range(z.created_index + 1, min(horizon_index, len(df)) + 1):
        a = atr[b] if np.isfinite(atr[b]) else 0.0
        if a <= 0:
            continue
        # touch: bar overlaps zone
        if h[b] >= z.lower and l[b] <= z.upper:
            z.retests += 1
            z.last_event_index = b
            if z.status == "fresh":
                z.status = "tested"
        # break: close beyond zone by breach buffer
        if z.type == "demand" and c[b] < z.lower - cfg.breach_atr * a:
            z.status = "broken"
            z.last_event_index = b
            break
        if z.type == "supply" and c[b] > z.upper + cfg.breach_atr * a:
            z.status = "broken"
            z.last_event_index = b
            break
        # mitigation: price entered the zone and closed back inside range
        if z.status != "broken" and h[b] >= z.lower and l[b] <= z.upper:
            inside_close = z.lower <= c[b] <= z.upper
            if inside_close:
                z.mitigations += 1
                if z.mitigations >= 2:
                    z.status = "mitigated"
    z.fresh = z.status == "fresh" or (z.retests <= 1 and z.status != "broken")
    z.strength = zone_strength(z)
    return z


def zones_visible_at(
    zones: list[Zone], index: int
) -> list[Zone]:
    return [z for z in zones if z.created_index <= index]
