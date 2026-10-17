"""Tests: setup composer and label engine."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml_engine.config import DEFAULT_CONFIG
from ml_engine.setups.composer import Setup, compose_setups
from ml_engine.setups.labels import label_setup, label_setups


def trending_with_pullbacks(n=600, seed=61):
    """Uptrend with deep pullbacks that retrace into prior demand zones.

    Cycle: 30-bar rally (+0.18%), 22-bar deep pullback retracing ~80% of the
    rally leg, 8-bar tight base, then a >=1.5 ATR impulse relaunching the
    next leg. The pullback returns price into the previous impulse's zone.
    """
    rng = np.random.default_rng(seed)
    prices = []
    level = 1.10
    for i in range(n):
        phase = i % 60
        if phase < 30:
            level += 0.0006  # rally: +1.8% over 30 bars
        elif phase < 52:
            level -= 0.0005  # pullback: retraces ~73% of the rally
        else:
            level += 0.00002  # tight base
        prices.append(level)
    close = np.array(prices)
    open_ = close + 0.00003
    wick = np.abs(rng.normal(0, 0.0003, n))
    noise = rng.normal(0, 0.00005, n)
    close = close + noise
    open_ = open_ + noise
    # impulse at cycle start: large body launching the rally
    for i in range(60, n, 60):
        prev = close[i - 1]
        close[i] = prev + 0.0035  # >> 1.5 ATR (ATR ~ 0.001)
        open_[i] = prev + 0.0001
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="1h"),
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": rng.integers(100, 2000, n).astype(float),
        }
    )


class TestComposer:
    def test_composes_setups_on_uptrend(self):
        df = trending_with_pullbacks()
        cfg = DEFAULT_CONFIG
        setups = compose_setups(df, "EURUSD", "H1", cfg)
        assert len(setups) > 0, "no setups composed on trending series"
        for s in setups:
            assert s.risk_reward >= cfg.setup.min_risk_reward
            if s.direction == "long":
                assert s.stop < s.entry < s.target
            else:
                assert s.target < s.entry < s.stop
            assert s.zone_context or s.liquidity_context
            assert s.price_action_context  # confirmation required

    def test_setups_carry_context(self):
        df = trending_with_pullbacks()
        setups = compose_setups(df, "EURUSD", "H1", DEFAULT_CONFIG)
        s = setups[0]
        assert s.setup_id.startswith("EURUSD-H1")
        assert isinstance(s.time, pd.Timestamp)
        assert s.structure_context["bias"] in (-1.0, 0.0, 1.0)

    def test_htf_bias_gate_blocks_conflicts(self):
        df = trending_with_pullbacks()
        cfg = DEFAULT_CONFIG
        # Strong bearish HTF bias must block long setups
        htf_bias = pd.Series(-1.0, index=df.index)
        setups = compose_setups(df, "EURUSD", "H1", cfg, htf_bias=htf_bias)
        assert all(s.direction == "short" or s.structure_context["htf_bias"] != 1.0 for s in setups)
        longs = [s for s in setups if s.direction == "long"]
        assert not longs, "bearish HTF bias must veto long setups"


class TestLabels:
    def _mk(self, direction, entry, stop, target):
        return Setup(
            setup_id="T-1", symbol="EURUSD", time=pd.Timestamp("2025-01-01"),
            index=10, direction=direction, entry=entry, stop=stop, target=target,
            risk_reward=abs(target - entry) / abs(entry - stop),
        )

    def test_tp_first(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        close = np.full(40, 1.10)
        high = close + 0.0005
        low = close - 0.0005
        high[15] = 1.125  # target hit at bar 15
        cfg = DEFAULT_CONFIG
        res = label_setup(s, high, low, close, cfg)
        assert res.tp_first and not res.sl_first
        assert res.time_to_tp_bars == 5
        assert res.r_multiple == pytest.approx(2.0)

    def test_sl_first(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        close = np.full(40, 1.10)
        high = close + 0.0005
        low = close - 0.0005
        low[12] = 1.085  # stop hit at bar 12
        res = label_setup(s, high, low, close, DEFAULT_CONFIG)
        assert res.sl_first and not res.tp_first
        assert res.r_multiple == pytest.approx(-1.0)

    def test_same_bar_ambiguity_is_pessimistic(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        close = np.full(40, 1.10)
        high = close + 0.0005
        low = close - 0.0005
        high[13] = 1.13
        low[13] = 1.08  # both in same bar
        res = label_setup(s, high, low, close, DEFAULT_CONFIG)
        assert res.sl_first  # SL wins the ambiguity
        assert res.r_multiple == pytest.approx(-1.0)

    def test_timeout_uses_close(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        n = 40
        close = np.linspace(1.10, 1.105, n)
        high = close + 0.0001
        low = close - 0.0001
        res = label_setup(s, high, low, close, DEFAULT_CONFIG)
        assert res.neither
        assert res.r_multiple == pytest.approx((close[-1] - 1.10) / 0.01, rel=1e-6)

    def test_mfe_mae_tracking(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        close = np.full(40, 1.10)
        high = close + 0.0005
        low = close - 0.0005
        high[14] = 1.115  # +1.5R favorable
        low[14] = 1.095   # -0.5R adverse in the same bar
        res = label_setup(s, high, low, close, DEFAULT_CONFIG)
        assert res.mfe_r >= 1.5 - 1e-9  # float noise tolerance at the boundary
        assert res.mae_r >= 0.5 - 1e-9
        assert res.win_1r and not res.win_2r

    def test_labels_use_only_future_bars(self):
        s = self._mk("long", 1.10, 1.09, 1.12)
        n = 40
        close = np.full(n, 1.10)
        high = close + 0.0005
        low = close - 0.0005
        # put the target touch BEFORE the setup bar: must be ignored
        high[5] = 1.13
        res = label_setup(s, high, low, close, DEFAULT_CONFIG)
        assert not res.tp_first  # pre-entry excursion ignored

    def test_label_setups_frame(self):
        df = trending_with_pullbacks()
        cfg = DEFAULT_CONFIG
        setups = compose_setups(df, "EURUSD", "H1", cfg)
        if not setups:
            pytest.skip("no setups in fixture")
        labels = label_setups(setups, df, cfg)
        assert len(labels) == len(setups)
        assert labels.index.is_unique
        for col in ("win_1r", "win_2r", "r_multiple", "mfe_r", "mae_r"):
            assert col in labels.columns
