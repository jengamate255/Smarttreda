"""Liquidity events: sweeps, equal highs/lows, stop-run rejection.

All events reference *confirmed* swing levels (right-bar lag) and are
emitted at the bar where the evidence completes — no repainting.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import LiquidityConfig, SwingConfig
from ml_engine.structure.swings import swing_events


@dataclass(frozen=True)
class LiquidityEvent:
    kind: str   # sweep_high | sweep_low | equal_highs | equal_lows | rejection
    direction: str  # interpretation: bullish | bearish | neutral
    index: int          # bar where the event becomes visible
    ref_index: int      # swept/compared level bar
    price: float
    reference_level: float
    magnitude_atr: float
    time: pd.Timestamp


def _wilder_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean().to_numpy()


def detect_liquidity(
    df: pd.DataFrame,
    liquidity_config: LiquidityConfig | None = None,
    swing_config: SwingConfig | None = None,
) -> list[LiquidityEvent]:
    cfg = liquidity_config or LiquidityConfig()
    scfg = swing_config or SwingConfig()
    swings = swing_events(df, scfg)
    atr = _wilder_atr(df, cfg.atr_period)

    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    t = df["time"].to_numpy()

    highs = [e for e in swings if e.kind == "swing_high"]
    lows = [e for e in swings if e.kind == "swing_low"]

    events: list[LiquidityEvent] = []

    # Equal highs/lows: consecutive confirmed same-kind swings within tolerance
    eq_tol = cfg.equal_tolerance_atr
    for a, b in itertools.pairwise(highs):
        i = b.visible_index
        if i >= len(atr):
            continue
        tol = eq_tol * atr[i] if atr[i] > 0 else 0.0
        if abs(a.price - b.price) <= tol:
            events.append(
                LiquidityEvent(
                    kind="equal_highs", direction="neutral", index=i,
                    ref_index=b.pivot_index, price=float(b.price),
                    reference_level=float(a.price), magnitude_atr=0.0,
                    time=pd.Timestamp(t[i]),
                )
            )
    for a, b in itertools.pairwise(lows):
        i = b.visible_index
        if i >= len(atr):
            continue
        tol = eq_tol * atr[i] if atr[i] > 0 else 0.0
        if abs(a.price - b.price) <= tol:
            events.append(
                LiquidityEvent(
                    kind="equal_lows", direction="neutral", index=i,
                    ref_index=b.pivot_index, price=float(b.price),
                    reference_level=float(a.price), magnitude_atr=0.0,
                    time=pd.Timestamp(t[i]),
                )
            )

    # Sweeps + rejection: a bar pierces a confirmed level and closes back
    # on the origin side. Visible at the sweeping bar itself.
    n = len(df)
    hi_pos = 0
    lo_pos = 0
    swept_highs: set[int] = set()
    swept_lows: set[int] = set()
    for b in range(n):
        while hi_pos < len(highs) and highs[hi_pos].visible_index <= b:
            hi_pos += 1
        while lo_pos < len(lows) and lows[lo_pos].visible_index <= b:
            lo_pos += 1
        a = atr[b] if b < len(atr) and np.isfinite(atr[b]) else 0.0
        if a <= 0:
            continue
        # sweep of a swing HIGH: wick above, close back below
        for e in reversed(highs[:hi_pos]):
            if e.pivot_index in swept_highs or e.visible_index > b:
                continue
            if h[b] > e.price and c[b] < e.price:
                swept_highs.add(e.pivot_index)
                mag = (h[b] - e.price) / a
                if True:
                    events.append(
                        LiquidityEvent(
                            kind="sweep_high", direction="bearish", index=b,
                            ref_index=e.pivot_index, price=float(c[b]),
                            reference_level=float(e.price),
                            magnitude_atr=float(mag), time=pd.Timestamp(t[b]),
                        )
                    )
                    # rejection: bar closes in lower 30% after the sweep
                    rng = h[b] - l[b]
                    if rng > 0 and (c[b] - l[b]) / rng < 0.3:
                        events.append(
                            LiquidityEvent(
                                kind="rejection", direction="bearish", index=b,
                                ref_index=e.pivot_index, price=float(c[b]),
                                reference_level=float(e.price),
                                magnitude_atr=float(mag), time=pd.Timestamp(t[b]),
                            )
                        )
            break
        # sweep of a swing LOW: wick below, close back above
        for e in reversed(lows[:lo_pos]):
            if e.pivot_index in swept_lows or e.visible_index > b:
                continue
            if l[b] < e.price and c[b] > e.price:
                swept_lows.add(e.pivot_index)
                mag = (e.price - l[b]) / a
                events.append(
                    LiquidityEvent(
                        kind="sweep_low", direction="bullish", index=b,
                        ref_index=e.pivot_index, price=float(c[b]),
                        reference_level=float(e.price),
                        magnitude_atr=float(mag), time=pd.Timestamp(t[b]),
                    )
                )
                rng = h[b] - l[b]
                if rng > 0 and (c[b] - l[b]) / rng > 0.7:
                    events.append(
                        LiquidityEvent(
                            kind="rejection", direction="bullish", index=b,
                            ref_index=e.pivot_index, price=float(c[b]),
                            reference_level=float(e.price),
                            magnitude_atr=float(mag), time=pd.Timestamp(t[b]),
                        )
                    )
            break

    events.sort(key=lambda e: (e.index, e.kind))
    return events


def events_at(events: list[LiquidityEvent], index: int, max_age: int = 0) -> list[LiquidityEvent]:
    return [e for e in events if 0 <= index - e.index <= max_age]
