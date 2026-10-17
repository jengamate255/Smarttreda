"""High-timeframe bias engine: bullish / bearish / neutral from structure.

Bias is CONTEXT, not a signal: the decision engine uses it to veto
counter-bias trades but never to initiate them.

Definition (deterministic):
- Swing sequence: last up to N confirmed swing labels (HH/HL bullish,
  LH/LL bearish) voted with recency weighting.
- EMA context: EMA fast vs slow separation and slope (secondary signal).
- Composite: structure vote is primary; EMA context shifts confidence.
All inputs are causal (confirmed pivots only).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import EngineConfig
from ml_engine.structure.events import StructureEvent, structure_events


@dataclass
class BiasResult:
    direction: str          # "bullish" | "bearish" | "neutral"
    strength: float         # 0..1
    structure_vote: float   # -1..1 (swing-label majority)
    ema_context: float      # -1..1
    evidence: list[str]
    last_swing_high: float | None
    last_swing_low: float | None


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


def _structure_vote(events: list[StructureEvent], index: int, lookback: int = 6) -> tuple[float, list[str]]:
    """Recency-weighted vote over the last ``lookback`` confirmed labels."""
    labels: list[tuple[int, float]] = []  # (index, score)
    for e in events:
        if e.index > index:
            break
        if e.kind in ("hh", "hl", "lh", "ll"):
            score = 1.0 if e.kind in ("hh", "hl") else -1.0
            labels.append((e.index, score))
    if not labels:
        return 0.0, []
    labels = labels[-lookback:]
    weights = np.arange(1, len(labels) + 1, dtype=float)
    vote = float(np.dot(weights, [s for _, s in labels]) / max(weights.sum(), 1e-12))
    evidence = [f"{k}@{i}" for i, k in ((i, "B" if s > 0 else "b") for i, s in labels)]
    return vote, evidence


def _ema_context(df: pd.DataFrame, index: int, cfg: EngineConfig) -> float:
    closes = df["close"].to_numpy(float)
    atr = _wilder_atr(df, cfg.feature.atr_period)
    fast = _ema(closes, cfg.regime.ema_fast)
    slow = _ema(closes, cfg.regime.ema_slow)
    if index >= len(closes):
        index = len(closes) - 1
    sep = (fast[index] - slow[index]) / (atr[index] if atr[index] > 0 else 1.0)
    slope = (fast[index] - fast[max(0, index - 5)]) / (atr[index] if atr[index] > 0 else 1.0)
    raw = 0.6 * np.tanh(sep) + 0.4 * np.tanh(slope * 3.0)
    return float(np.clip(raw, -1.0, 1.0))


def htf_bias_at(df: pd.DataFrame, index: int, cfg: EngineConfig) -> BiasResult:
    """Bias visible at bar ``index``."""
    events = structure_events(df, cfg.swing)
    vote, evidence = _structure_vote(events, index)
    ema = _ema_context(df, index, cfg)

    if abs(vote) >= 0.15:
        direction = "bullish" if vote > 0 else "bearish"
        strength = float(min(1.0, abs(vote) + 0.15 * max(0.0, ema * np.sign(vote))))
    elif abs(ema) >= 0.25:
        direction = "bullish" if ema > 0 else "bearish"
        strength = float(min(0.6, abs(ema)))
    else:
        direction = "neutral"
        strength = 0.0

    # last confirmed levels for invalidation text
    last_sh = None
    last_sl = None
    for e in events:
        if e.index > index:
            break
        if e.kind == "hh" or e.kind == "lh":
            last_sh = e.level
        if e.kind == "hl" or e.kind == "ll":
            last_sl = e.level

    return BiasResult(
        direction=direction,
        strength=strength,
        structure_vote=vote,
        ema_context=ema,
        evidence=evidence,
        last_swing_high=last_sh,
        last_swing_low=last_sl,
    )


def htf_bias_series(df: pd.DataFrame, cfg: EngineConfig) -> pd.Series:
    """Per-bar causal bias as +1 / 0 / -1 (aligned to the frame index).

    Used to gate base-timeframe setups without leakage: each bar's value
    uses only structure confirmed at or before that bar.
    """
    events = structure_events(df, cfg.swing)
    n = len(df)
    out = np.zeros(n)
    labels: list[tuple[int, float]] = []
    ema_ctx = np.zeros(n)
    # precompute EMA context per bar (causal by construction)
    closes = df["close"].to_numpy(float)
    atr = _wilder_atr(df, cfg.feature.atr_period)
    fast = _ema(closes, cfg.regime.ema_fast)
    slow = _ema(closes, cfg.regime.ema_slow)
    for i in range(n):
        sep = (fast[i] - slow[i]) / (atr[i] if atr[i] > 0 else 1.0)
        slope = (fast[i] - fast[max(0, i - 5)]) / (atr[i] if atr[i] > 0 else 1.0)
        ema_ctx[i] = float(np.clip(0.6 * np.tanh(sep) + 0.4 * np.tanh(slope * 3.0), -1, 1))

    pos = 0
    for i in range(n):
        while pos < len(events) and events[pos].index <= i:
            e = events[pos]
            if e.kind in ("hh", "hl", "lh", "ll"):
                labels.append((e.index, 1.0 if e.kind in ("hh", "hl") else -1.0))
            pos += 1
        if labels:
            recent = labels[-6:]
            weights = np.arange(1, len(recent) + 1, dtype=float)
            vote = float(np.dot(weights, [s for _, s in recent]) / max(weights.sum(), 1e-12))
        else:
            vote = 0.0
        if abs(vote) >= 0.15:
            out[i] = 1.0 if vote > 0 else -1.0
        elif abs(ema_ctx[i]) >= 0.25:
            out[i] = 1.0 if ema_ctx[i] > 0 else -1.0
    return pd.Series(out, index=df.index)
