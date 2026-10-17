"""Integration test: full pipeline on synthetic data + viz smoke test."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

try:
    import matplotlib
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml_engine.config import DEFAULT_CONFIG
from ml_engine.data.validate import validate
from ml_engine.datasets.build import assert_no_label_leak, build_dataset, feature_columns, xy
from ml_engine.features.build import build_features
from ml_engine.setups.composer import compose_setups
from ml_engine.setups.labels import label_setups
from ml_engine.validation.walkforward import assert_chronology, make_folds


def full_series(n=3000, seed=77):
    """Uptrend with deep pullbacks that retrace into prior demand zones,
    producing confirmed swings, BOS, zones, sweeps, and breakouts."""
    rng = np.random.default_rng(seed)
    level = 1.10
    prices = []
    for i in range(n):
        phase = i % 60
        if phase < 30:
            level += 0.0006  # rally leg
        elif phase < 52:
            level -= 0.0005  # deep pullback (retraces into prior zone)
        else:
            level += 0.00002  # tight base
        prices.append(level)
    close = np.array(prices) + rng.normal(0, 0.00005, n)
    open_ = close + 0.00003 + rng.normal(0, 0.00005, n)
    # impulse at each cycle start
    for i in range(60, n, 60):
        prev = close[i - 1]
        close[i] = prev + 0.0035
        open_[i] = prev + 0.0001
    wick = np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="15min"),
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": rng.integers(100, 3000, n).astype(float),
        }
    )


class TestEndToEnd:
    def test_pipeline_synthetic(self):
        cfg = DEFAULT_CONFIG
        df = full_series(2600)
        rep = validate(df, "SYNTH", "M15")
        assert rep.ok, rep.errors

        features = build_features(df, "SYNTH", "M15", cfg)
        setups = compose_setups(df, "SYNTH", "M15", cfg)
        assert len(setups) >= 5, f"expected setups, got {len(setups)}"
        labels = label_setups(setups, df, cfg)

        dataset, _meta = build_dataset(setups, features, labels, "fv1")
        fcols = feature_columns(dataset, "fv1")
        assert_no_label_leak(fcols)
        assert len(fcols) > 20

        X, _y, m = xy(dataset, fcols, "win_2r")
        assert X.shape[0] == len(setups)

        folds = make_folds(m["time"], cfg.walk_forward)
        if folds:
            assert_chronology(folds)

        # Setups geometry sanity
        for s in setups[:200]:
            if s.direction == "long":
                assert s.stop < s.entry < s.target
            else:
                assert s.target < s.entry < s.stop

    def test_features_match_setup_bar(self):
        cfg = DEFAULT_CONFIG
        df = full_series(1500, seed=78)
        features = build_features(df, "SYNTH", "M15", cfg)
        setups = compose_setups(df, "SYNTH", "M15", cfg)
        if not setups:
            pytest.skip("no setups")
        labels = label_setups(setups, df, cfg)
        dataset, _ = build_dataset(setups, features, labels, "fv1")
        # the setup's own bar value equals feature row at its index
        s = setups[0]
        row = dataset[dataset.setup_id == s.setup_id].iloc[0]
        assert row["rsi14"] == pytest.approx(features.iloc[s.index]["rsi14"])


class TestVizSmoke:
    @pytest.mark.skipif(not _HAS_MATPLOTLIB, reason="matplotlib not installed")
    def test_chart_renders(self, tmp_path):
        from ml_engine.patterns.zones import detect_zone_candidates
        from ml_engine.structure.events import structure_events
        from ml_engine.viz.charts import plot_detections

        cfg = DEFAULT_CONFIG
        df = full_series(800, seed=79)
        events = structure_events(df, cfg.swing)
        zones = detect_zone_candidates(df, "M15", cfg.zone)
        setups = compose_setups(df, "SYNTH", "M15", cfg)
        out = tmp_path / "chart.png"
        plot_detections(
            df, "SYNTH M15 detections", out,
            swings=None, structure_events=events, zones=zones, setups=setups,
            last_n=250,
        )
        assert out.exists() and out.stat().st_size > 10_000
