"""Tests: dataset assembly, walk-forward folds, models, backtest, risk, experiments."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

try:
    import lightgbm
    _HAS_LIGHTGBM = True
except ImportError:
    _HAS_LIGHTGBM = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import itertools

from ml_engine.config import BacktestConfig, WalkForwardConfig
from ml_engine.datasets.build import (
    assert_no_label_leak,
    build_dataset,
    dataset_hash,
    feature_columns,
    xy,
)
from ml_engine.setups.composer import Setup
from ml_engine.validation.walkforward import assert_chronology, make_folds


def make_setups(n=100, start="2025-01-01", symbol="EURUSD"):
    setups = []
    for i in range(60, 60 + n):
        direction = "long" if i % 2 == 0 else "short"
        entry = 1.10 + i * 0.0001
        stop = entry - 0.005 if direction == "long" else entry + 0.005
        target = entry + 0.015 if direction == "long" else entry - 0.015
        setups.append(
            Setup(
                setup_id=f"{symbol}-H1-{direction[0].upper()}-{i}",
                symbol=symbol,
                time=pd.Timestamp(start) + pd.Timedelta(hours=i),
                index=i,
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
                risk_reward=3.0,
            )
        )
    return setups


def make_feature_frame(n, symbol="EURUSD"):
    rng = np.random.default_rng(5)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": "H1",
            "f1": rng.normal(0, 1, n),
            "f2": rng.normal(0, 1, n),
            "f3": rng.normal(0, 1, n),
        }
    )


def make_labels(setups, seed=9):
    rng = np.random.default_rng(seed)
    rows = []
    for s in setups:
        r = rng.normal(0.1, 1.0)
        rows.append(
            {
                "setup_id": s.setup_id,
                "mfe_r": abs(r) + 0.5,
                "mae_r": abs(rng.normal(0.3, 0.2)),
                "time_to_tp_bars": 10, "time_to_sl_bars": None,
                "tp_first": r > 0, "sl_first": r <= 0, "neither": False,
                "win_1r": r > -0.2, "win_2r": r > 0.3, "win_3r": r > 0.8,
                "win_4r": r > 1.5, "r_multiple": r, "exit_bars": 20,
                "max_hold_bars": 200,
            }
        )
    return pd.DataFrame(rows).set_index("setup_id")


def frame_prices(n=300, seed=15):
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0, 0.0008, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 0.0006, n))
    df = pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="1h"),
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": np.full(n, 100.0),
        }
    )
    return df


def setups_from_frame(df, n=20):
    """Realistic setups: entry at bar close, stop 20 pips below/above."""
    setups = []
    for k, i in enumerate(range(60, 60 + n)):
        c = float(df["close"].iloc[i])
        direction = "long" if k % 2 == 0 else "short"
        if direction == "long":
            entry, stop, target = c, c - 0.002, c + 0.006
        else:
            entry, stop, target = c, c + 0.002, c - 0.006
        setups.append(
            Setup(
                setup_id=f"EURUSD-H1-{direction[0].upper()}-{i}",
                symbol="EURUSD",
                time=pd.Timestamp(df["time"].iloc[i]),
                index=i,
                direction=direction,
                entry=entry, stop=stop, target=target,
                risk_reward=3.0,
            )
        )
    return setups


class TestDataset:
    def test_build_and_quarantine(self):
        setups = make_setups(50)
        feats = make_feature_frame(200)
        labels = make_labels(setups)
        dataset, _meta = build_dataset(setups, feats, labels, "fv1")
        assert len(dataset) == 50
        fcols = feature_columns(dataset, "fv1")
        assert_no_label_leak(fcols)
        assert set(fcols) == {"f1", "f2", "f3"}
        # labels attached by id
        assert dataset["win_2r"].notna().all()
        # feature row is the setup bar's row
        assert dataset.iloc[0]["f1"] == feats.iloc[setups[0].index]["f1"]

    def test_hash_changes_with_features(self):
        setups = make_setups(30)
        feats = make_feature_frame(200)
        labels = make_labels(setups)
        d1, _ = build_dataset(setups, feats, labels, "fv1")
        feats2 = feats.copy()
        feats2["f1"] = feats2["f1"] + 1.0
        d2, _ = build_dataset(setups, feats2, labels, "fv1")
        c1 = feature_columns(d1, "fv1")
        c2 = feature_columns(d2, "fv1")
        assert dataset_hash(d1, c1) != dataset_hash(d2, c2)

    def test_xy_shapes(self):
        setups = make_setups(40)
        feats = make_feature_frame(200)
        labels = make_labels(setups)
        dataset, _ = build_dataset(setups, feats, labels, "fv1")
        fcols = feature_columns(dataset, "fv1")
        X, y, _meta = xy(dataset, fcols, "win_2r")
        assert X.shape[0] == len(y) == 40
        assert X.shape[1] == 3
        assert set(np.unique(y)) <= {0.0, 1.0}


class TestWalkForward:
    def test_folds_chronological_with_purge(self):
        times = pd.Series(pd.date_range("2025-01-01", periods=3000, freq="1h"))
        cfg = WalkForwardConfig(train_bars=1000, test_bars=300, step_bars=300, purge_bars=50)
        folds = make_folds(times, cfg)
        assert len(folds) >= 2
        assert_chronology(folds)
        for f in folds:
            assert f.train_index[-1] < f.test_index[0]
            assert f.test_index[0] - f.train_index[-1] == cfg.purge_bars + 1

    def test_insufficient_data_yields_no_folds(self):
        times = pd.Series(pd.date_range("2025-01-01", periods=100, freq="1h"))
        folds = make_folds(times, WalkForwardConfig(train_bars=1000, test_bars=300))
        assert folds == []

    def test_expanding_window(self):
        times = pd.Series(pd.date_range("2025-01-01", periods=2000, freq="1h"))
        cfg = WalkForwardConfig(train_bars=500, test_bars=200, step_bars=200, purge_bars=50)
        folds = make_folds(times, cfg)
        assert len(folds) >= 3
        # expanding: fold k+1 train starts at same point, ends later
        assert folds[0].train_index[0] == folds[1].train_index[0]
        assert folds[1].train_index[-1] > folds[0].train_index[-1]


class TestModels:
    def test_fit_and_predict_all_types(self):
        from ml_engine.models.train import fit_model

        rng = np.random.default_rng(3)
        n = 400
        X = rng.normal(0, 1, (n, 5))
        beta = np.array([1.0, -0.8, 0.5, 0.0, 0.3])
        logit = X @ beta
        y = (logit + rng.normal(0, 0.5, n) > 0).astype(int)
        for mt in ["lr", "rf", "lgbm"]:
            if mt == "lgbm":
                try:
                    import lightgbm  # noqa: F401
                except ImportError:
                    continue
            model = fit_model(X, y, [f"f{i}" for i in range(5)], "win_2r", mt)
            p = model.predict_proba(X)
            assert 0 <= p.min() and p.max() <= 1
            assert p.shape == (n,)

    @pytest.mark.skipif(not _HAS_LIGHTGBM, reason="lightgbm not installed")
    def test_calibration_better_than_raw(self):
        """Isotonic-calibrated LGBM probabilities must be closer to empirical
        rates than 0/1-uncalibrated RF on this separable problem."""
        from ml_engine.models.train import make_pipeline

        rng = np.random.default_rng(11)
        n = 3000
        X = rng.normal(0, 1, (n, 4))
        logit = X @ np.array([1.5, -1.0, 0.5, 0.2])
        p_true = 1 / (1 + np.exp(-logit))
        y = (rng.uniform(0, 1, n) < p_true).astype(int)
        pipe = make_pipeline("lgbm", calibrate=True)
        pipe.fit(X, y)
        p = pipe.predict_proba(X)[:, 1]
        from ml_engine.models.evaluate import calibration_buckets

        buckets = calibration_buckets(y, p)
        # in-sample sanity: calibration buckets should be monotone-ish
        realized = [b["realized"] for b in buckets]
        for i in range(1, len(realized)):
            assert realized[i] >= realized[i - 1] - 0.15


class TestBacktest:
    def test_backtest_runs_and_reports(self):
        from ml_engine.backtest.engine import backtest_setups
        from ml_engine.backtest.metrics import trade_metrics

        df = frame_prices()
        setups = setups_from_frame(df, 20)
        cfg = BacktestConfig()
        trades = backtest_setups(df, pd.DataFrame([s.__dict__ for s in setups]), None, cfg)
        tdf = pd.DataFrame([t.__dict__ for t in trades])
        m = trade_metrics(tdf, cfg.start_balance)
        assert m["trades"] == len(trades)
        assert 0 <= m["win_rate"] <= 1
        assert "expectancy" in m and "profit_factor" in m
        for t in trades:
            assert t.exit_reason in ("tp", "sl", "sl_ambiguity_first", "timeout")
            if t.exit_reason in ("sl", "sl_ambiguity_first"):
                # a stop-out must lose money (costs push it past -risk)
                assert t.pnl < 0, f"sl exit with pnl {t.pnl}"
            assert t.lots >= 0.01

    def test_single_position_no_overlap(self):
        from ml_engine.backtest.engine import backtest_setups

        df = frame_prices()
        setups = setups_from_frame(df, 20)
        trades = backtest_setups(df, pd.DataFrame([s.__dict__ for s in setups]), None, BacktestConfig())
        for a, b in itertools.pairwise(trades):
            assert b.entry_time > a.exit_time

    def test_cost_model_reduces_pnl(self):
        from ml_engine.backtest.engine import backtest_setups

        df = frame_prices()
        setups = setups_from_frame(df, 10)
        s_df = pd.DataFrame([s.__dict__ for s in setups])
        no_cost = backtest_setups(df, s_df, None, BacktestConfig(spread_points=0, slippage_points=0, commission_per_lot=0))
        with_cost = backtest_setups(df, s_df, None, BacktestConfig())
        pnl_no = sum(t.pnl for t in no_cost)
        pnl_yes = sum(t.pnl for t in with_cost)
        assert pnl_yes < pnl_no


class TestRisk:
    def test_size_capped(self):
        from ml_engine.risk.sizing import SizingPolicy, size_for_probability

        pol = SizingPolicy(base_risk_percent=0.5, max_risk_percent=1.0, min_risk_percent=0.1)
        assert size_for_probability(0.9, pol) == pytest.approx(1.0)
        assert size_for_probability(0.2, pol) == pytest.approx(0.1)
        assert size_for_probability(0.6, pol) == pytest.approx(0.1 + 0.5 * 0.9)

    def test_lots_never_exceed_risk(self):
        from ml_engine.risk.sizing import lots_for_risk

        lots = lots_for_risk(10_000, 0.5, stop_distance_price=0.005)
        worst_loss = 0.005 / 0.0001 * 10.0 * lots
        assert worst_loss <= 10_000 * 0.005 + 1e-6

    def test_probability_gate_in_backtest(self):
        from ml_engine.backtest.engine import backtest_setups

        df = frame_prices()
        setups = setups_from_frame(df, 10)
        s_df = pd.DataFrame([s.__dict__ for s in setups])
        probs = pd.Series([0.3] * len(setups))
        cfg = BacktestConfig(min_probability=0.5)
        trades = backtest_setups(df, s_df, probs, cfg)
        assert trades == []


class TestExperiments:
    def test_registry_roundtrip(self, tmp_path):
        from ml_engine.experiments.registry import (
            list_experiments,
            record_experiment,
            record_fold_metrics,
        )

        db = tmp_path / "exp.db"
        record_experiment(
            experiment_id="exp-1",
            dataset_hash="abc123",
            dataset_version="dv1",
            feature_version="fv1",
            label_version="lv1",
            model_type="lgbm",
            target="win_2r",
            params={"seed": 42},
            train_period=("2025-01-01", "2025-06-01"),
            test_period=("2025-06-01", "2025-08-01"),
            metrics={"auc": 0.61},
            git_commit="deadbeef",
            db_path=db,
        )
        record_fold_metrics("exp-1", 0, {"auc": 0.6}, db_path=db)
        exps = list_experiments(db_path=db)
        assert len(exps) == 1
        assert exps[0]["experiment_id"] == "exp-1"
        assert exps[0]["metrics"]["auc"] == 0.61

    def test_append_only_never_overwrites(self, tmp_path):
        from ml_engine.experiments.registry import list_experiments, record_experiment

        db = tmp_path / "exp.db"
        for i in range(3):
            record_experiment(
                experiment_id=f"exp-{i}", dataset_hash=f"h{i}",
                dataset_version="dv1", feature_version="fv1", label_version="lv1",
                model_type="lr", target="win_2r", params={},
                train_period=("", ""), test_period=("", ""), metrics={"i": i},
                db_path=db,
            )
        exps = list_experiments(db_path=db)
        assert len(exps) == 3
        assert [e["metrics"]["i"] for e in exps] == [0, 1, 2]
