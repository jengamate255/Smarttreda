"""Trade-outcome labels.

Instead of "next candle up/down", each label simulates an actual trade:
    entry = close of bar t
    SL / TP distances from configuration (fixed pips or ATR multiples)
    exit checked against future bars (t+1 .. t+max_lookahead_bars):
        TP touched first  -> WIN
        SL touched first  -> LOSS
        neither           -> NO_RESULT (0)

Costs (spread + commission) are deducted by pulling TP and SL boundaries
toward the entry price, matching the backtest engine's treatment of costs.

Both directions are simulated at every bar:
    label_rr{N}            buy-side outcome: 1 WIN, -1 LOSS, 0 NO_RESULT
    buy_tp_first_rr{N}     binary "buy TP before SL"
    sell_tp_first_rr{N}    binary "sell TP before SL"
    buy_exit_bar_rr{N}     bars until first touch (0 = none)

Same-bar TP+SL ambiguity resolves in favor of SL by default (conservative);
set labels.conservative_same_bar: false to resolve in favor of TP.
Implementation is vectorized over the lookahead horizon.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils import pip_size


def _stop_distance(df: pd.DataFrame, cfg: dict, symbol: str) -> np.ndarray:
    labels_cfg = cfg.get("labels", {})
    mode = labels_cfg.get("stop_mode", "atr_multiple")
    if mode == "fixed_pips":
        pips = float(labels_cfg.get("fixed_stop_pips", 20.0))
        return np.full(len(df), pips)
    if mode == "atr_multiple":
        mult = float(labels_cfg.get("atr_stop_multiple", 1.5))
        if "atr" not in df.columns:
            raise ValueError("atr column required for atr_multiple stops; build technical features first")
        return df["atr"].to_numpy() * mult / pip_size(symbol)
    raise ValueError(f"unknown stop_mode {mode}")


def simulate_labels(
    df: pd.DataFrame,
    cfg: dict | None = None,
    symbol: str = "EURUSD",
) -> pd.DataFrame:
    cfg = cfg or {}
    labels_cfg = cfg.get("labels", {})
    rr_ratios = [int(r) for r in labels_cfg.get("rr_ratios", [2, 3, 4])]
    horizon = int(labels_cfg.get("max_lookahead_bars", 96))

    n = len(df)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    stop_pips = _stop_distance(df, cfg, symbol)
    ps = pip_size(symbol)

    columns: dict[str, np.ndarray] = {}
    for rr in rr_ratios:
        columns[f"label_rr{rr}"] = np.zeros(n, dtype=np.int64)
        columns[f"buy_tp_first_rr{rr}"] = np.zeros(n, dtype=np.int64)
        columns[f"sell_tp_first_rr{rr}"] = np.zeros(n, dtype=np.int64)
        columns[f"buy_exit_bar_rr{rr}"] = np.zeros(n, dtype=np.int64)

    idx = np.arange(n)
    conservative_same_bar = bool(labels_cfg.get("conservative_same_bar", True))
    for rr in rr_ratios:
        tp = stop_pips * rr
        cost = float(labels_cfg.get("spread_pips", 0.8)) + float(labels_cfg.get("commission_pips", 0.2))

        buy_tp = close + (tp - cost) * ps
        buy_sl = close - np.maximum(stop_pips - cost, 0.0) * ps
        sell_tp = close - (tp - cost) * ps
        sell_sl = close + np.maximum(stop_pips - cost, 0.0) * ps

        decided = np.zeros(n, dtype=bool)
        buy_tp_first = np.zeros(n, dtype=bool)
        sell_tp_first = np.zeros(n, dtype=bool)
        label = np.zeros(n, dtype=np.int64)
        exit_bar = np.zeros(n, dtype=np.int64)

        for j in range(1, horizon + 1):
            if decided.all() or j >= n:
                break
            cur = ~decided

            bs = np.zeros(n, dtype=bool)
            bsl = np.zeros(n, dtype=bool)
            ss = np.zeros(n, dtype=bool)
            ssl = np.zeros(n, dtype=bool)
            bs[: n - j] = high[j:] >= buy_tp[:-j]
            bsl[: n - j] = low[j:] <= buy_sl[:-j]
            ss[: n - j] = low[j:] <= sell_tp[:-j]
            ssl[: n - j] = high[j:] >= sell_sl[:-j]

            bs = bs & cur
            bsl = bsl & cur
            ss = ss & cur
            ssl = ssl & cur

            if conservative_same_bar:
                buy_sl_first = bsl
                buy_tp_only = bs & ~bsl
                sell_sl_first = ssl
                sell_tp_only = ss & ~ssl
            else:
                buy_tp_only = bs
                buy_sl_first = bsl & ~bs
                sell_tp_only = ss
                sell_sl_first = ssl & ~ss

            label[buy_tp_only] = 1
            label[buy_sl_first] = -1
            buy_tp_first[buy_tp_only] = True
            sell_tp_first[sell_tp_only] = True
            exit_bar[buy_tp_only | buy_sl_first | sell_tp_only | sell_sl_first] = j
            decided |= buy_tp_only | buy_sl_first | sell_tp_only | sell_sl_first

        columns[f"label_rr{rr}"] = label
        columns[f"buy_tp_first_rr{rr}"] = buy_tp_first.astype(np.int64)
        columns[f"sell_tp_first_rr{rr}"] = sell_tp_first.astype(np.int64)
        columns[f"buy_exit_bar_rr{rr}"] = exit_bar

    return pd.DataFrame(columns, index=df.index)
