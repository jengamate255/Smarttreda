"""Anti-leakage acceptance tests.

A model trained with ANY of these violations is considered INVALID:
  - future candle leakage in features
  - future indicator leakage
  - incorrect multi-timeframe alignment
  - random train/test contamination
  - look-ahead bias in labels
  - duplicate candles
  - timestamp errors / non-monotonic time
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import Config
from src.features.pipeline import build_features
from src.features.technical import build_technical
from src.labels.trade_outcome import simulate_labels
from src.ml.train import date_splits, prepare_targets
from tests.conftest import BASE_CONFIG, make_ohlcv


def test_no_future_candle_leakage_in_features():
    """Truncation invariance: feature values at t computed with only data <= t."""
    df = make_ohlcv(n=300, seed=42)
    htf = make_ohlcv(n=80, seed=43, timeframe="H1")
    full = build_features(df, BASE_CONFIG, {"H1": htf}, base_timeframe="M15")
    cut_at = 150
    cut = build_features(
        df.iloc[:cut_at], BASE_CONFIG,
        {"H1": htf[htf["time"] <= df["time"].iloc[cut_at - 1]]},
        base_timeframe="M15",
    )
    for col in full.columns:
        if col == "time" or col.startswith("label"):
            continue
        a = full[col].iloc[cut_at - 1]
        b = cut[col].iloc[cut_at - 1]
        if isinstance(a, (float, np.floating)) and isinstance(b, (float, np.floating)):
            assert np.isclose(a, b, equal_nan=True), f"future leakage in {col}"
        else:
            assert a == b, f"future leakage in {col}"


def test_no_future_indicator_leakage():
    df = make_ohlcv(n=200, seed=1)
    tech = build_technical(df, BASE_CONFIG["features"])
    for col in tech.columns:
        for i in range(20, len(df)):
            val_full = tech[col].iloc[i]
            val_trunc = build_technical(df.iloc[: i + 1], BASE_CONFIG["features"])[col].iloc[-1]
            assert np.isclose(val_full, val_trunc, equal_nan=True), f"{col} leaks at {i}"
            break


def test_mtf_alignment_no_lookahead():
    """H4 context value at a base bar must be the H4 bar whose open <= base time."""
    base = make_ohlcv(n=400, start="2023-01-01")
    htf = make_ohlcv(n=100, start="2023-01-01", timeframe="H4")
    features = build_features(base, BASE_CONFIG, {"H4": htf}, base_timeframe="M15")
    col = "h4_regime_ema_50"
    for idx in [100, 200, 300]:
        t = features["time"].iloc[idx]
        htf_cut = htf[htf["time"] <= t]
        expected = build_technical(htf_cut, BASE_CONFIG["features"])["ema_50"].iloc[-1]
        assert np.isclose(features[col].iloc[idx], expected, equal_nan=True)


def test_splits_are_chronological_and_disjoint(cfg):
    df = make_ohlcv(n=3000, start="2019-01-01", timeframe="M15")
    local = Config(data={**dict(BASE_CONFIG), "model": {"splits": {
        "train_start": "2019-01-01", "train_end": "2019-01-11",
        "validation_start": "2019-01-11", "validation_end": "2019-01-16",
        "test_start": "2019-01-16", "test_end": "2019-01-21",
        "walk_forward_start": "2019-01-21", "walk_forward_end": "2099-12-31",
    }}})
    splits = date_splits(df, local)
    assert len(splits["train"]) > 0
    assert len(splits["validation"]) > 0
    assert len(splits["test"]) > 0
    assert len(splits["walk_forward"]) > 0
    t = pd.to_datetime(df["time"], utc=True)
    train_max = t.iloc[splits["train"]].max()
    val_min = t.iloc[splits["validation"]].min()
    assert train_max < val_min
    assert set(splits["train"]) & set(splits["validation"]) == set()
    assert set(splits["train"]) & set(splits["test"]) == set()
    assert set(splits["validation"]) & set(splits["test"]) == set()


def test_random_shuffle_would_contaminate_detector(cfg):
    """The project rejects random splits; verify split dates can't overlap."""
    df = make_ohlcv(n=2000, start="2019-01-01", timeframe="M15")
    local = Config(data={**dict(BASE_CONFIG), "model": {"splits": {
        "train_start": "2019-01-01", "train_end": "2019-01-11",
        "validation_start": "2019-01-11", "validation_end": "2019-01-16",
        "test_start": "2019-01-16", "test_end": "2019-01-21",
        "walk_forward_start": "2019-01-21", "walk_forward_end": "2099-12-31",
    }}})
    splits = date_splits(df, local)
    # Same row must never appear in two splits.
    all_idx = np.concatenate([splits[k] for k in ("train", "validation", "test")])
    assert len(np.unique(all_idx)) == len(all_idx)


def test_duplicate_candles_detected(cfg):
    df = make_ohlcv(n=100)
    dup = pd.concat([df, df.iloc[[3]]], ignore_index=True).sort_values("time")
    report = __import__("src.data.storage", fromlist=["integrity_report"]).integrity_report(
        dup, ["time", "open", "high", "low", "close"]
    )
    assert report["duplicate_timestamps"] == 1
    assert report["fatal"]


def test_timestamp_errors_detected(cfg):
    df = make_ohlcv(n=100)
    df = df.sort_values("time").reset_index(drop=True)
    df.loc[10, "time"] = df.loc[9, "time"] - pd.Timedelta(hours=1)
    report = __import__("src.data.storage", fromlist=["integrity_report"]).integrity_report(
        df, ["time", "open", "high", "low", "close"]
    )
    assert report["non_monotonic"] >= 1
    assert report["fatal"]


def test_labels_do_not_leak_future(cfg):
    """Interior-bar labels are identical between full and truncated frames;
    bars within `max_lookahead_bars` of the cut see a shorter window by design."""
    df = make_ohlcv(n=150, seed=21)
    cfg2 = {"labels": {"stop_mode": "fixed_pips", "fixed_stop_pips": 30.0,
                       "rr_ratios": [3], "max_lookahead_bars": 30,
                       "spread_pips": 0.8, "commission_pips": 0.2}}
    full = simulate_labels(df, cfg2, symbol="EURUSD")
    cut = simulate_labels(df.iloc[:100], cfg2, symbol="EURUSD")
    interior = 100 - 30
    assert (full["label_rr3"].iloc[:interior] == cut["label_rr3"].iloc[:interior]).all()
    assert (full["buy_tp_first_rr3"].iloc[:interior] == cut["buy_tp_first_rr3"].iloc[:interior]).all()


def test_targets_derive_causally_from_labels(cfg):
    df = make_ohlcv(n=200, seed=5)
    cfg2 = {"labels": {"stop_mode": "fixed_pips", "fixed_stop_pips": 20.0,
                       "rr_ratios": [3], "max_lookahead_bars": 30,
                       "spread_pips": 0.8, "commission_pips": 0.2}}
    labels = simulate_labels(df, cfg2, symbol="EURUSD")
    t = prepare_targets(labels, cfg, 3)
    assert set(t) == {"setup", "direction", "outcome"}
    assert set(t["direction"].tolist()) <= {0, 1, 2}
