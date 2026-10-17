"""Feature builder: trailing windows only, no centered statistics.

Output: one DataFrame aligned to the base frame index. All columns are
computable at bar t from bars <= t. Structure/zone/liquidity/pattern
features come from the causal event streams and are broadcast to every bar
with explicit recency (bars since last event) so tree models can use them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml_engine.config import EngineConfig
from ml_engine.patterns.price_action import PatternEvent, detect_price_action
from ml_engine.patterns.zones import Zone, detect_zone_candidates, zone_strength
from ml_engine.structure.events import StructureEvent, structure_events


def _ema_features(close: pd.Series, periods: tuple[int, ...]) -> pd.DataFrame:
    out = {}
    for p in periods:
        ema = close.ewm(span=p, adjust=False, min_periods=1).mean()
        out[f"ema{p}"] = ema.to_numpy()
    return pd.DataFrame(out)


def _trend_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    close = df["close"]
    f = _ema_features(close, cfg.feature.ema_periods)
    out = {}
    for p in cfg.feature.ema_periods:
        out[f"ema{p}"] = f[f"ema{p}"].to_numpy()
        out[f"close_ema{p}_dist_atr"] = np.zeros(len(df))
    atr = _wilder_atr(df, cfg.feature.atr_period)
    for p in cfg.feature.ema_periods:
        out[f"close_ema{p}_dist_atr"] = (close.to_numpy() - out[f"ema{p}"]) / np.where(atr > 0, atr, 1.0)
    # EMA slope: 5-bar change normalized by ATR
    for p in (20, 50):
        ema = pd.Series(out[f"ema{p}"])
        slope = ema.diff(5).fillna(0.0).to_numpy()
        out[f"ema{p}_slope_atr"] = slope / np.where(atr > 0, atr, 1.0)
    ema20, ema50, ema200 = out["ema20"], out["ema50"], out["ema200"]
    out["ema_align_bull"] = ((ema20 > ema50) & (ema50 > ema200)).astype(float)
    out["ema_align_bear"] = ((ema20 < ema50) & (ema50 < ema200)).astype(float)
    return pd.DataFrame(out)


def _wilder_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    return (
        pd.Series(tr)
        .ewm(alpha=1.0 / period, adjust=False, min_periods=1)
        .mean()
        .to_numpy()
    )


def _momentum_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    close = df["close"]
    # Wilder RSI with a proper flat-series fix: 0/0 -> 50 (not 100)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / cfg.feature.rsi_period, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(alpha=1.0 / cfg.feature.rsi_period, adjust=False, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0).fillna(50.0)
    roc = close.pct_change(cfg.feature.roc_period).fillna(0.0)
    momentum = (close - close.shift(10)).fillna(0.0)
    atr = _wilder_atr(df, cfg.feature.atr_period)
    return pd.DataFrame(
        {
            "rsi14": rsi.clip(0, 100).to_numpy(),
            "roc10": roc.to_numpy(),
            "momentum10_atr": momentum.to_numpy() / np.where(atr > 0, atr, 1.0),
        }
    )


def _volatility_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    atr = _wilder_atr(df, cfg.feature.atr_period)
    close = df["close"].to_numpy(float)
    out = {"atr14": atr, "atr_pct": atr / np.where(close > 0, close, 1.0)}
    returns = pd.Series(close).pct_change()
    out["rolling_vol20"] = returns.rolling(cfg.feature.volatility_window, min_periods=1).std().fillna(0).to_numpy()
    # ATR percentile rank over trailing window (causal rank of last value)
    atr_s = pd.Series(atr)
    win = cfg.feature.atr_pct_window
    pct_rank = (
        atr_s.rolling(win, min_periods=20)
        .apply(lambda x: float(np.sum(x <= x[-1]) / len(x)), raw=True)
    )
    out["atr_percentile"] = pct_rank.fillna(0.5).to_numpy()
    rng = (df["high"] - df["low"]).to_numpy(float)
    out["range_expansion"] = pd.Series(rng).rolling(20, min_periods=1).mean().to_numpy()
    out["range_expansion"] = rng / np.where(out["range_expansion"] > 0, out["range_expansion"], 1.0)
    return pd.DataFrame(out)


def _volume_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    v = df["volume"]
    avg = v.rolling(cfg.feature.volume_avg_period, min_periods=1).mean()
    rel = (v / avg.replace(0.0, np.nan)).fillna(1.0)
    pct = v.rolling(200, min_periods=20).rank(pct=True)
    expansion = (v / v.shift(1).replace(0.0, np.nan)).fillna(1.0).clip(0, 10)
    return pd.DataFrame(
        {
            "rel_volume": rel.clip(0, 10).to_numpy(),
            "volume_percentile": pct.fillna(0.5).to_numpy(),
            "volume_expansion": expansion.to_numpy(),
        }
    )


def _structure_features(
    df: pd.DataFrame, events: list[StructureEvent], cfg: EngineConfig
) -> pd.DataFrame:
    n = len(df)
    cols = {
        "hh": np.zeros(n), "hl": np.zeros(n), "lh": np.zeros(n), "ll": np.zeros(n),
        "bos_up": np.zeros(n), "bos_down": np.zeros(n),
        "choch_up": np.zeros(n), "choch_down": np.zeros(n),
        "bars_since_bos": np.full(n, np.nan),
        "dist_last_swing_high_atr": np.full(n, np.nan),
        "dist_last_swing_low_atr": np.full(n, np.nan),
    }
    atr = _wilder_atr(df, cfg.feature.atr_period)
    close = df["close"].to_numpy(float)

    counts = {k: 0 for k in ("hh", "hl", "lh", "ll", "bos_up", "bos_down", "choch_up", "choch_down")}
    last_swing_high = np.nan
    last_swing_low = np.nan
    last_bos_index = None
    pos = 0
    for i in range(n):
        while pos < len(events) and events[pos].index <= i:
            e = events[pos]
            if e.kind in cols:
                counts[e.kind] += 1
                if e.kind == "hh" or e.kind == "lh":
                    last_swing_high = e.level
                if e.kind == "hl" or e.kind == "ll":
                    last_swing_low = e.level
                if e.kind in ("bos_up", "bos_down"):
                    last_bos_index = e.index
            pos += 1
        for k in ("hh", "hl", "lh", "ll", "bos_up", "bos_down", "choch_up", "choch_down"):
            cols[k][i] = counts[k]
        if last_bos_index is not None:
            cols["bars_since_bos"][i] = i - last_bos_index
        if np.isfinite(last_swing_high) and atr[i] > 0:
            cols["dist_last_swing_high_atr"][i] = (close[i] - last_swing_high) / atr[i]
        if np.isfinite(last_swing_low) and atr[i] > 0:
            cols["dist_last_swing_low_atr"][i] = (close[i] - last_swing_low) / atr[i]
    return pd.DataFrame(cols)


def _zone_features(df: pd.DataFrame, zones: list[Zone], cfg: EngineConfig) -> pd.DataFrame:
    """Per-bar distance/strength to nearest supply/demand zone.

    Efficient causal evaluation: each zone's state timeline (touches, breaks)
    is precomputed ONCE with a single forward pass, then sampled per bar via
    prefix counts. A zone is visible at bar i iff created_index <= i; state
    at i reflects only bars in (creation, i].
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    atr = _wilder_atr(df, cfg.feature.atr_period)

    # Precompute per-zone causal state timelines (O(zones * n) total).
    zone_states: list[dict] = []
    for z in zones:
        touches = np.zeros(n, dtype=np.int32)      # cumulative touches by bar
        broken_at = n                                # bar index of break (or n)
        s = z.created_index
        count = 0
        for b in range(s + 1, n):
            if high[b] >= z.lower and low[b] <= z.upper:
                count += 1
            touches[b] = count
        # break detection: first close beyond zone by breach buffer
        for b in range(s + 1, n):
            a = atr[b] if np.isfinite(atr[b]) else 0.0
            if a <= 0:
                continue
            if z.type == "demand" and close[b] < z.lower - cfg.zone.breach_atr * a:
                broken_at = b
                break
            if z.type == "supply" and close[b] > z.upper + cfg.zone.breach_atr * a:
                broken_at = b
                break
        zone_states.append({"z": z, "touches": touches, "broken_at": broken_at})

    cols = {
        "dist_demand_atr": np.full(n, np.nan),
        "demand_strength": np.full(n, np.nan),
        "demand_fresh": np.full(n, np.nan),
        "dist_supply_atr": np.full(n, np.nan),
        "supply_strength": np.full(n, np.nan),
        "supply_fresh": np.full(n, np.nan),
        "in_demand": np.zeros(n),
        "in_supply": np.zeros(n),
    }
    for i in range(n):
        best_demand = None   # (dist_atr, state, zone)
        best_supply = None
        for st in zone_states:
            z = st["z"]
            if z.created_index > i:
                continue
            dist = min(abs(close[i] - z.lower), abs(close[i] - z.upper))
            if atr[i] > 0:
                dist_atr = dist / atr[i]
            else:
                dist_atr = np.inf
            status = "broken" if st["broken_at"] <= i else "tested" if st["touches"][i] > 0 else "fresh"
            # strength via the shared formula with causal status
            tmp = Zone(
                zone_id=z.zone_id, type=z.type, tf=z.tf,
                created_index=z.created_index, created_time=z.created_time,
                upper=z.upper, lower=z.lower, width=z.width,
                width_atr=z.width_atr, origin=z.origin,
                departure_strength_atr=z.departure_strength_atr,
                departure_velocity=z.departure_velocity,
                volume_context=z.volume_context,
                last_event_index=z.last_event_index,
            )
            tmp.status = status
            strength = zone_strength(tmp)
            entry = (dist_atr, status, strength, z)
            if z.type == "demand" and (best_demand is None or dist_atr < best_demand[0]):
                best_demand = entry
            if z.type == "supply" and (best_supply is None or dist_atr < best_supply[0]):
                best_supply = entry
        if best_demand is not None:
            cols["dist_demand_atr"][i] = best_demand[0]
            cols["demand_strength"][i] = best_demand[2]
            cols["demand_fresh"][i] = 1.0 if best_demand[1] == "fresh" else 0.0
            if best_demand[3].lower <= close[i] <= best_demand[3].upper:
                cols["in_demand"][i] = 1.0
        if best_supply is not None:
            cols["dist_supply_atr"][i] = best_supply[0]
            cols["supply_strength"][i] = best_supply[2]
            cols["supply_fresh"][i] = 1.0 if best_supply[1] == "fresh" else 0.0
            if best_supply[3].lower <= close[i] <= best_supply[3].upper:
                cols["in_supply"][i] = 1.0
    return pd.DataFrame(cols)


def _pattern_features(df: pd.DataFrame, patterns: list[PatternEvent], cfg: EngineConfig) -> pd.DataFrame:
    n = len(df)
    cols = {
        "engulfing_bull": np.zeros(n), "engulfing_bear": np.zeros(n),
        "pin_bull": np.zeros(n), "pin_bear": np.zeros(n),
        "inside_bar": np.zeros(n), "outside_bar": np.zeros(n),
        "rejection_bull": np.zeros(n), "rejection_bear": np.zeros(n),
        "pa_conf_bull": np.zeros(n), "pa_conf_bear": np.zeros(n),
        "bars_since_pattern": np.full(n, np.nan),
    }
    last_pat = None
    by_bar: dict[int, list[PatternEvent]] = {}
    for e in patterns:
        by_bar.setdefault(e.index, []).append(e)
    for i in range(n):
        bull = bear = 0.0
        for e in by_bar.get(i, []):
            if e.pattern == "bullish_engulfing":
                cols["engulfing_bull"][i] = 1.0
                bull += 0.5
            elif e.pattern == "bearish_engulfing":
                cols["engulfing_bear"][i] = 1.0
                bear += 0.5
            elif e.pattern == "pin_bar":
                if e.direction == "bullish":
                    cols["pin_bull"][i] = 1.0
                    bull += 0.4
                else:
                    cols["pin_bear"][i] = 1.0
                    bear += 0.4
            elif e.pattern == "inside_bar":
                cols["inside_bar"][i] = 1.0
            elif e.pattern == "outside_bar":
                cols["outside_bar"][i] = 1.0
            elif e.pattern == "rejection_candle":
                if e.direction == "bullish":
                    cols["rejection_bull"][i] = 1.0
                    bull += 0.3
                else:
                    cols["rejection_bear"][i] = 1.0
                    bear += 0.3
            last_pat = i
        cols["pa_conf_bull"][i] = min(bull, 1.0)
        cols["pa_conf_bear"][i] = min(bear, 1.0)
        if last_pat is not None:
            cols["bars_since_pattern"][i] = i - last_pat
    return pd.DataFrame(cols)


def _liquidity_features(df: pd.DataFrame, liq_events, cfg: EngineConfig) -> pd.DataFrame:
    """Per-bar liquidity context from causal events (single pass)."""

    n = len(df)
    cols = {
        "sweep_high_recent": np.zeros(n), "sweep_low_recent": np.zeros(n),
        "equal_highs_recent": np.zeros(n), "equal_lows_recent": np.zeros(n),
        "bars_since_sweep": np.full(n, np.nan),
        "sweep_magnitude_atr": np.full(n, np.nan),
    }
    # Single forward pass: state machine over index-sorted events.
    pos = 0
    last_sweep = None
    last_eq_high_at = -10**9
    last_eq_low_at = -10**9
    for i in range(n):
        while pos < len(liq_events) and liq_events[pos].index <= i:
            e = liq_events[pos]
            if e.kind in ("sweep_high", "sweep_low"):
                last_sweep = e
            elif e.kind == "equal_highs":
                last_eq_high_at = e.index
            elif e.kind == "equal_lows":
                last_eq_low_at = e.index
            pos += 1
        if last_sweep is not None and i - last_sweep.index <= 10:
            if last_sweep.kind == "sweep_high":
                cols["sweep_high_recent"][i] = 1.0
            else:
                cols["sweep_low_recent"][i] = 1.0
            cols["bars_since_sweep"][i] = i - last_sweep.index
            cols["sweep_magnitude_atr"][i] = last_sweep.magnitude_atr
        if i - last_eq_high_at <= 20:
            cols["equal_highs_recent"][i] = 1.0
        if i - last_eq_low_at <= 20:
            cols["equal_lows_recent"][i] = 1.0
    return pd.DataFrame(cols)


def _context_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    t = pd.to_datetime(df["time"])
    hour = t.dt.hour.to_numpy(float)
    dow = t.dt.dayofweek.to_numpy(float)
    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / 24.0),
            "hour_cos": np.cos(2 * np.pi * hour / 24.0),
            "dow_sin": np.sin(2 * np.pi * dow / 7.0),
            "dow_cos": np.cos(2 * np.pi * dow / 7.0),
            "session_asian": ((hour >= 0) & (hour < 8)).astype(float),
            "session_london": ((hour >= 7) & (hour < 16)).astype(float),
            "session_ny": ((hour >= 12) & (hour < 21)).astype(float),
        }
    )


def _regime_features(df: pd.DataFrame, cfg: EngineConfig) -> pd.DataFrame:
    """Causal regime one-hots + component scores from regime v2."""
    from ml_engine.features.regime import classify_regimes, regime_onehots

    reg = classify_regimes(df, cfg.regime)
    out = regime_onehots(reg)
    out["regime_ema_dist_atr"] = reg["ema_dist_atr"]
    return out


def build_features(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cfg: EngineConfig,
    extra_events: dict | None = None,
) -> pd.DataFrame:
    """Assemble the full per-bar feature frame for one symbol/timeframe."""
    parts = [
        _trend_features(df, cfg),
        _momentum_features(df, cfg),
        _volatility_features(df, cfg),
        _volume_features(df, cfg),
        _structure_features(df, structure_events(df, cfg.swing), cfg),
        _zone_features(df, detect_zone_candidates(df, timeframe, cfg.zone), cfg),
        _pattern_features(df, detect_price_action(df, cfg.price_action), cfg),
        _liquidity_features(df, _liquidity_events(df, cfg), cfg),
        _regime_features(df, cfg),
        _context_features(df, cfg),
    ]
    out = pd.concat(parts, axis=1)
    out.insert(0, "symbol", symbol)
    out.insert(1, "timeframe", timeframe)
    return out


def _liquidity_events(df: pd.DataFrame, cfg: EngineConfig):
    from ml_engine.patterns.liquidity import detect_liquidity

    return detect_liquidity(df, cfg.liquidity, cfg.swing)
