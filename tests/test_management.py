"""Tests for the trade management simulator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml_engine.backtest.management import (
    ManagementResult,
    _hold_to_target,
    _trailing_atr_stop,
    management_summary,
    simulate_management,
)
from ml_engine.setups.composer import Setup


def _synth_data(n: int = 200) -> dict:
    rng = np.random.default_rng(42)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0003, n))
    h = close + rng.uniform(0.0001, 0.0005, n)
    l = close - rng.uniform(0.0001, 0.0005, n)
    o = close + rng.uniform(-0.0001, 0.0001, n)
    atr = pd.Series(np.maximum(h - l, np.abs(h - np.roll(close, 1)))).ewm(span=14, min_periods=1).mean().to_numpy()
    return {
        "close": close, "high": h, "low": l, "open": o,
        "atr": atr,
        "time": pd.date_range("2025-01-01", periods=n, freq="1h"),
    }


def _make_setup(idx: int = 100) -> Setup:
    return Setup(
        setup_id="TEST-EURUSD-L-100",
        symbol="EURUSD",
        time=pd.Timestamp("2025-01-05 04:00"),
        index=idx,
        direction="long",
        entry=1.1000,
        stop=1.0950,
        target=1.1100,
        risk_reward=2.0,
        timeframes=["H1"],
    )


class TestHoldToTarget:
    def test_tp_hit(self):
        # Manually construct data where TP is hit after start
        n = 50
        high = np.full(n, 1.105)
        high[15] = 1.115  # TP at 1.110 hit at bar 15 (after start=10)
        low = np.full(n, 1.099)
        r, exit_b, _exit_p = _hold_to_target(
            high, low, 1.1000, 1.0950, 1.1100, "long", 50, 10,
        )
        assert r == pytest.approx(2.0)
        assert exit_b == 15

    def test_sl_hit(self):
        n = 50
        high = np.full(n, 1.105)
        low = np.full(n, 1.099)
        low[13] = 1.094  # SL at 1.095 hit at bar 13
        r, _exit_b, _exit_p = _hold_to_target(
            high, low, 1.1000, 1.0950, 1.1100, "long", 50, 10,
        )
        assert r == pytest.approx(-1.0)

    def test_timeout(self):
        n = 50
        high = np.full(n, 1.102)
        low = np.full(n, 1.099)
        r, exit_b, _exit_p = _hold_to_target(
            high, low, 1.1000, 1.0950, 1.1100, "long", 20, 30,
        )
        # Timeout at bar 49
        assert exit_b == 49
        assert isinstance(r, float)


class TestTrailingStop:
    def test_trail_wins(self):
        # Price goes up steadily, trailing stop locks in profit
        n = 50
        close = np.linspace(1.100, 1.115, n)
        high = close + 0.0003
        low = close - 0.0003
        atr = np.full(n, 0.003)
        r, _exit_b, _exit_p = _trailing_atr_stop(
            high, low, 1.1000, 1.0950, "long", 2.0, atr, 50, 10,
        )
        assert r > 1.0  # trailed and locked in > 1R

    def test_trail_losers(self):
        # Price drops immediately
        n = 50
        close = np.linspace(1.100, 1.090, n)
        high = close + 0.0003
        low = close - 0.0003
        atr = np.full(n, 0.003)
        r, _exit_b, _exit_p = _trailing_atr_stop(
            high, low, 1.1000, 1.0950, "long", 2.0, atr, 50, 10,
        )
        assert r < 0


class TestSimulateManagement:
    def test_returns_three_rules(self):
        data = _synth_data(200)
        setup = _make_setup(100)
        results = simulate_management(
            setup, data["high"], data["low"], data["close"], data["atr"],
        )
        assert len(results) == 3
        rules = [res.rule for res in results]
        assert "hold_to_target" in rules
        assert any("trailing" in r for r in rules)
        assert any("time_" in r for r in rules)

    def test_all_results_have_same_mfe_mae(self):
        data = _synth_data(200)
        setup = _make_setup(100)
        results = simulate_management(
            setup, data["high"], data["low"], data["close"], data["atr"],
        )
        mfe_vals = [r.max_favorable_r for r in results]
        mae_vals = [r.max_adverse_r for r in results]
        assert len(set(mfe_vals)) == 1  # all same
        assert len(set(mae_vals)) == 1


class TestManagementSummary:
    def test_summary_structure(self):
        results = [
            ManagementResult("S1", "hold_to_target", 1.0, 110, 1.101, 1.5, 0.3, 10),
            ManagementResult("S1", "trailing_2atr", 0.5, 108, 1.1005, 1.5, 0.3, 8),
            ManagementResult("S2", "hold_to_target", -1.0, 105, 1.095, 0.2, 1.0, 5),
            ManagementResult("S2", "trailing_2atr", -0.3, 104, 1.097, 0.2, 1.0, 4),
        ]
        s = management_summary(results)
        assert "hold_to_target" in s
        assert s["hold_to_target"]["n"] == 2
        assert s["hold_to_target"]["mean_r"] == 0.0  # (1.0 + -1.0)/2
