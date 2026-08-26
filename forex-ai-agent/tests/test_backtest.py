"""Backtest engine + metrics tests on crafted signals."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics
from tests.conftest import make_ohlcv


def _uptrend_frame(n=100):
    df = make_ohlcv(n=n, seed=5, drift=0.02)
    return df.reset_index(drop=True)


def test_tp_hit_gives_positive_pnl():
    df = _uptrend_frame()
    entry = df["close"].iloc[10]
    signals = [{
        "time": df["time"].iloc[10], "side": "BUY",
        "sl": entry - 0.002, "tp": entry + 0.002, "spread_pips": 0.8,
    }]
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.pnl > 0
    # SL/TP are absolute prices; the engine charges the 0.8 pip spread at
    # entry, so realized R = (0.002 - 0.00008) / (0.002 + 0.00008) = 0.9231
    assert trade.pnl_r == pytest.approx(0.9231, abs=0.01)


def test_sl_hit_gives_negative_pnl():
    df = make_ohlcv(n=100, seed=9, drift=-0.02).reset_index(drop=True)
    entry = df["close"].iloc[10]
    df.loc[11, "high"] = entry + 0.005
    df.loc[11, "open"] = df["close"].iloc[10]
    df.loc[11, "close"] = entry + 0.003
    signals = [{
        "time": df["time"].iloc[10], "side": "SELL",
        "sl": entry + 0.002, "tp": entry - 0.002, "spread_pips": 0.8,
    }]
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    trade = result.trades[0]
    assert trade.exit_reason == "SL"
    assert trade.pnl < 0


def test_same_bar_sl_tp_conservative():
    df = make_ohlcv(n=100, seed=11).reset_index(drop=True)
    entry = df["close"].iloc[10]
    # Next bar is enormous: both SL and TP inside its range -> SL must win.
    df.loc[11, "high"] = entry + 0.005
    df.loc[11, "low"] = entry - 0.005
    signals = [{
        "time": df["time"].iloc[10], "side": "BUY",
        "sl": entry - 0.002, "tp": entry + 0.002, "spread_pips": 0.0,
    }]
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    assert result.trades[0].exit_reason == "SL"


def test_historical_spread_mode_charges_bar_spread():
    df = _uptrend_frame()
    entry = df["close"].iloc[10]
    df.loc[10, "spread"] = 3.0  # wide historical spread at the entry bar (pips)
    signals = [{
        "time": df["time"].iloc[10], "side": "BUY",
        "sl": entry - 0.002, "tp": entry + 0.002, "spread_pips": 0.0,
    }]
    hist = run_backtest(df, lambda f: signals, cfg={"spread_mode": "historical"},
                        symbol="EURUSD", timeframe="M15")
    sig = run_backtest(df, lambda f: signals, cfg={},
                       symbol="EURUSD", timeframe="M15")
    assert hist.trades[0].pnl < sig.trades[0].pnl
    assert hist.trades[0].pnl == pytest.approx(sig.trades[0].pnl - 3.0 * 0.0001)


def test_session_whitelist_filters_signals():
    df = make_ohlcv(n=100, seed=5, drift=0.02, start="2023-01-02").reset_index(drop=True)
    # Bar 10 -> 02:30 UTC (hour 2, Asian session). Bar 40 -> 10:00 UTC (London).
    sigs = [
        {"time": df["time"].iloc[10], "side": "BUY",
         "sl": df["close"].iloc[10] - 0.002, "tp": df["close"].iloc[10] + 0.05,
         "spread_pips": 0.8},
        {"time": df["time"].iloc[40], "side": "BUY",
         "sl": df["close"].iloc[40] - 0.002, "tp": df["close"].iloc[40] + 0.05,
         "spread_pips": 0.8},
    ]
    cfg = {"session_whitelist": ["london"],
           "sessions": {"asian": [0, 8], "london": [7, 16], "new_york": [12, 21]}}
    result = run_backtest(df, lambda f: sigs, cfg=cfg, symbol="EURUSD", timeframe="M15")
    assert len(result.trades) == 1
    assert result.trades[0].entry == pytest.approx(df["close"].iloc[40] + 0.8 * 0.0001)


def test_max_simultaneous_trades_enforced():
    df = _uptrend_frame(60)
    entry = df["close"].iloc[10]
    signals = []
    for i in (10, 11, 12):
        signals.append({
            "time": df["time"].iloc[i], "side": "BUY",
            "sl": entry - 0.001, "tp": entry + 0.010, "spread_pips": 0.8,
        })
    result = run_backtest(df, lambda f: signals, cfg={"max_simultaneous_trades": 1},
                          symbol="EURUSD", timeframe="M15")
    assert len(result.trades) == 1


def test_metrics_compute_all_spec_fields():
    df = make_ohlcv(n=400, seed=19).reset_index(drop=True)
    signals = []
    for i in range(50, len(df) - 5, 7):
        e = df["close"].iloc[i]
        signals.append({
            "time": df["time"].iloc[i], "side": "BUY" if i % 2 == 0 else "SELL",
            "sl": e - 0.002, "tp": e + 0.004, "spread_pips": 0.8,
        })
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    metrics = compute_metrics(result.trades, result.equity_curve)
    for key in ("total_trades", "win_rate", "loss_rate", "profit_factor", "expectancy_r",
                "average_r", "max_drawdown_pct", "sharpe", "sortino", "average_win_r",
                "average_loss_r", "largest_win_r", "largest_loss_r", "max_win_streak",
                "max_loss_streak", "calmar"):
        assert key in metrics, key
    assert metrics["total_trades"] == len(result.trades)


def test_equity_curve_tracks_mark_to_market():
    df = _uptrend_frame(60)
    entry = df["close"].iloc[10]
    # Force a steady rise after entry so the held position never dips below entry.
    for i in range(11, len(df)):
        df.loc[i, "open"] = df["close"].iloc[i - 1]
        df.loc[i, "close"] = df["open"].iloc[i] + 0.0005
        df.loc[i, "high"] = df["close"].iloc[i] + 0.0001
        df.loc[i, "low"] = df["open"].iloc[i] - 0.0001
    signals = [{
        "time": df["time"].iloc[10], "side": "BUY",
        "sl": entry - 0.001, "tp": entry + 0.05, "spread_pips": 0.0,
    }]
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    equity = result.equity_curve
    assert len(equity) == len(df)
    assert (equity["equity"].diff().dropna() > -1e-9).all()  # up market, position held, mtm >= entry


def test_signals_without_sl_tp_get_atr_stops():
    df = _uptrend_frame(80)
    signals = [{"time": df["time"].iloc[20], "side": "BUY"}]
    result = run_backtest(df, lambda f: signals, cfg={}, symbol="EURUSD", timeframe="M15")
    assert len(result.trades) == 1
    assert result.trades[0].sl is not None and result.trades[0].tp is not None
