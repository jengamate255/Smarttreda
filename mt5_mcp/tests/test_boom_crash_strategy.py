from __future__ import annotations

import pytest

from mt5_mcp.tools import boom_crash_strategy as strategy


def _crash_candles(broken: bool = False) -> list[dict]:
    rows = []
    price = 120.0
    for i in range(30):
        rows.append({"time": i, "open": price, "high": price + 0.4, "low": price - 0.4, "close": price})
    rows.append({"time": 30, "open": 120.0, "high": 121.0, "low": 80.0, "close": 81.0})
    price = 82.0
    for i in range(31, 60):
        if broken and i == 58:
            rows.append({"time": i, "open": price, "high": price + 0.4, "low": 78.0, "close": 77.0})
            price = 77.0
        else:
            rows.append({"time": i, "open": price, "high": price + 0.4, "low": price, "close": price})
            price += (95.0 - 82.0) / 28.0
    return rows


def test_crash_levels_are_on_correct_sides(monkeypatch):
    monkeypatch.setattr(strategy, "_candles_any", lambda symbol, count: _crash_candles())
    signal = strategy.detect_spike("CRASH900", lookback_minutes=60, min_spike_ticks=8)

    assert signal is not None
    assert signal.sl_price < signal.spike_low < signal.entry_price < signal.tp_price
    assert signal.spike_age_candles >= 0
    assert signal.atr > 0


def test_broken_crash_extreme_is_watch_only(monkeypatch):
    monkeypatch.setattr(strategy, "_candles_any", lambda symbol, count: _crash_candles(broken=True))
    signal = strategy.detect_spike("CRASH900", lookback_minutes=60, min_spike_ticks=8)

    assert signal is not None
    assert signal.confidence == 0
    assert signal.current_price <= signal.spike_low


def test_trail_sl_price_is_armed_chandelier_reference(monkeypatch):
    """The trail reference sits between entry and +1R once armed (risk > ATR)."""
    monkeypatch.setattr(strategy, "_candles_any", lambda symbol, count: _crash_candles())
    signal = strategy.detect_spike("CRASH900", lookback_minutes=60, min_spike_ticks=8)

    assert signal is not None
    # Long: trail reference = entry + (risk - 1.0 x ATR); risk >> ATR with a
    # 2-ATR SL buffer, so the reference must be above entry but below +1R.
    risk = abs(signal.entry_price - signal.sl_price)
    assert signal.trail_sl_price > signal.entry_price
    assert signal.trail_sl_price < signal.entry_price + risk
    # 1R partial exit level is unchanged (entry + 1R).
    assert signal.partial_tp_price == pytest.approx(signal.entry_price + risk)
