"""Deterministic market structure: swings, HH/HL/LH/LL, BOS, CHoCH.

Anti-leakage note: fractal swing detection with `window` bars of confirmation
on each side requires `window` FUTURE bars. To keep the features causal we
*shift the confirmed swing flags forward by `window` bars* before deriving
structure state, i.e. a swing only becomes visible `window` bars after the
extreme. This trades a small confirmation delay for zero look-ahead bias.

State machine (fully documented so behavior is reproducible):
  - A confirmed swing high/lows is the extreme of a (2*window+1)-bar window.
  - HH: new swing high above previous swing high. LH: below.
  - HL: new swing low above previous swing low. LL: below.
  - trend = +1 if the latest confirmed swing sequence is HH or HL, -1 for LH/LL.
  - BOS: close trades beyond the most recent confirmed opposite-side extreme.
  - CHoCH: BOS against the current trend (potential change of character).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def swing_fractals(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        if high[i] >= np.max(high[i - window : i + window + 1]):
            swing_high[i] = True
        if low[i] <= np.min(low[i - window : i + window + 1]):
            swing_low[i] = True

    out = pd.DataFrame(index=df.index)
    out["swing_high"] = swing_high
    out["swing_low"] = swing_low
    out["swing_high_confirmed"] = pd.Series(swing_high, index=df.index).shift(window).fillna(False)
    out["swing_low_confirmed"] = pd.Series(swing_low, index=df.index).shift(window).fillna(False)
    return out


def build_structure(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        if high[i] >= np.max(high[i - window : i + window + 1]):
            swing_high[i] = True
        if low[i] <= np.min(low[i - window : i + window + 1]):
            swing_low[i] = True

    label = np.full(n, 0, dtype=int)  # -1 downtrend, 0 undecided, +1 uptrend
    hh = np.zeros(n, dtype=int)
    hl = np.zeros(n, dtype=int)
    lh = np.zeros(n, dtype=int)
    ll = np.zeros(n, dtype=int)
    bos_up = np.zeros(n, dtype=int)
    bos_down = np.zeros(n, dtype=int)
    choch_up = np.zeros(n, dtype=int)
    choch_down = np.zeros(n, dtype=int)

    prev_sh: float | None = None
    prev_sl: float | None = None
    last_sh: float | None = None
    last_sl: float | None = None
    last_swing_kind: str | None = None
    trend = 0

    def on_new_swing(kind: str, price: float, idx: int):
        nonlocal trend, last_swing_kind
        if kind == "H":
            if prev_sh is not None:
                hh[idx] = 1 if price > prev_sh else 0
                lh[idx] = 1 if price < prev_sh else 0
            trend = 1 if (prev_sl is not None and price > prev_sl) else trend
        else:
            if prev_sl is not None:
                hl[idx] = 1 if price > prev_sl else 0
                ll[idx] = 1 if price < prev_sl else 0
            trend = -1 if (prev_sh is not None and price < prev_sh) else trend
        last_swing_kind = kind

    for i in range(n):
        if i >= window:
            idx = i - window
            if swing_high[idx] and not swing_low[idx]:
                prev_sh = last_sh
                last_sh = high[idx]
                on_new_swing("H", high[idx], idx)
            if swing_low[idx] and not swing_high[idx]:
                prev_sl = last_sl
                last_sl = low[idx]
                on_new_swing("L", low[idx], idx)

        if last_sh is not None and close[i] > last_sh:
            bos_up[i] = 1
            if trend <= 0:
                choch_up[i] = 1
                trend = 1
        if last_sl is not None and close[i] < last_sl:
            bos_down[i] = 1
            if trend >= 0:
                choch_down[i] = 1
                trend = -1

        label[i] = trend

    out = pd.DataFrame(index=df.index)
    out["structure_label"] = label
    out["hh"] = hh
    out["hl"] = hl
    out["lh"] = lh
    out["ll"] = ll
    out["bos_up"] = bos_up
    out["bos_down"] = bos_down
    out["choch_up"] = choch_up
    out["choch_down"] = choch_down
    return out
