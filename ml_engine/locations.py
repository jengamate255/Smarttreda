"""Location engine: where is price, and what significant levels sit nearby?

A professional trader cares about LOCATION before anything else. This module
builds measurable locations:

- support / resistance clusters from confirmed swing pivots
- previous day high/low (PDH/PDL) and previous session high/low
- significant swings (large, recent, unbroken)
- consolidation zones (range compression rectangles)
- breakout levels (broken range extremes -> retest candidates)

Every location is causal: it only becomes visible at its ``created_at`` bar,
and retests/counters are counted only on bars after creation.

Contract L1 (see data-contracts.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import EngineConfig
from ml_engine.structure.swings import SwingEvent, swing_events


@dataclass
class Location:
    location_id: str
    kind: str           # support | resistance | pdh | pdl | session_high |
                        # session_low | swing_high | swing_low |
                        # consolidation | breakout_level
    price_high: float
    price_low: float    # == price_high for line levels
    timeframe: str
    created_index: int  # first bar where this location is known
    created_time: pd.Timestamp
    strength: float     # 0..100
    freshness: float    # 0..1 (1 = just created)
    retests: int        # touches after creation (causal)
    broken: bool        # breached beyond tolerance (causal)
    width_atr: float


def _wilder_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return (
        pd.Series(tr)
        .ewm(alpha=1.0 / period, adjust=False, min_periods=1)
        .mean()
        .to_numpy()
    )


class LocationConfig:
    """Cluster/session parameters for the location engine."""

    cluster_tolerance_atr: float = 0.45
    min_cluster_strength: float = 30.0
    swing_strength_min_atr: float = 0.75
    consolidation_range_max_atr: float = 0.8
    consolidation_min_bars: int = 12
    consolidation_break_buffer_atr: float = 0.25
    breakout_range_bars: int = 30
    session_windows: dict[str, tuple[int, int]] | None = None  # UTC hours

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        if self.session_windows is None:
            self.session_windows = {"asia": (0, 8), "london": (7, 16), "newyork": (12, 21)}


def _cluster_pivots(
    swings: list[SwingEvent],
    atr: np.ndarray,
    tol_atr: float,
    n: int,
) -> list[dict]:
    """Group confirmed same-kind pivots within tolerance into levels."""
    by_kind: dict[str, list[SwingEvent]] = {}
    for e in swings:
        by_kind.setdefault(e.kind, []).append(e)

    clusters: list[dict] = []
    for kind, group in by_kind.items():
        i = 0
        while i < len(group):
            pivot = group[i]
            members = [pivot]
            j = i + 1
            ref_price = pivot.price
            while j < len(group):
                a = atr[group[j].visible_index] if group[j].visible_index < n else atr[-1]
                tol = tol_atr * (a if np.isfinite(a) and a > 0 else 0.0)
                if abs(group[j].price - ref_price) <= max(tol, 1e-12):
                    members.append(group[j])
                    ref_price = (ref_price * (len(members) - 1) + group[j].price) / len(members)
                    j += 1
                else:
                    break
            # a cluster needs >= 2 pivots OR one pivot + wick evidence
            clusters.append({"kind": kind, "price": ref_price, "members": members})
            i = j
    return clusters


def _line_locations(
    clusters: list[dict], df: pd.DataFrame, atr: np.ndarray, cfg: LocationConfig
) -> list[Location]:
    """support/resistance lines from pivot clusters, with causal touch counts."""
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    t = pd.to_datetime(df["time"])
    n = len(df)
    out: list[Location] = []
    for cl in clusters:
        price = cl["price"]
        kind = "support" if cl["kind"] == "swing_low" else "resistance"
        created = max(m.visible_index for m in cl["members"])
        if created >= n:
            continue
        touches = 0
        broken = False
        for b in range(created + 1, n):
            a = atr[b] if np.isfinite(atr[b]) else 0.0
            tol = cfg.cluster_tolerance_atr * (a if a > 0 else 0.0)
            if l[b] <= price + tol and h[b] >= price - tol:
                touches += 1
            if kind == "support" and c[b] < price - max(tol, cfg.consolidation_break_buffer_atr * a):
                broken = True
                break
            if kind == "resistance" and c[b] > price + max(tol, cfg.consolidation_break_buffer_atr * a):
                broken = True
                break
        strength = min(100.0, 30.0 + len(cl["members"]) * 18.0 + touches * 8.0)
        if strength < cfg.min_cluster_strength or broken:
            continue
        out.append(
            Location(
                location_id=f"s-r-{kind}-{int(price * 1e5)}",
                kind=kind,
                price_high=float(price),
                price_low=float(price),
                timeframe="",
                created_index=int(created),
                created_time=pd.Timestamp(t[created]),
                strength=float(strength),
                freshness=float(max(0.0, 1.0 - (n - 1 - created) / 400.0)),
                retests=int(touches),
                broken=False,
                width_atr=0.0,
            )
        )
    return out


def _day_locations(df: pd.DataFrame, atr: np.ndarray, cfg: LocationConfig) -> list[Location]:
    """PDH/PDL: previous day's high/low, known at the first bar of each day."""
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    t = pd.to_datetime(df["time"])
    n = len(df)
    day = t.dt.normalize()
    out: list[Location] = []
    prev_day = None
    prev_high = prev_low = None
    for i in range(n):
        if prev_day is None or day.iloc[i] != prev_day:
            if prev_day is not None and prev_high is not None:
                atr[i] if np.isfinite(atr[i]) else 0.0
                out.append(
                    Location(
                        location_id=f"pdh-{prev_day.date()}",
                        kind="pdh",
                        price_high=float(prev_high), price_low=float(prev_high),
                        timeframe="",
                        created_index=i, created_time=pd.Timestamp(t[i]),
                        strength=70.0, freshness=1.0, retests=0, broken=False,
                        width_atr=0.0,
                    )
                )
                out.append(
                    Location(
                        location_id=f"pdl-{prev_day.date()}",
                        kind="pdl",
                        price_high=float(prev_low), price_low=float(prev_low),
                        timeframe="",
                        created_index=i, created_time=pd.Timestamp(t[i]),
                        strength=70.0, freshness=1.0, retests=0, broken=False,
                        width_atr=0.0,
                    )
                )
            prev_day = day.iloc[i]
            prev_high = h[i]
            prev_low = l[i]
        else:
            prev_high = max(prev_high, h[i])
            prev_low = min(prev_low, l[i])
    return out


def _session_locations(df: pd.DataFrame, cfg: LocationConfig) -> list[Location]:
    """Previous session high/low per session window (UTC hours)."""
    t = pd.to_datetime(df["time"])
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    n = len(df)
    out: list[Location] = []
    for session_name, (start_hour, end_hour) in cfg.session_windows.items():
        in_session = (t.dt.hour >= start_hour) & (t.dt.hour < end_hour)
        # session id changes when entering a new day
        day = t.dt.normalize()
        cur_key = None
        cur_high = cur_low = None
        prev_high = prev_low = None
        for i in range(n):
            if in_session.iloc[i]:
                key = (day.iloc[i], session_name)
                if key != cur_key:
                    if cur_key is not None and prev_high is not None:
                        out.extend(
                            [
                                Location(
                                    location_id=f"session-{session_name}-high-{cur_key[0].date()}",
                                    kind="session_high",
                                    price_high=float(prev_high), price_low=float(prev_high),
                                    timeframe="", created_index=i,
                                    created_time=pd.Timestamp(t[i]),
                                    strength=55.0, freshness=1.0, retests=0,
                                    broken=False, width_atr=0.0,
                                ),
                                Location(
                                    location_id=f"session-{session_name}-low-{cur_key[0].date()}",
                                    kind="session_low",
                                    price_high=float(prev_low), price_low=float(prev_low),
                                    timeframe="", created_index=i,
                                    created_time=pd.Timestamp(t[i]),
                                    strength=55.0, freshness=1.0, retests=0,
                                    broken=False, width_atr=0.0,
                                ),
                            ]
                        )
                    _prev_key, cur_key = cur_key, key
                    prev_high, prev_low = cur_high, cur_low
                    cur_high, cur_low = h[i], l[i]
                else:
                    cur_high = max(cur_high, h[i]) if cur_high is not None else h[i]
                    cur_low = min(cur_low, l[i]) if cur_low is not None else l[i]
    return out


def _significant_swing_locations(
    swings: list[SwingEvent], df: pd.DataFrame, atr: np.ndarray, cfg: LocationConfig
) -> list[Location]:
    """Unbroken swings with magnitude >= min ATR, marked as significant."""
    t = pd.to_datetime(df["time"])
    n = len(df)
    close = df["close"].to_numpy(float)
    out: list[Location] = []
    for e in swings:
        if e.visible_index >= n:
            continue
        a = atr[e.visible_index]
        if not np.isfinite(a) or a <= 0:
            continue
        # magnitude vs surrounding structure approximated by distance to close
        mag = abs(close[e.visible_index] - e.price) / a
        if mag < cfg.swing_strength_min_atr:
            continue
        kind = "swing_high" if e.kind == "swing_high" else "swing_low"
        out.append(
            Location(
                location_id=f"swing-{kind}-{e.pivot_index}",
                kind=kind,
                price_high=float(e.price), price_low=float(e.price),
                timeframe="", created_index=int(e.visible_index),
                created_time=pd.Timestamp(t[e.visible_index]),
                strength=float(min(90.0, 40.0 + mag * 15.0)),
                freshness=float(max(0.0, 1.0 - (n - 1 - e.visible_index) / 300.0)),
                retests=0, broken=False, width_atr=float(mag),
            )
        )
    return out


def _consolidation_locations(df: pd.DataFrame, atr: np.ndarray, cfg: LocationConfig) -> list[Location]:
    """Range-compression rectangles (potential breakout bases)."""
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    t = pd.to_datetime(df["time"])
    n = len(df)
    out: list[Location] = []
    rng = h - l
    i = 0
    while i < n:
        a = atr[i]
        if not np.isfinite(a) or a <= 0 or rng[i] > cfg.consolidation_range_max_atr * a:
            i += 1
            continue
        j = i
        while j < n:
            aj = atr[j]
            if not np.isfinite(aj) or aj <= 0 or rng[j] > cfg.consolidation_range_max_atr * aj:
                break
            j += 1
        if j - i >= cfg.consolidation_min_bars:
            out.append(
                Location(
                    location_id=f"consolidation-{i}",
                    kind="consolidation",
                    price_high=float(h[i:j].max()), price_low=float(l[i:j].min()),
                    timeframe="", created_index=j,
                    created_time=pd.Timestamp(t[j - 1]),
                    strength=float(min(80.0, 40.0 + (j - i) * 2.0)),
                    freshness=float(max(0.0, 1.0 - (n - 1 - j) / 300.0)),
                    retests=0, broken=False,
                    width_atr=float((h[i:j].max() - l[i:j].min()) / atr[j - 1]),
                )
            )
        i = j + 1
    return out


def _breakout_locations(df: pd.DataFrame, atr: np.ndarray, cfg: LocationConfig) -> list[Location]:
    """Broken N-bar range extremes become retest levels (flip zones)."""
    from ml_engine.patterns.breakout import detect_breakouts

    events = detect_breakouts(df)
    t = pd.to_datetime(df["time"])
    out: list[Location] = []
    for e in events:
        if not e.kind.startswith("breakout"):
            continue
        out.append(
            Location(
                location_id=f"breakout-{e.index}-{e.direction}",
                kind="breakout_level",
                price_high=float(e.level), price_low=float(e.level),
                timeframe="", created_index=int(e.index),
                created_time=pd.Timestamp(t[e.index]),
                strength=float(min(75.0, 45.0 + e.strength_atr * 12.0)),
                freshness=1.0, retests=0, broken=False,
                width_atr=float(e.strength_atr),
            )
        )
    return out


def detect_locations(df: pd.DataFrame, timeframe: str, cfg: EngineConfig) -> list[Location]:
    """All location families, deduplicated and sorted by creation."""
    lcfg = LocationConfig()
    atr = _wilder_atr(df, cfg.feature.atr_period)
    swings = swing_events(df, cfg.swing)

    locations: list[Location] = []
    locations.extend(_line_locations(_cluster_pivots(swings, atr, lcfg.cluster_tolerance_atr, len(df)), df, atr, lcfg))
    locations.extend(_day_locations(df, atr, lcfg))
    locations.extend(_session_locations(df, lcfg))
    locations.extend(_significant_swing_locations(swings, df, atr, lcfg))
    locations.extend(_consolidation_locations(df, atr, lcfg))
    locations.extend(_breakout_locations(df, atr, lcfg))

    for loc in locations:
        loc.timeframe = timeframe

    # dedupe by (kind, rounded price, created)
    seen: set[tuple] = set()
    unique: list[Location] = []
    for loc in locations:
        key = (loc.kind, round(loc.price_high, 6), loc.created_index)
        if key not in seen:
            seen.add(key)
            unique.append(loc)
    unique.sort(key=lambda x: (x.created_index, x.kind))
    return unique


def locations_visible_at(locations: list[Location], index: int) -> list[Location]:
    return [loc for loc in locations if loc.created_index <= index]


def nearest_location_at(
    locations: list[Location], index: int, price: float, kind: str | None = None
) -> Location | None:
    """Nearest visible location (optionally filtered by kind)."""
    best = None
    best_dist = np.inf
    for loc in locations:
        if loc.created_index > index:
            continue
        if kind is not None and loc.kind != kind:
            continue
        dist = min(abs(price - loc.price_high), abs(price - loc.price_low))
        if dist < best_dist:
            best, best_dist = loc, dist
    return best


def distance_to(loc: Location, price: float) -> float:
    if loc.price_low <= price <= loc.price_high:
        return 0.0
    return float(min(abs(price - loc.price_low), abs(price - loc.price_high)))
