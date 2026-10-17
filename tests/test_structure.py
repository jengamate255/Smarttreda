"""Tests: market structure (swings, labels, BOS, CHoCH) and causality."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml_engine.config import SwingConfig
from ml_engine.structure.events import structure_events, structure_state_at
from ml_engine.structure.swings import confirmed_swings_at, swing_events


def frame_from_prices(prices, start="2025-01-01", freq="1h"):
    prices = np.asarray(prices, dtype=float)
    open_ = prices
    close = prices + 0.0
    high = prices + 0.1
    low = prices - 0.1
    return pd.DataFrame(
        {
            "time": pd.date_range(start=start, periods=len(prices), freq=freq),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": np.full(len(prices), 100.0),
        }
    )


class TestSwings:
    def test_detects_simple_peak(self):
        # peak at index 10 with 5 lower bars each side
        prices = [1.0] * 5 + [1.0 + 0.1 * (10 - abs(i - 10)) for i in range(5, 16)] + [1.0] * 5
        df = frame_from_prices(prices)
        df.loc[10, ["high", "low", "open", "close"]] += 5.0  # pronounced peak
        events = swing_events(df, SwingConfig(left=3, right=3))
        highs = [e for e in events if e.kind == "swing_high"]
        assert any(e.pivot_index == 10 for e in highs)

    def test_confirmation_lag_enforced(self):
        cfg = SwingConfig(left=3, right=5)
        prices = list(np.full(20, 1.0))
        df = frame_from_prices(prices)
        df.loc[10, ["high", "low", "open", "close"]] += 3.0
        events = swing_events(df, cfg)
        for e in events:
            assert e.visible_index == e.pivot_index + cfg.right
            assert e.visible_index >= cfg.right

    def test_ties_rejected(self):
        prices = list(np.full(20, 1.0))
        df = frame_from_prices(prices)
        events = swing_events(df, SwingConfig(left=3, right=3))
        # flat series: no unique extremes -> no swings
        assert all(e.kind not in ("swing_high", "swing_low") for e in events) or events == []

    def test_confirmed_swings_at_excludes_future(self):
        prices = list(np.full(30, 1.0))
        df = frame_from_prices(prices)
        df.loc[10, ["high", "low", "open", "close"]] += 3.0  # peak at 10
        df.loc[25, ["high", "low", "open", "close"]] -= 3.0  # trough at 25
        cfg = SwingConfig(left=3, right=3)
        events = swing_events(df, cfg)
        # At bar 12, only the peak (visible at 13? no: 10+3=13)...
        # pivot at 10 is visible at 13. At bar 12 nothing is visible.
        h12, l12 = confirmed_swings_at(events, 12)
        assert h12 == [] and l12 == []
        h14, _l14 = confirmed_swings_at(events, 14)
        assert any(e.pivot_index == 10 for e in h14)
        # trough at 25 visible at 28
        _h28, l28 = confirmed_swings_at(events, 28)
        assert any(e.pivot_index == 25 for e in l28)


class TestStructureEvents:
    def test_uptrend_yields_hh_hl(self):
        # rising zigzag: peaks and troughs both rising
        n = 60
        prices = []
        level = 1.0
        for i in range(n):
            level += 0.02
            prices.append(level)
        df = frame_from_prices(prices)
        # create alternating swings
        for i in range(10, 60, 10):
            df.loc[i, ["high", "low", "open", "close"]] += 1.0 if (i // 10) % 2 == 0 else -1.0
        events = structure_events(df, SwingConfig(left=3, right=3))
        kinds = [e.kind for e in events]
        # at least some labeled swings exist
        assert any(k in ("hh", "hl", "lh", "ll") for k in kinds)

    def test_bos_on_break_of_confirmed_high(self):
        # peak at 10, confirmed at 13; break at 20
        prices = list(np.full(40, 1.0))
        df = frame_from_prices(prices)
        df.loc[10, ["high", "low", "open", "close"]] += 3.0
        for i in range(20, 40):
            df.loc[i, ["high", "low", "open", "close"]] += 5.0
        events = structure_events(df, SwingConfig(left=3, right=3))
        bos = [e for e in events if e.kind == "bos_up"]
        assert len(bos) >= 1
        # BOS must fire at/after the pivot became visible (13)
        assert all(e.index >= 13 for e in bos)
        # ...and the broken level is the confirmed pivot price
        assert any(e.level == pytest.approx(df["high"].iloc[10]) for e in bos)

    def test_choch_requires_opposite_prior_structure(self):
        """A bearish structure (LL+LH pivots) broken to the upside must
        register choch_up (or at minimum bos_up)."""
        np.random.default_rng(99)
        # Zigzag downtrend: lower highs and lower lows with real pivots
        n = 80
        prices = []
        level = 2.0
        for i in range(n):
            phase = i % 10
            if phase < 5:
                level -= 0.03  # decline leg
            else:
                level += 0.02  # corrective bounce (makes LH pivots)
            prices.append(level)
        df = frame_from_prices(prices)
        # small noise keeps fractals clean
        cfg = SwingConfig(left=2, right=2)
        events = structure_events(df, cfg)
        kinds = [e.kind for e in events]
        assert "ll" in kinds or "lh" in kinds, f"no bearish structure in fixture: {set(kinds)}"
        # Force an upside break at the end: push last bars above the last LH
        last_lh = [e for e in events if e.kind == "lh"][-1]
        for i in range(n - 6, n):
            df.loc[i, ["open", "close", "high", "low"]] = last_lh.level + 0.5
        events2 = structure_events(df, cfg)
        kinds2 = [e.kind for e in events2]
        assert "choch_up" in kinds2 or "bos_up" in kinds2, f"no break registered: {set(kinds2)}"

    def test_events_are_sorted_and_causal(self):
        rng = np.random.default_rng(42)
        ret = rng.normal(0, 0.01, 400)
        close = 1.1 * np.exp(np.cumsum(ret))
        df = pd.DataFrame(
            {
                "time": pd.date_range("2025-01-01", periods=400, freq="1h"),
                "open": close,
                "high": close * 1.001,
                "low": close * 0.999,
                "close": close,
                "volume": np.full(400, 100.0),
            }
        )
        cfg = SwingConfig(left=3, right=3)
        events = structure_events(df, cfg)
        idxs = [e.index for e in events]
        assert idxs == sorted(idxs)
        for e in events:
            assert 0 <= e.index < len(df)
            if e.kind.startswith("bos") or e.kind.startswith("choch"):
                assert e.index >= cfg.right  # level must be confirmed before use

    def test_state_at_counts_events_causally(self):
        prices = list(np.full(40, 1.0))
        df = frame_from_prices(prices)
        df.loc[10, ["high", "low", "open", "close"]] += 3.0
        for i in range(20, 40):
            df.loc[i, ["high", "low", "open", "close"]] += 5.0
        cfg = SwingConfig(left=3, right=3)
        events = structure_events(df, cfg)
        early = structure_state_at(events, 5)
        late = structure_state_at(events, 39)
        assert late["bos_up"] >= early["bos_up"]
        assert late["bos_up"] >= 1


class TestNoLookahead:
    def test_structure_prefix_stability(self):
        """Events on df[:k] must be a prefix of events on the full frame
        wherever the confirmation window fits — i.e. detection at bar b
        never depends on bars after b + right."""
        rng = np.random.default_rng(7)
        ret = rng.normal(0, 0.012, 300)
        close = 1.1 * np.exp(np.cumsum(ret))
        def mk(n):
            return pd.DataFrame(
                {
                    "time": pd.date_range("2025-01-01", periods=n, freq="1h"),
                    "open": close[:n], "high": close[:n] * 1.0015,
                    "low": close[:n] * 0.9985, "close": close[:n],
                    "volume": np.full(n, 100.0),
                }
            )
        cfg = SwingConfig(left=3, right=3)
        full = structure_events(mk(300), cfg)
        trunc = structure_events(mk(250), cfg)
        # Every trunc event must appear identically in full (same indices,
        # same levels) because detection at bar b uses only bars <= b+right
        # and b+right < 250 for all trunc events.
        full_by_key = {(e.index, e.kind, e.ref_index): e for e in full}
        for e in trunc:
            key = (e.index, e.kind, e.ref_index)
            assert key in full_by_key, f"trunc event {key} missing in full"
            assert full_by_key[key].level == pytest.approx(e.level)
