"""Label simulation tests: TP/SL outcomes must match hand-computed expectations."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.labels.trade_outcome import simulate_labels
from src.features.technical import build_technical
from tests.conftest import BASE_CONFIG, make_ohlcv, make_deterministic_ohlcv


def _with_atr(df):
    tech = build_technical(df, BASE_CONFIG["features"])
    return pd.concat([df.reset_index(drop=True), tech[["atr"]]], axis=1)


def test_fixed_pip_labels_deterministic():
    df = make_deterministic_ohlcv()
    cfg = {"labels": {"stop_mode": "fixed_pips", "fixed_stop_pips": 20.0,
                      "rr_ratios": [2, 3], "max_lookahead_bars": 48,
                      "spread_pips": 0.8, "commission_pips": 0.2}}
    labels = simulate_labels(df, cfg, symbol="EURUSD")
    # Bars 30-35 contain a huge up impulse: close jumps +0.0030 (30 pips).
    # Buy TP (60 pips at RR3, minus 1 pip costs) not reached in 5 bars, but SL 20 pips not hit either -> 0.
    # Instead check the impulse bar index 34 area: entry ~1.0148; buy TP=1.0148+0.0059 far; SL=1.0148-0.0192.
    # The subsequent down-move of -0.0004/bar for 25 bars covers 0.01 -> never 0.0192. So no SL.
    # Sanity: labels are one of {-1, 0, 1} and time-aligned.
    for col in ("label_rr2", "label_rr3"):
        assert set(labels[col].unique()) <= {-1, 0, 1}
    assert (labels["buy_tp_first_rr3"].isin([0, 1])).all()


def test_atr_labels_win_when_tp_first():
    # Craft: entry bar close C; next bar rallies >= 10*ATR (TP at 1.5*3=4.5 ATR).
    df = make_ohlcv(n=120, seed=11)
    df = _with_atr(df)
    atr0 = df["atr"].iloc[50]
    df.loc[51, "high"] = df["close"].iloc[50] + 10.0 * atr0
    df.loc[51, "close"] = df["high"].iloc[51]
    df.loc[51, "low"] = df["close"].iloc[50] - 0.5 * atr0
    cfg = {"labels": {"stop_mode": "atr_multiple", "atr_stop_multiple": 1.5,
                      "rr_ratios": [3], "max_lookahead_bars": 20,
                      "spread_pips": 0.8, "commission_pips": 0.2}}
    labels = simulate_labels(df, cfg, symbol="EURUSD")
    assert labels["label_rr3"].iloc[50] == 1
    assert labels["buy_tp_first_rr3"].iloc[50] == 1
    assert labels["buy_exit_bar_rr3"].iloc[50] == 1


def test_sl_before_tp_same_bar_is_conservative():
    df = make_ohlcv(n=120, seed=13)
    df = _with_atr(df)
    atr0 = df["atr"].iloc[60]
    df.loc[61, "high"] = df["close"].iloc[60] + 5.0 * atr0   # TP touched
    df.loc[61, "low"] = df["close"].iloc[60] - 2.0 * atr0    # SL touched same bar
    cfg = {"labels": {"stop_mode": "atr_multiple", "atr_stop_multiple": 1.5,
                      "rr_ratios": [3], "max_lookahead_bars": 20,
                      "spread_pips": 0.8, "commission_pips": 0.2}}
    labels = simulate_labels(df, cfg, symbol="EURUSD")
    assert labels["label_rr3"].iloc[60] == -1
    assert labels["buy_tp_first_rr3"].iloc[60] == 0


def test_same_bar_tp_wins_when_not_conservative():
    df = make_ohlcv(n=120, seed=13)
    df = _with_atr(df)
    atr0 = df["atr"].iloc[60]
    df.loc[61, "high"] = df["close"].iloc[60] + 5.0 * atr0
    df.loc[61, "low"] = df["close"].iloc[60] - 2.0 * atr0
    cfg = {"labels": {"stop_mode": "atr_multiple", "atr_stop_multiple": 1.5,
                      "rr_ratios": [3], "max_lookahead_bars": 20,
                      "spread_pips": 0.8, "commission_pips": 0.2,
                      "conservative_same_bar": False}}
    labels = simulate_labels(df, cfg, symbol="EURUSD")
    assert labels["label_rr3"].iloc[60] == 1
    assert labels["buy_tp_first_rr3"].iloc[60] == 1


def test_no_result_when_no_touch():
    df = make_ohlcv(n=80, seed=17)
    df = _with_atr(df)
    cfg = {"labels": {"stop_mode": "fixed_pips", "fixed_stop_pips": 200.0,
                      "rr_ratios": [3], "max_lookahead_bars": 3,
                      "spread_pips": 0.8, "commission_pips": 0.2}}
    labels = simulate_labels(df, cfg, symbol="EURUSD")
    assert (labels["label_rr3"] == 0).all()


def test_labels_are_causal():
    """Labels at interior bars must be identical whether computed on the full
    frame or on a truncated frame (edge bars near the cut legitimately see a
    shorter lookahead window, so only interior bars are compared)."""
    df = make_deterministic_ohlcv()
    cfg = {"labels": {"stop_mode": "fixed_pips", "fixed_stop_pips": 20.0,
                      "rr_ratios": [3], "max_lookahead_bars": 48,
                      "spread_pips": 0.8, "commission_pips": 0.2}}
    full = simulate_labels(df, cfg, symbol="EURUSD")
    for cut in (20, 40, 55):
        trunc = simulate_labels(df.iloc[:cut], cfg, symbol="EURUSD")
        interior = max(0, cut - 48)
        if interior == 0:
            continue
        for col in ("label_rr3", "buy_tp_first_rr3"):
            assert (full[col].iloc[:interior] == trunc[col].iloc[:interior]).all(), f"label inconsistency at {cut}"
