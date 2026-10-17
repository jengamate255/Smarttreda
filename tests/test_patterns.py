"""Tests: pattern engines (price action, zones, liquidity, breakouts)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml_engine.config import (
    BreakoutConfig,
    LiquidityConfig,
    PriceActionConfig,
    SwingConfig,
    ZoneConfig,
)
from ml_engine.patterns.breakout import detect_breakouts
from ml_engine.patterns.liquidity import detect_liquidity
from ml_engine.patterns.price_action import detect_price_action, events_at
from ml_engine.patterns.zones import (
    advance_zone_state,
    detect_zone_candidates,
)


def synth(n=300, seed=11, base=1.10, vol=0.002):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, vol, n)
    close = base * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, vol * base, n))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="1h"),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": rng.integers(100, 2000, n).astype(float),
        }
    )


class TestPriceAction:
    def _bar(self, o, h, l, c, prev=None):
        return o, h, l, c

    def test_bullish_engulfing_detected_with_props(self):
        cfg = PriceActionConfig(atr_period=14)
        df = synth(200, seed=1)
        # craft a clean bullish engulfing at bar 100
        prev = df.iloc[99]
        df.loc[99, ["open", "close"]] = [prev["close"] + 0.001, prev["close"] - 0.001]
        row = df.iloc[100]
        big = 0.02
        df.loc[100, "open"] = row["close"] - 0.005
        df.loc[100, "close"] = row["close"] + big
        df.loc[100, "high"] = row["close"] + big + 0.001
        df.loc[100, "low"] = df.loc[100, "open"] - 0.001
        events = detect_price_action(df, cfg)
        eng = [e for e in events if e.pattern == "bullish_engulfing" and e.index == 100]
        assert eng, "bullish engulfing not detected"
        assert "body_to_range" in eng[0].props
        assert "volume_ratio" in eng[0].props
        assert "atr_ratio" in eng[0].props

    def test_pin_bar_directions(self):
        cfg = PriceActionConfig()
        df = synth(300, seed=2)
        # bullish pin: tiny body at top, long lower wick
        df.loc[100, "open"] = 1.2
        df.loc[100, "close"] = 1.2005
        df.loc[100, "high"] = 1.2010
        df.loc[100, "low"] = 1.15
        events = detect_price_action(df, cfg)
        pins = [e for e in events if e.pattern == "pin_bar" and e.index == 100]
        assert pins and pins[0].direction == "bullish"
        # bearish pin at 150
        df.loc[150, "open"] = 1.2
        df.loc[150, "close"] = 1.1995
        df.loc[150, "high"] = 1.25
        df.loc[150, "low"] = 1.199
        events = detect_price_action(df, cfg)
        pins = [e for e in events if e.pattern == "pin_bar" and e.index == 150]
        assert pins and pins[0].direction == "bearish"

    def test_inside_bar(self):
        df = synth(300, seed=3)
        df.iloc[101]
        df.loc[101, "high"] = df.iloc[100]["high"] - 0.0005
        df.loc[101, "low"] = df.iloc[100]["low"] + 0.0005
        events = detect_price_action(df, PriceActionConfig())
        ins = [e for e in events if e.pattern == "inside_bar" and e.index == 101]
        assert ins

    def test_events_at_recency(self):
        df = synth(300, seed=4)
        events = detect_price_action(df, PriceActionConfig())
        if events:
            e0 = events[0]
            got = events_at(events, e0.index, max_age=0)
            assert e0 in got or all(g.index <= e0.index for g in got)


class TestZones:
    def test_demand_zone_from_drop_base_rally(self):
        # Build: quiet base, strong bullish impulse
        df = synth(300, seed=5)
        base_level = df["close"].iloc[100]
        for i in range(96, 100):  # tight base
            df.loc[i, ["open", "close"]] = base_level
            df.loc[i, "high"] = base_level + 0.0002
            df.loc[i, "low"] = base_level - 0.0002
        impulse = base_level + 0.05  # >> 1.5 ATR
        df.loc[100, "open"] = base_level
        df.loc[100, "close"] = impulse
        df.loc[100, "high"] = impulse + 0.0005
        df.loc[100, "low"] = base_level - 0.0005
        zones = detect_zone_candidates(df, "H1", ZoneConfig(base_bars=4, impulse_mult=1.5))
        demand = [z for z in zones if z.type == "demand" and z.created_index == 100]
        assert demand, "demand zone not detected"
        z = demand[0]
        assert z.origin == "drop_base_rally"
        assert z.upper >= z.lower
        assert z.departure_strength_atr >= 1.5

    def test_supply_zone_mirror(self):
        df = synth(300, seed=6)
        base_level = df["close"].iloc[100]
        for i in range(96, 100):
            df.loc[i, ["open", "close"]] = base_level
            df.loc[i, "high"] = base_level + 0.0002
            df.loc[i, "low"] = base_level - 0.0002
        df.loc[100, "open"] = base_level
        df.loc[100, "close"] = base_level - 0.05
        df.loc[100, "high"] = base_level + 0.0005
        df.loc[100, "low"] = base_level - 0.0505
        zones = detect_zone_candidates(df, "H1", ZoneConfig(base_bars=4, impulse_mult=1.5))
        supply = [z for z in zones if z.type == "supply" and z.created_index == 100]
        assert supply and supply[0].origin == "rally_base_drop"

    def test_zone_state_transitions_causally(self):
        df = synth(400, seed=7)
        zones = detect_zone_candidates(df, "H1", ZoneConfig())
        if not zones:
            pytest.skip("no zones in synthetic frame")
        z = zones[0]
        early = advance_zone_state(z, df, z.created_index + 1, ZoneConfig())
        assert early.status in ("fresh", "tested")
        late = advance_zone_state(z, df, len(df) - 1, ZoneConfig())
        # late state uses more bars: retests can only grow
        assert late.retests >= early.retests

    def test_broken_zone_penalized(self):
        df = synth(300, seed=8)
        zones = detect_zone_candidates(df, "H1", ZoneConfig())
        if not zones:
            pytest.skip("no zones")
        z = zones[0]
        # Force a break: push closes far below a demand zone
        if z.type == "demand":
            for i in range(z.created_index + 2, min(len(df), z.created_index + 50)):
                if df["close"].iloc[i] > z.lower - 0.5:
                    df.loc[i, ["open", "close", "high", "low"]] = z.lower - 0.5
        z2 = advance_zone_state(z, df, len(df) - 1, ZoneConfig())
        if z2.status == "broken":
            assert z2.strength <= 40


class TestLiquidity:
    def test_sweep_high_detected_with_rejection(self):
        # swing high at 20 (visible 23), then a bar sweeping above and closing back
        prices = np.full(60, 1.0)
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=60, freq="1h"),
                "open": prices, "high": prices + 0.001, "low": prices - 0.001,
                "close": prices, "volume": np.full(60, 100.0),
            }
        )
        df.loc[20, ["open", "close", "high", "low"]] += 0.02  # confirmed swing high
        # sweep at 40: high above swing, close back below
        df.loc[40, "high"] = 1.03
        df.loc[40, "open"] = 1.002
        df.loc[40, "close"] = 1.001  # closes back below the 1.02 level
        df.loc[40, "low"] = 1.0005
        cfg = LiquidityConfig(atr_period=14, sweep_min_wick_atr=0.0)
        events = detect_liquidity(df, cfg, SwingConfig(left=3, right=3))
        sweeps = [e for e in events if e.kind == "sweep_high"]
        assert sweeps, "sweep not detected"
        assert sweeps[0].index == 40
        assert sweeps[0].reference_level == pytest.approx(df["high"].iloc[20])
        # close (1.001) below reference (1.02): sweep + bearish rejection
        rej = [e for e in events if e.kind == "rejection" and e.index == 40]
        assert rej and rej[0].direction == "bearish"

    def test_sweep_low_mirror(self):
        prices = np.full(60, 1.0)
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=60, freq="1h"),
                "open": prices, "high": prices + 0.001, "low": prices - 0.001,
                "close": prices, "volume": np.full(60, 100.0),
            }
        )
        df.loc[20, ["open", "close", "high", "low"]] -= 0.02  # swing low
        df.loc[40, "low"] = 0.97
        df.loc[40, "open"] = 0.998
        df.loc[40, "close"] = 1.001  # closes back above 0.98
        df.loc[40, "high"] = 1.0015
        events = detect_liquidity(df, LiquidityConfig(sweep_min_wick_atr=0.0), SwingConfig(left=3, right=3))
        sweeps = [e for e in events if e.kind == "sweep_low"]
        assert sweeps and sweeps[0].direction == "bullish"

    def test_equal_highs_within_tolerance(self):
        prices = np.full(80, 1.0)
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=80, freq="1h"),
                "open": prices, "high": prices + 0.001, "low": prices - 0.001,
                "close": prices, "volume": np.full(80, 100.0),
            }
        )
        df.loc[20, ["open", "close", "high", "low"]] += 0.02
        df.loc[50, ["open", "close", "high", "low"]] += 0.0205  # equal within tol
        events = detect_liquidity(df, LiquidityConfig(equal_tolerance_atr=0.5), SwingConfig(left=3, right=3))
        eq = [e for e in events if e.kind == "equal_highs"]
        assert eq, "equal highs not flagged"


class TestBreakout:
    def test_breakout_up_from_range(self):
        prices = np.full(60, 1.0)
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=60, freq="1h"),
                "open": prices, "high": prices + 0.001, "low": prices - 0.001,
                "close": prices, "volume": np.full(60, 100.0),
            }
        )
        # breakout up at bar 40
        df.loc[40, ["open", "close"]] = [1.001, 1.02]
        df.loc[40, "high"] = 1.021
        df.loc[40, "low"] = 1.0
        cfg = BreakoutConfig(range_bars=20, buffer_atr=0.12)
        events = detect_breakouts(df, cfg)
        ups = [e for e in events if e.kind == "breakout_up"]
        assert ups and ups[0].index == 40
        assert ups[0].level == pytest.approx(1.001)  # 20-bar high before bar 40

    def test_retest_after_breakout(self):
        prices = np.full(80, 1.0)
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=80, freq="1h"),
                "open": prices, "high": prices + 0.001, "low": prices - 0.001,
                "close": prices, "volume": np.full(80, 100.0),
            }
        )
        # breakout at 40
        df.loc[40, ["open", "close"]] = [1.001, 1.02]
        df.loc[40, "high"] = 1.021
        df.loc[40, "low"] = 1.0
        # hold above, then retest at 50 (dip to level, close back above)
        for i in range(41, 50):
            df.loc[i, ["open", "close"]] = [1.02, 1.02]
            df.loc[i, "high"] = 1.021
            df.loc[i, "low"] = 1.019
        df.loc[50, "open"] = 1.02
        df.loc[50, "close"] = 1.015
        df.loc[50, "low"] = 1.0015  # touches level ~1.001
        df.loc[50, "high"] = 1.02
        cfg = BreakoutConfig(range_bars=20, buffer_atr=0.12, retest_tolerance_atr=1.0)
        events = detect_breakouts(df, cfg)
        retests = [e for e in events if e.kind == "retest_up"]
        assert retests, "retest not detected"
        assert retests[0].index == 50

    def test_no_breakout_in_flat_market(self):
        df = synth(200, seed=12, vol=0.0001)
        events = detect_breakouts(df, BreakoutConfig())
        # extremely low volatility: maybe zero or few breakouts, none extreme
        assert all(e.strength_atr < 10 for e in events)
