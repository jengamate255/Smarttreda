"""Deterministic supply/demand zone detection (first version).

The algorithm is deliberately explicit and configurable; zone detection is NOT
objectively "correct" — it is a repeatable, documented heuristic.

Algorithm (all causal — a zone is born at the candle that validates it):
  1. Demand zone candidate: a swing low (see features.structure) where, within
     `impulse_lookback_bars` bars, close rallies at least `min_impulse_atr`
     ATRs above the swing low (a bullish impulse away).
     Zone body = [low, low + zone_depth_atr * ATR at formation].
  2. Supply zone candidate: mirror image at swing highs with bearish impulse.
  3. ANTI-LEAKAGE (critical): a zone only becomes visible `activation_delay =
     max(swing_window, impulse_lookback) + 1` bars after the swing extreme —
     i.e. after the swing is confirmed AND the impulse window has elapsed.
     All zone features (distance, strength, age, touches, invalidation) are
     only ever reported at bars >= activation. Overlapping zones are ALLOWED in
     this version (no merging): the nearest active zone is reported. This keeps
     every zone's state independent of future bars, which is verified by the
     truncation-invariance tests.
  4. A zone is active from activation until it is invalidated: a close beyond
     its far edge. Invalidated zones are never reported as nearest.
  5. Features per bar: nearest supply (above close) / demand (below close),
     distance in ATRs, zone strength (ATRs of the impulse), age in bars, and
     touch count (bars since activation whose range overlaps the zone body, or
     comes within `max_touch_distance_atr` ATRs of it — the tolerance is
     configurable and defaults to 0.5).

Implementation notes: invalidation and touch-bar indices are computed
vectorially per zone; nearest-zone lookup uses sorted edges + bisect with a
bounded forward scan.
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from src.features.structure import swing_fractals

MAX_SCAN = 64


def _build_zones(df: pd.DataFrame, cfg: dict) -> dict:
    window = int(cfg.get("swing_window", 3))
    min_impulse_atr = float(cfg.get("min_impulse_atr", 1.0))
    impulse_lookback = int(cfg.get("impulse_lookback_bars", 5))
    zone_depth_atr = float(cfg.get("zone_depth_atr", 0.5))
    touch_tol_atr = float(cfg.get("max_touch_distance_atr", 0.5))

    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / 14, adjust=False, min_periods=1).mean().to_numpy()
    atr_safe = np.where(atr <= 0, 1e-12, atr)

    swings = swing_fractals(df, window)
    sh_idx = np.where(swings["swing_high"].to_numpy())[0]
    sl_idx = np.where(swings["swing_low"].to_numpy())[0]
    sh_set = set(sh_idx.tolist())
    sl_set = set(sl_idx.tolist())

    activation_delay = max(window, impulse_lookback) + 1
    zones: list[dict] = []

    def make_zone(kind: str, idx: int, bottom: float, top: float, strength: float) -> dict:
        return {
            "kind": kind,
            "birth": int(idx),
            "bottom": float(bottom),
            "top": float(top),
            "strength": float(strength),
            "activation": int(idx) + activation_delay,
        }

    for idx in sl_idx:
        if idx in sh_set or idx + 1 + impulse_lookback > n:
            continue
        impulse = (close[idx + 1 : idx + 1 + impulse_lookback].max() - low[idx]) / atr_safe[idx]
        if impulse >= min_impulse_atr:
            depth = zone_depth_atr * atr_safe[idx]
            zones.append(make_zone("demand", idx, low[idx], low[idx] + depth, impulse))

    for idx in sh_idx:
        if idx in sl_set or idx + 1 + impulse_lookback > n:
            continue
        impulse = (high[idx] - close[idx + 1 : idx + 1 + impulse_lookback].min()) / atr_safe[idx]
        if impulse >= min_impulse_atr:
            depth = zone_depth_atr * atr_safe[idx]
            zones.append(make_zone("supply", idx, high[idx] - depth, high[idx], impulse))

    # Invalidation + touch bars, both starting only at activation.
    for z in zones:
        if z["activation"] >= n:
            continue
        a = z["activation"]
        if z["kind"] == "supply":
            crossed = close[a:] > z["top"]
        else:
            crossed = close[a:] < z["bottom"]
        hits = np.nonzero(crossed)[0]
        z["end"] = int(a + hits[0]) if hits.size else n

        # Touch = bar range overlaps the zone, or sits within `touch_tol_atr`
        # ATRs of it (measured bar-by-bar so the result stays causal).
        win = slice(a, z["end"])
        dist_below = np.maximum(z["bottom"] - high[win], 0.0)  # bar fully below zone
        dist_above = np.maximum(low[win] - z["top"], 0.0)      # bar fully above zone
        max_dist = np.maximum(dist_below, dist_above)
        touch = max_dist <= touch_tol_atr * atr_safe[win]
        mask = np.zeros(n, dtype=bool)
        mask[win] = touch
        z["touch_bars"] = np.nonzero(mask)[0].tolist()

    zones = [z for z in zones if z["activation"] < n]

    return {
        "zones": zones,
        "supply_edges": sorted((z["bottom"], z) for z in zones if z["kind"] == "supply"),
        "demand_edges": sorted((z["top"], z) for z in zones if z["kind"] == "demand"),
        "n": n,
        "atr": atr_safe,
    }


def _nearest_zone(edges: list[tuple[float, dict]], price: float, bar: int, want_above: bool):
    """Nearest active zone above (want_above=True) or below price."""
    prices = [e[0] for e in edges]
    if want_above:
        pos = bisect.bisect_right(prices, price)
        step = 1
    else:
        pos = bisect.bisect_left(prices, price) - 1
        step = -1
    scanned = 0
    while 0 <= pos < len(edges) and scanned < MAX_SCAN:
        _, z = edges[pos]
        if z["activation"] <= bar < z["end"]:
            return z
        pos += step
        scanned += 1
    if scanned >= MAX_SCAN:
        for _, z in edges:
            if z["activation"] <= bar < z["end"]:
                if want_above and z["bottom"] > price:
                    return z
                if not want_above and z["top"] < price:
                    return z
    return None


def build_supply_demand(
    df: pd.DataFrame,
    cfg: dict | None = None,
    htf_alignment: pd.DataFrame | None = None,
) -> pd.DataFrame:
    cfg = cfg or {}
    n = len(df)
    close = df["close"].to_numpy(dtype=float)

    built = _build_zones(df, cfg)
    atr = built["atr"]
    n_out = built["n"]

    cols = {
        "nearest_supply_dist_atr": np.full(n_out, np.nan),
        "nearest_demand_dist_atr": np.full(n_out, np.nan),
        "supply_strength": np.zeros(n_out),
        "demand_strength": np.zeros(n_out),
        "supply_age_bars": np.zeros(n_out),
        "demand_age_bars": np.zeros(n_out),
        "supply_touches": np.zeros(n_out),
        "demand_touches": np.zeros(n_out),
        "sd_htf_align_buy": np.zeros(n_out),
        "sd_htf_align_sell": np.zeros(n_out),
    }

    if built["zones"]:
        supply_edges = built["supply_edges"]
        demand_edges = built["demand_edges"]
        dist_s = cols["nearest_supply_dist_atr"]
        dist_d = cols["nearest_demand_dist_atr"]
        str_s = cols["supply_strength"]
        str_d = cols["demand_strength"]
        age_s = cols["supply_age_bars"]
        age_d = cols["demand_age_bars"]
        touch_s = cols["supply_touches"]
        touch_d = cols["demand_touches"]

        for bar in range(n_out):
            p = close[bar]
            s = _nearest_zone(supply_edges, p, bar, want_above=True)
            d = _nearest_zone(demand_edges, p, bar, want_above=False)
            if s is not None:
                dist_s[bar] = (s["bottom"] - p) / atr[bar]
                str_s[bar] = s["strength"]
                age_s[bar] = bar - s["birth"]
                touch_s[bar] = bisect.bisect_right(s["touch_bars"], bar)
            if d is not None:
                dist_d[bar] = (p - d["top"]) / atr[bar]
                str_d[bar] = d["strength"]
                age_d[bar] = bar - d["birth"]
                touch_d[bar] = bisect.bisect_right(d["touch_bars"], bar)

    result = pd.DataFrame(cols, index=df.index)
    if htf_alignment is not None:
        if "htf_demand_align" in htf_alignment.columns:
            result["sd_htf_align_buy"] = htf_alignment["htf_demand_align"].astype(int)
        if "htf_supply_align" in htf_alignment.columns:
            result["sd_htf_align_sell"] = htf_alignment["htf_supply_align"].astype(int)
    return result
