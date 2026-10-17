"""Breakout detection: range breakouts and retests.

A breakout at bar b: close beyond the prior `range_bars` extreme by
`buffer_atr` x ATR. A retest: within `retest_max_bars`, price returns to the
broken level (within tolerance) and closes back on the breakout side.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import BreakoutConfig


@dataclass(frozen=True)
class BreakoutEvent:
    kind: str               # breakout_up | breakout_down | retest_up | retest_down
    direction: str          # bullish | bearish
    index: int
    price: float
    level: float
    strength_atr: float     # close distance beyond level / ATR
    time: pd.Timestamp
    retest_of: int | None = None  # breakout index this retest belongs to


def detect_breakouts(
    df: pd.DataFrame, config: BreakoutConfig | None = None
) -> list[BreakoutEvent]:
    cfg = config or BreakoutConfig()
    n = len(df)
    if n < cfg.range_bars + 2:
        return []

    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    t = df["time"]
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / cfg.atr_period, adjust=False, min_periods=1).mean().to_numpy()

    roll_high = pd.Series(h).rolling(cfg.range_bars).max().shift(1).to_numpy()
    roll_low = pd.Series(l).rolling(cfg.range_bars).min().shift(1).to_numpy()

    events: list[BreakoutEvent] = []
    open_breakouts: list[dict] = []

    for b in range(cfg.range_bars, n):
        a = atr[b] if np.isfinite(atr[b]) else 0.0
        if a <= 0 or not np.isfinite(roll_high[b]):
            continue
        level_hi = float(roll_high[b])
        level_lo = float(roll_low[b])

        broke_up = c[b] > level_hi + cfg.buffer_atr * a
        broke_down = c[b] < level_lo - cfg.buffer_atr * a

        if broke_up:
            strength = (c[b] - level_hi) / a
            events.append(
                BreakoutEvent(
                    kind="breakout_up", direction="bullish", index=b,
                    price=float(c[b]), level=level_hi,
                    strength_atr=float(strength), time=pd.Timestamp(t[b]),
                )
            )
            open_breakouts.append(
                {"index": b, "level": level_hi, "direction": "up", "retested": False}
            )
        elif broke_down:
            strength = (level_lo - c[b]) / a
            events.append(
                BreakoutEvent(
                    kind="breakout_down", direction="bearish", index=b,
                    price=float(c[b]), level=level_lo,
                    strength_atr=float(strength), time=pd.Timestamp(t[b]),
                )
            )
            open_breakouts.append(
                {"index": b, "level": level_lo, "direction": "down", "retested": False}
            )

        # Retests of still-open breakouts (first touch within window)
        for ob in open_breakouts:
            if ob["retested"] or b - ob["index"] > cfg.retest_max_bars:
                continue
            tol = cfg.retest_tolerance_atr * a
            if ob["direction"] == "up":
                touched = l[b] <= ob["level"] + tol
                held = c[b] > ob["level"] - tol
                if touched and held and b != ob["index"]:
                    ob["retested"] = True
                    events.append(
                        BreakoutEvent(
                            kind="retest_up", direction="bullish", index=b,
                            price=float(c[b]), level=ob["level"],
                            strength_atr=0.0, time=pd.Timestamp(t[b]),
                            retest_of=ob["index"],
                        )
                    )
            else:
                touched = h[b] >= ob["level"] - tol
                held = c[b] < ob["level"] + tol
                if touched and held and b != ob["index"]:
                    ob["retested"] = True
                    events.append(
                        BreakoutEvent(
                            kind="retest_down", direction="bearish", index=b,
                            price=float(c[b]), level=ob["level"],
                            strength_atr=0.0, time=pd.Timestamp(t[b]),
                            retest_of=ob["index"],
                        )
                    )

        # Drop expired breakouts
        open_breakouts = [
            ob for ob in open_breakouts if b - ob["index"] <= cfg.retest_max_bars
        ]

    return events
