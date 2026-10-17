"""Market regime engine v2: multi-signal, causal, testable.

Regimes: TRENDING_UP, TRENDING_DOWN, RANGING, CHOPPY, TRANSITION,
HIGH_VOLATILITY, LOW_VOLATILITY.

Signals combined (all trailing-only):
- Wilder ADX (trend strength)
- EMA fast/slow separation + slope (trend direction)
- ATR percentile (volatility level)
- Range compression/expansion (recent range vs longer baseline)
- Swing-structure alternation (chop detection)
- Recent regime flips (transition detection)

Every output column is computable from bars <= t. The composite regime is
deterministic and documented below; raw component scores are also returned
so ML can learn regime *interactions* rather than a single label.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from ml_engine.config import RegimeConfig


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


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    return (
        pd.Series(series)
        .ewm(span=period, adjust=False, min_periods=1)
        .mean()
        .to_numpy()
    )


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Causal Wilder ADX: returns DataFrame{adx, plus_di, minus_di}."""
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(df)
    up = np.zeros(n)
    down = np.zeros(n)
    for i in range(1, n):
        up[i] = h[i] - h[i - 1]
        down[i] = l[i - 1] - l[i]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    tr[0] = h[0] - l[0] if h[0] > l[0] else 1e-12

    def _smooth(x: np.ndarray) -> np.ndarray:
        return pd.Series(x).ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean().to_numpy()

    atr_s = _smooth(tr)
    plus_s = _smooth(plus_dm)
    minus_s = _smooth(minus_dm)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_s / atr_s
        minus_di = 100.0 * minus_s / atr_s
    denom = plus_di + minus_di
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)
    adx = _smooth(dx)
    return pd.DataFrame(
        {
            "adx": np.nan_to_num(adx, nan=0.0),
            "plus_di": np.nan_to_num(plus_di, nan=0.0),
            "minus_di": np.nan_to_num(minus_di, nan=0.0),
        }
    )


def _atr_percentile(atr: np.ndarray, window: int, min_periods: int = 20) -> np.ndarray:
    s = pd.Series(atr)
    out = s.rolling(window, min_periods=min_periods).apply(
        lambda x: float(np.sum(x <= x[-1]) / len(x)), raw=True
    )
    return out.fillna(0.5).to_numpy()


def classify_regimes(
    df: pd.DataFrame, cfg: RegimeConfig | None = None
) -> pd.DataFrame:
    """Full regime frame: composite label + component scores + one-hots."""
    cfg = cfg or RegimeConfig()
    n = len(df)
    close = df["close"]
    atr = _wilder_atr(df, cfg.atr_period)

    adx_frame = wilder_adx(df, cfg.adx_period)
    adx = adx_frame["adx"].to_numpy()
    plus_di = adx_frame["plus_di"].to_numpy()
    minus_di = adx_frame["minus_di"].to_numpy()

    atr_pct = _atr_percentile(atr, cfg.atr_pct_window) * 100.0

    ema_fast = _ema(close.to_numpy(float), cfg.ema_fast)
    ema_slow = _ema(close.to_numpy(float), cfg.ema_slow)
    ema_dist = (ema_fast - ema_slow) / np.where(atr > 0, atr, 1.0)

    # Range compression/expansion: 10-bar mean range vs 60-bar baseline
    rng = (df["high"] - df["low"]).to_numpy(float)
    short_range = pd.Series(rng).rolling(10, min_periods=1).mean().to_numpy()
    long_range = pd.Series(rng).rolling(60, min_periods=10).mean().to_numpy()
    compression = np.where(long_range > 0, short_range / long_range, 1.0)

    # Chop: directional efficiency of trailing 20-bar move (|net| / path)
    net_move = np.abs(close.to_numpy() - pd.Series(close).shift(20).to_numpy())
    path = pd.Series(np.abs(close.diff())).rolling(20, min_periods=1).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        efficiency = np.where(path > 0, net_move / path, 1.0)  # 1 = trend, 0 = chop

    # Swing-structure alternation (via confirmed fractal pivots, causal):
    from ml_engine.structure.swings import swing_events

    alternation = np.zeros(n)
    swings = swing_events(df)
    highs = [e for e in swings if e.kind == "swing_high"]
    lows = [e for e in swings if e.kind == "swing_low"]
    hseq: list[float] = []
    lseq: list[float] = []
    hi = lo = 0
    for i in range(n):
        while hi < len(highs) and highs[hi].visible_index <= i:
            hseq.append(highs[hi].price)
            hi += 1
        while lo < len(lows) and lows[lo].visible_index <= i:
            lseq.append(lows[lo].price)
            lo += 1
        # alternation = fraction of last 6 same-kind pivots that flip direction
        seq = hseq[-3:] if len(hseq) >= len(lseq) else lseq[-3:]
        if len(seq) >= 3:
            flips = sum(1 for a, b in itertools.pairwise(seq) if (a - b) * (seq[0] - seq[1]) < 0)
            alternation[i] = flips / max(1, len(seq) - 1)
    # chop score high when ADX weak AND efficiency low AND alternation high
    adx_norm = np.clip(adx / 25.0, 0.0, 1.0)
    chop_score = (1.0 - adx_norm) * 0.5 + (1.0 - efficiency) * 0.3 + alternation * 0.2

    # ---- composite regimes (deterministic, documented) --------------------
    trend_regime = np.full(n, "ranging", dtype=object)
    trend_regime[(adx > 25) & (plus_di > minus_di) & (ema_dist > 0.3)] = "trending_up"
    trend_regime[(adx > 25) & (minus_di > plus_di) & (ema_dist < -0.3)] = "trending_down"
    trend_regime[chop_score > 0.62] = "choppy"

    vol_regime = np.full(n, "normal_vol", dtype=object)
    vol_regime[atr_pct >= cfg.high_vol_pct] = "high_volatility"
    vol_regime[atr_pct <= cfg.low_vol_pct] = "low_volatility"

    # transition: trend regime flipped within the last 10 bars
    transition = np.zeros(n)
    flipped_at = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if trend_regime[i] != trend_regime[i - 1]:
            flipped_at[i] = True
    flip_idx = np.where(flipped_at)[0]
    for fi in flip_idx:
        transition[fi : min(n, fi + 10)] = 1.0
    # also mark transitions when EMA distance crosses zero
    cross_up = (ema_dist > 0) & (pd.Series(ema_dist).shift(1).fillna(0).to_numpy() <= 0)
    cross_down = (ema_dist < 0) & (pd.Series(ema_dist).shift(1).fillna(0).to_numpy() >= 0)
    for i in np.where(cross_up | cross_down)[0]:
        transition[i : min(n, i + 10)] = 1.0

    composite = np.full(n, "ranging", dtype=object)
    composite[trend_regime == "trending_up"] = "trending_up"
    composite[trend_regime == "trending_down"] = "trending_down"
    composite[(transition > 0) & ~(trend_regime == "choppy")] = "transition"
    composite[trend_regime == "choppy"] = "choppy"

    return pd.DataFrame(
        {
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "atr_percentile": atr_pct,
            "compression": compression,
            "efficiency": efficiency,
            "chop_score": chop_score,
            "transition": transition,
            "ema_dist_atr": ema_dist,
            "trend_regime": trend_regime,
            "vol_regime": vol_regime,
            "composite_regime": composite,
        }
    )


def regime_onehots(reg: pd.DataFrame) -> pd.DataFrame:
    """One-hot features for ML (composite + vol + transition flag)."""
    out = pd.DataFrame(index=reg.index)
    for label in ("trending_up", "trending_down", "ranging", "choppy", "transition"):
        out[f"regime_{label}"] = (reg["composite_regime"] == label).astype(float)
    for label in ("high_volatility", "low_volatility", "normal_vol"):
        out[f"vol_{label}"] = (reg["vol_regime"] == label).astype(float)
    out["regime_chop_score"] = reg["chop_score"].to_numpy()
    out["regime_compression"] = reg["compression"].to_numpy()
    out["regime_efficiency"] = reg["efficiency"].to_numpy()
    out["regime_adx"] = (reg["adx"] / 100.0).to_numpy()
    return out
