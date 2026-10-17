"""Outcome labeling: MFE/MAE, R-barriers, first-touch resolution.

Labels use future bars by definition — they are computed AFTER feature
extraction and joined to setups only via setup_id. The dataset builder
guarantees label columns never enter the feature matrix.

Barrier model (contract C6): from the bar AFTER the setup trigger, future
highs/lows decide which level is hit first. If both stop and target are
touched within the same future bar, the STOP wins (pessimistic).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ml_engine.config import EngineConfig
from ml_engine.setups.composer import Setup


@dataclass
class LabelResult:
    setup_id: str
    mfe_r: float
    mae_r: float
    time_to_tp_bars: int | None
    time_to_sl_bars: int | None
    tp_first: bool
    sl_first: bool
    neither: bool
    win_1r: bool
    win_2r: bool
    win_3r: bool
    win_4r: bool
    r_multiple: float
    exit_bars: int
    max_hold_bars: int

    def to_dict(self) -> dict:
        return asdict(self)


def label_setup(
    setup: Setup,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    cfg: EngineConfig,
) -> LabelResult:
    """Resolve one setup's outcome from future bars (deterministic)."""
    i = setup.index
    n = len(close)
    risk = abs(setup.entry - setup.stop)
    if risk <= 0:
        return LabelResult(
            setup.setup_id, 0.0, 0.0, None, None, False, False, True,
            False, False, False, False, 0.0, 0, cfg.label.max_hold_bars,
        )

    long = setup.direction == "long"
    tp = setup.target
    sl = setup.stop

    mfe = 0.0
    mae = 0.0
    tp_bar = None
    sl_bar = None
    exit_bar = min(n - 1, i + cfg.label.max_hold_bars)
    r_multiple = 0.0

    for b in range(i + 1, min(n, i + 1 + cfg.label.max_hold_bars)):
        hi = high[b]
        lo = low[b]
        fav = (hi - setup.entry) if long else (setup.entry - lo)
        adv = (setup.entry - lo) if long else (hi - setup.entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)

        hit_sl = (lo <= sl) if long else (hi >= sl)
        hit_tp = (hi >= tp) if long else (lo <= tp)

        if hit_sl and hit_tp:
            # Ambiguity resolved pessimistically: stop first
            sl_bar = b
            exit_bar = b
            r_multiple = -1.0
            break
        if hit_sl:
            sl_bar = b
            exit_bar = b
            r_multiple = -1.0
            break
        if hit_tp:
            tp_bar = b
            exit_bar = b
            r_multiple = float(abs(tp - setup.entry) / risk)
            break
    else:
        # Timeout: exit at last close
        b = min(n - 1, i + cfg.label.max_hold_bars)
        exit_close = close[b]
        move = (exit_close - setup.entry) if long else (setup.entry - exit_close)
        r_multiple = move / risk

    mfe_r = mfe / risk
    mae_r = mae / risk

    return LabelResult(
        setup_id=setup.setup_id,
        mfe_r=float(mfe_r),
        mae_r=float(mae_r),
        time_to_tp_bars=tp_bar - i if tp_bar is not None else None,
        time_to_sl_bars=sl_bar - i if sl_bar is not None else None,
        tp_first=tp_bar is not None and (sl_bar is None or tp_bar <= sl_bar),
        sl_first=sl_bar is not None and (tp_bar is None or sl_bar < tp_bar),
        neither=tp_bar is None and sl_bar is None,
        win_1r=bool(mfe_r >= 1.0),
        win_2r=bool(mfe_r >= 2.0),
        win_3r=bool(mfe_r >= 3.0),
        win_4r=bool(mfe_r >= 4.0),
        r_multiple=float(r_multiple),
        exit_bars=int(exit_bar - i),
        max_hold_bars=cfg.label.max_hold_bars,
    )


def label_setups(
    setups: list[Setup], df: pd.DataFrame, cfg: EngineConfig
) -> pd.DataFrame:
    """Label all setups of one frame; returns DataFrame keyed by setup_id."""
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    rows = [label_setup(s, high, low, close, cfg).to_dict() for s in setups]
    return pd.DataFrame(rows).set_index("setup_id")
