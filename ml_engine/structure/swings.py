"""Confirmed fractal swing detection with explicit confirmation lag.

A swing high at bar p requires `left` bars before and `right` bars after p
to be strictly lower (ties rejected, mirroring the verified mt5_mcp
implementation). Therefore the swing only becomes VISIBLE at bar
p + right. `swing_events` returns the event at its visibility index so any
downstream consumer is leakage-free by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import SwingConfig


@dataclass(frozen=True)
class SwingEvent:
    kind: str            # "swing_high" | "swing_low"
    pivot_index: int      # bar where the extreme printed
    visible_index: int    # bar where the pivot is confirmed (pivot + right)
    price: float
    time: pd.Timestamp


def _pivots(values: np.ndarray, left: int, right: int, find_max: bool) -> list[int]:
    """Indices of unique-extreme fractal pivots (positional)."""
    n = len(values)
    pivots: list[int] = []
    window = left + right + 1
    if n < window:
        return pivots
    # A pivot at i is only confirmed when i + right < n.
    for i in range(left, n - right):
        seg = values[i - left : i + right + 1]
        extreme = seg.max() if find_max else seg.min()
        if values[i] != extreme:
            continue
        # unique extreme in window (ties rejected)
        count = int((seg == extreme).sum())
        if count == 1:
            pivots.append(i)
    return pivots


def swing_events(df: pd.DataFrame, config: SwingConfig | None = None) -> list[SwingEvent]:
    """All confirmed swing highs/lows, each stamped at its visibility bar."""
    cfg = config or SwingConfig()
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    times = df["time"].to_numpy()
    events: list[SwingEvent] = []
    for i in _pivots(highs, cfg.left, cfg.right, find_max=True):
        events.append(
            SwingEvent(
                kind="swing_high",
                pivot_index=i,
                visible_index=i + cfg.right,
                price=float(highs[i]),
                time=pd.Timestamp(times[i]),
            )
        )
    for i in _pivots(lows, cfg.left, cfg.right, find_max=False):
        events.append(
            SwingEvent(
                kind="swing_low",
                pivot_index=i,
                visible_index=i + cfg.right,
                price=float(lows[i]),
                time=pd.Timestamp(times[i]),
            )
        )
    events.sort(key=lambda e: (e.visible_index, e.pivot_index))
    return events


def confirmed_swings_at(
    events: list[SwingEvent], index: int
) -> tuple[list[SwingEvent], list[SwingEvent]]:
    """Swing highs/lows already visible at bar ``index`` (for stepwise use)."""
    highs = [e for e in events if e.kind == "swing_high" and e.visible_index <= index]
    lows = [e for e in events if e.kind == "swing_low" and e.visible_index <= index]
    return highs, lows
