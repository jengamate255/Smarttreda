"""Feature engineering tests, including anti-leakage verification.

The core anti-leakage property: a feature computed at bar t must be identical
to the same feature computed on a frame truncated at bar t (no future bars).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import build_features
from src.features.price_action import build_price_action
from src.features.sessions import session_features
from src.features.structure import build_structure, swing_fractals
from src.features.supply_demand import build_supply_demand
from src.features.technical import build_technical
from tests.conftest import BASE_CONFIG, make_ohlcv


def assert_no_lookahead(builder, df, cfg=None, check_idx=None):
    """Recompute features on the truncated frame; values must match at check_idx."""
    full = builder(df, cfg) if cfg is not None else builder(df)
    check_idx = check_idx or len(df) - 5
    truncated = builder(df.iloc[: check_idx + 1], cfg) if cfg is not None else builder(df.iloc[: check_idx + 1])
    for col in full.columns:
        if col in ("time",):
            continue
        full_val = full[col].iloc[check_idx]
        trunc_val = truncated[col].iloc[check_idx]
        if isinstance(full_val, (float, np.floating)) or isinstance(trunc_val, (float, np.floating)):
            if np.isnan(full_val) and np.isnan(trunc_val):
                continue
            assert np.isclose(full_val, trunc_val, equal_nan=True), f"{col} leaks at {check_idx}"
        else:
            assert full_val == trunc_val, f"{col} leaks at {check_idx}"


def test_technical_no_lookahead():
    df = make_ohlcv(n=500)
    assert_no_lookahead(build_technical, df, BASE_CONFIG["features"])


def test_technical_values():
    df = make_ohlcv(n=300, seed=3)
    tech = build_technical(df, BASE_CONFIG["features"])
    assert "ema_20" in tech.columns
    assert "ema_50" in tech.columns
    assert "rsi" in tech.columns
    assert "atr" in tech.columns
    assert tech["atr"].iloc[-1] > 0
    assert (tech["rsi"].between(0, 100)).all()
    # Causal columns must be NaN until warmup (min_periods).
    assert tech["ema_50"].iloc[:49].isna().all()


def test_price_action_no_lookahead():
    df = make_ohlcv(n=300)
    assert_no_lookahead(build_price_action, df)


def test_structure_no_lookahead():
    df = make_ohlcv(n=500)
    assert_no_lookahead(build_structure, df, BASE_CONFIG["features"]["swing_window"])


def test_swings_are_aligned():
    """swing_high_confirmed must only be True at least `window` bars after the
    extreme — i.e. the raw flag is never exposed directly."""
    df = make_ohlcv(n=300)
    swings = swing_fractals(df, window=3)
    raw = np.where(swings["swing_high"].to_numpy())[0]
    confirmed = np.where(swings["swing_high_confirmed"].to_numpy())[0]
    for c in confirmed:
        assert c - 3 in raw


def test_supply_demand_no_lookahead():
    df = make_ohlcv(n=300)
    assert_no_lookahead(build_supply_demand, df, BASE_CONFIG["supply_demand"])


def test_supply_demand_zones_found_on_trending_data():
    """A strong rally must produce demand zones below price."""
    n = 800
    times = pd.date_range("2023-01-01", periods=n, freq="900s", tz="UTC")
    close = 1.0 + np.arange(n) * 0.00005 + np.sin(2 * np.pi * np.arange(n) / 40) * 0.004
    df = pd.DataFrame({
        "time": times,
        "open": close - 0.0001,
        "high": close + 0.0003,
        "low": close - 0.0003,
        "close": close,
        "tick_volume": 100,
        "spread": 0.8,
        "real_volume": np.nan,
    })
    sd = build_supply_demand(df, BASE_CONFIG["supply_demand"])
    assert sd["nearest_demand_dist_atr"].notna().sum() > 0


def test_supply_demand_touch_tolerance_counts_near_misses():
    """Bars that come close to a zone without overlapping it count as touches
    when max_touch_distance_atr allows it, and are ignored at tolerance 0."""
    n = 40
    times = pd.date_range("2023-01-01", periods=n, freq="900s", tz="UTC")
    o = np.full(n, 1.0)
    h = np.full(n, 1.0)
    l = np.full(n, 1.0)
    c = np.full(n, 1.0)
    for i in range(n):
        if i <= 10:
            c[i] = 1.0 - i * 0.0002          # grind down to a low at bar 10
        elif i <= 15:
            c[i] = 0.998 + (i - 10) * 0.0008  # rally away from the low
        elif i <= 24:
            c[i] = 1.002                      # hold high, far above the zone
        else:
            c[i] = 0.9983                     # fade to just above the zone top
        o[i] = c[i - 1] if i else c[i]
        h[i] = max(o[i], c[i]) + 0.0002
        l[i] = min(o[i], c[i]) - 0.0002
    # Zone top at bar 10 ≈ 0.9980; bars 25+ trade at low = 0.9981: above the
    # top (no overlap) but within 0.5 ATR -> near-miss touches.
    df = pd.DataFrame({"time": times, "open": o, "high": h, "low": l, "close": c,
                       "tick_volume": 100, "spread": 0.8, "real_volume": np.nan})
    cfg = dict(BASE_CONFIG["supply_demand"])
    tol_default = build_supply_demand(df, cfg)
    cfg["max_touch_distance_atr"] = 0.0
    tol_zero = build_supply_demand(df, cfg)
    assert tol_default["demand_touches"].sum() > 0
    assert tol_default["demand_touches"].sum() > tol_zero["demand_touches"].sum()


def test_session_features():
    df = make_ohlcv(n=200, start="2023-01-02")
    sess = session_features(df, BASE_CONFIG["sessions"])
    assert set(["hour", "day_of_week", "session_asian", "session_london",
                "session_new_york", "session_overlap"]) <= set(sess.columns)
    london = sess[sess["hour"] == 8]
    assert (london["session_london"] == 1).all()


def test_pipeline_builds_complete_matrix():
    df = make_ohlcv(n=400, start="2023-01-01")
    htf = make_ohlcv(n=100, start="2023-01-01", timeframe="H1")
    features = build_features(df, BASE_CONFIG, {"H1": htf}, base_timeframe="M15")
    assert "rsi" in features.columns
    assert "nearest_demand_dist_atr" in features.columns
    assert "h1_setup_ema_50" in features.columns
    assert features["time"].is_monotonic_increasing


def test_htf_merge_is_causal():
    """HTF context at bar t must not use HTF bars opened after t."""
    base = make_ohlcv(n=600, start="2023-01-01")  # M15
    htf = make_ohlcv(n=160, start="2023-01-01", timeframe="H1")
    features = build_features(base, BASE_CONFIG, {"H1": htf}, base_timeframe="M15")
    col = "h1_setup_ema_50"
    for idx in range(60, len(features), 37):
        t = features["time"].iloc[idx]
        # Recompute HTF ema on the truncated HTF frame and compare the aligned value.
        htf_cut = htf[htf["time"] <= t].copy()
        htf_ema = build_technical(htf_cut, BASE_CONFIG["features"])["ema_50"]
        expected = htf_ema.iloc[-1] if len(htf_ema) else np.nan
        actual = features[col].iloc[idx]
        assert np.isclose(actual, expected, equal_nan=True), f"HTF leakage at {idx}"
