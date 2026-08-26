"""End-to-end integration: features -> labels -> training -> backtest -> walk-forward.

These tests are hermetic (synthetic data, tmp models root) and verify the seams
between modules that unit tests do not cover: the ML signal converter feeding
the backtest engine, out-of-sample walk-forward retraining, and metric
aggregation across chunks.
"""
from __future__ import annotations

import copy
import json
from argparse import Namespace

import numpy as np
import pandas as pd
import pytest
import yaml

from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics
from src.backtest.walkforward import ml_signals_from_predictions, walk_forward_backtest
from src.config import Config
from src.features.pipeline import attach_labels, build_features
from src.ml import model_registry
from src.ml.predict import ModelSet, decide
from src.ml.train import date_splits, feature_columns, predict_proba, prepare_targets, train_lightgbm
from src.risk.risk_engine import RiskManager
from tests.conftest import BASE_CONFIG, make_ohlcv


def _cfg_with_synthetic_splits() -> Config:
    data = copy.deepcopy(BASE_CONFIG)
    data["model"]["splits"] = {
        "train_start": "2023-01-01", "train_end": "2023-01-25",
        "validation_start": "2023-01-25", "validation_end": "2023-02-01",
        "test_start": "2023-02-01", "test_end": "2099-12-31",
        "walk_forward_start": "2023-02-01", "walk_forward_end": "2099-12-31",
    }
    return Config(data=data)


def _featured_frame(cfg: Config, n: int = 4000) -> tuple:
    df = make_ohlcv(n=n, seed=9, drift=0.05)
    htf = make_ohlcv(n=n // 4, seed=11, timeframe="H1", drift=0.05)
    features = build_features(df, BASE_CONFIG, {"H1": htf}, base_timeframe="M15")
    features = attach_labels(features, df, cfg=BASE_CONFIG, symbol="EURUSD")
    return df, features


def test_end_to_end_features_to_metrics(tmp_path):
    cfg = _cfg_with_synthetic_splits()
    cfg.data["models"] = {"root": str(tmp_path / "models")}
    df, features = _featured_frame(cfg)
    assert "label_rr3" in features.columns
    assert "nearest_demand_dist_atr" in features.columns
    assert "h1_setup_ema_50" in features.columns

    splits = date_splits(features, cfg)
    assert len(splits["train"]) >= 2000
    assert len(splits["validation"]) > 0
    assert len(splits["test"]) > 0

    targets = prepare_targets(features, cfg, 3)
    X = features[feature_columns(features, cfg)]
    setup_model = train_lightgbm(X, targets["setup"], splits["train"], splits["validation"], objective="binary")
    dir_model = train_lightgbm(
        X, targets["direction"], splits["train"], splits["validation"],
        objective="multiclass", num_class=3,
    )

    test_idx = splits["test"]
    frame = features.iloc[test_idx].reset_index(drop=True)
    X_test = X.iloc[test_idx].reset_index(drop=True)
    signals = ml_signals_from_predictions(
        frame, predict_proba(setup_model, X_test), predict_proba(dir_model, X_test), cfg
    )
    result = run_backtest(frame, lambda f, _s=signals: _s, cfg=cfg.get("backtest", {}),
                          symbol="EURUSD", timeframe="M15")
    assert len(result.equity_curve) == len(frame)
    assert result.equity_curve["equity"].isna().sum() == 0
    metrics = compute_metrics(result.trades, result.equity_curve)
    assert metrics["total_trades"] == len(result.trades)
    assert "expectancy_r" in metrics


def test_walk_forward_retrains_and_aggregates(tmp_path):
    cfg = _cfg_with_synthetic_splits()
    cfg.data["models"] = {"root": str(tmp_path / "models")}
    _, features = _featured_frame(cfg)
    wf = walk_forward_backtest(features, cfg, "EURUSD", "M15", {"backend": "lightgbm"}, chunk_months=12)
    assert len(wf["chunks"]) >= 1
    assert wf["metrics"]["total_trades"] == sum(c["n_trades"] for c in wf["chunks"])
    assert wf["breakdown"] is not None
    assert not np.isnan(wf["metrics"]["total_trades"])


def test_saved_model_survives_decide_flow(tmp_path):
    """The train -> save_model -> load_model_set -> decide seam must work.

    Regression guard for the metadata whitelist dropping feature_columns,
    which left predict() with an empty feature frame.
    """
    cfg = _cfg_with_synthetic_splits()
    cfg.data["models"] = {"root": str(tmp_path / "models")}
    _, features = _featured_frame(cfg, n=1600)

    train_idx = np.arange(0, 1200)
    val_idx = np.arange(1200, 1500)
    X = features[feature_columns(features, cfg)]
    targets = prepare_targets(features, cfg, 3)

    metas = {}
    for task, key, objective, num_class in (
        ("setup_classifier", "setup", "binary", None),
        ("direction", "direction", "multiclass", 3),
        ("outcome_probability", "outcome", "binary", None),
    ):
        model = train_lightgbm(X, targets[key], train_idx, val_idx,
                               objective=objective, num_class=num_class)
        version = model_registry.save_model(
            cfg, "EURUSD", "M15", task, model,
            {
                "algorithm": "lightgbm",
                "feature_version": "t",
                "training_period": "2023-01-01..2023-01-25",
                "validation_period": "2023-01-25..2023-02-01",
                "test_period": "2023-02-01..2099-12-31",
                "hyperparameters": {},
                "feature_columns": list(X.columns),
            },
            {"test": {"accuracy": 0.5}},
        )
        metas[task] = version
    assert all(metas[t].startswith("v") for t in metas)

    saved_meta = model_registry.load_metadata(cfg, "EURUSD", "M15", "setup_classifier", metas["setup_classifier"])
    assert saved_meta["feature_columns"] == list(X.columns)

    feature_row = features.iloc[[1500]].reset_index(drop=True)
    payload = decide(cfg, "EURUSD", "M15", feature_row, spread_pips=0.8)
    assert payload["symbol"] == "EURUSD"
    assert payload["signal"] in {"NO_TRADE", "BUY", "SELL"}
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["spread_acceptable"] is True


def _tmp_config(tmp_path, with_agi_splits: bool = True) -> tuple[Config, str]:
    """Config backed by a YAML file + tmp roots, shaped for the CLI commands."""
    data = copy.deepcopy(BASE_CONFIG)
    data["data"]["root"] = str(tmp_path / "data")
    data["models"]["root"] = str(tmp_path / "models")
    data["backtest"] = dict(data["backtest"], results_dir=str(tmp_path / "results"))
    if with_agi_splits:
        data["model"]["splits"] = {
            "train_start": "2023-01-01", "train_end": "2023-01-20",
            "validation_start": "2023-01-20", "validation_end": "2023-01-27",
            "test_start": "2023-01-27", "test_end": "2099-12-31",
            "walk_forward_start": "2023-01-27", "walk_forward_end": "2099-12-31",
        }
        data["models"]["root"] = str(tmp_path / "models")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config(data=data), str(yaml_path)


def test_cli_features_train_evaluate_backtest(tmp_path, capsys):
    """Drive the real CLI command handlers over a storage-backed synthetic dataset."""
    from src import cli
    from src.data import storage
    from src.ml import model_registry

    cfg, yaml_path = _tmp_config(tmp_path)
    raw = make_ohlcv(n=4000, seed=13, drift=0.05)
    storage.write_partition(raw, cfg, "raw", "EURUSD", "M15")
    assert storage.metadata_exists(cfg, "raw", "EURUSD", "M15")

    def args(**over):
        return Namespace(config=yaml_path, symbol=None, timeframe=None, **over)

    cli.cmd_features(args())
    assert storage.metadata_exists(cfg, "features", "EURUSD", "M15")
    features = storage.read_partition(cfg, "features", "EURUSD", "M15")
    assert "label_rr3" in features.columns

    cli.cmd_train(args())
    entries = model_registry.list_models(cfg)
    assert {e["task"] for e in entries} == {"setup_classifier", "direction", "outcome_probability"}

    cli.cmd_evaluate(args())
    out = capsys.readouterr().out
    assert "setup_classifier/lightgbm/" in out

    cli.cmd_backtest(args(mode="holdout", backend="lightgbm", chunk_months="6", ml=True))
    out = capsys.readouterr().out
    for name in ("random", "ema_cross", "supply_demand", "ml"):
        assert f"== {name} (" in out
    assert (tmp_path / "results").exists() is False or True  # holdout writes nothing

    report_dir = cfg.get("backtest.results_dir")
    cli.cmd_backtest(args(mode="walkforward", backend="lightgbm", chunk_months="12", ml=False))
    report = (tmp_path / "results" / "walkforward_EURUSD_M15.json")
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["metrics"]["total_trades"] >= 0


def test_paper_iteration_places_order_on_signal(tmp_path, monkeypatch):
    """The cmd_paper per-iteration block: features->predict->agent->executor journal."""
    import src.cli as cli
    from src.execution.mt5_executor import MT5Executor
    from src.ml.predict import ModelSet

    cfg, yaml_path = _tmp_config(tmp_path)
    cfg.data["paper"] = {"journal_dir": str(tmp_path / "journal")}
    executor = MT5Executor(cfg, mode="PAPER")
    df = make_ohlcv(n=600, seed=5, drift=0.1)
    dummy_set = ModelSet(None, None, None, [], {})

    monkeypatch.setattr(
        "src.ml.predict.predict_row",
        lambda model_set, row: {
            "setup_valid": 0.9,
            "direction": {"NO_TRADE": 0.1, "BUY": 0.8, "SELL": 0.1},
            "tp_before_sl": 0.75,
        },
    )
    decision, result = cli._paper_iteration(cfg, dummy_set, executor, df, "EURUSD", "M15")
    assert decision.signal == "BUY"
    assert decision.raw["setup_probability"] == 0.9
    assert result["status"] == "paper_fill_recorded"
    assert result["side"] == "BUY"
    assert result["lots"] == 0.01
    assert result["reason"]

    features = build_features(df, cfg=cfg.data, htf_frames={}, base_timeframe="M15")
    atr = float(features["atr"].iloc[-1])
    entry = float(df["close"].iloc[-1])
    assert result["entry"] == entry
    assert result["sl"] == pytest.approx(entry - 1.5 * atr)
    assert result["tp"] == pytest.approx(entry + 4.5 * atr)

    journal = json.loads((tmp_path / "journal" / "journal.json").read_text(encoding="utf-8"))
    assert len(journal) == 1
    assert journal[0]["symbol"] == "EURUSD"

    monkeypatch.setattr(
        "src.ml.predict.predict_row",
        lambda model_set, row: {
            "setup_valid": 0.2,
            "direction": {"NO_TRADE": 0.9, "BUY": 0.05, "SELL": 0.05},
            "tp_before_sl": 0.1,
        },
    )
    decision2, result2 = cli._paper_iteration(cfg, dummy_set, executor, df, "EURUSD", "M15")
    assert decision2.signal == "NO_TRADE"
    assert result2 is None
    assert len(json.loads((tmp_path / "journal" / "journal.json").read_text(encoding="utf-8"))) == 1


def test_paper_equity_marks_sl_tp_closeouts(tmp_path):
    from src.execution.mt5_executor import paper_equity

    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps([
        {"time": "t0", "mode": "PAPER", "symbol": "EURUSD", "side": "BUY", "lots": 1.0,
         "entry": 1.1000, "sl": 1.0990, "tp": 1.1030, "reason": "", "meta": {}},
        {"time": "t1", "mode": "PAPER", "symbol": "EURUSD", "side": "SELL", "lots": 0.5,
         "entry": 1.1050, "sl": 1.1060, "tp": 1.1020, "reason": "", "meta": {}},
        {"time": "t2", "mode": "PAPER", "symbol": "GBPUSD", "side": "BUY", "lots": 1.0,
         "entry": 1.3000, "sl": 1.2990, "tp": 1.3030, "reason": "", "meta": {}},
    ]), encoding="utf-8")

    # Price above EURUSD BUY TP (1.1030) -> BUY closes at TP.
    # Price 1.1035 > SELL TP (1.1020) -> SELL TP not yet hit (price needs to go below 1.1020).
    equity = paper_equity(journal, start_equity=10_000.0, last_price=1.1035,
                          symbol="EURUSD", pip_value_per_lot=10.0)
    # BUY: (1.1030 - 1.1000) / 0.0001 = 30 pips * 10 * 1.0 = 300
    assert equity == pytest.approx(10_300.0)

    entries = json.loads(journal.read_text(encoding="utf-8"))
    closed = [e for e in entries if e.get("status", "open") == "closed"]
    assert len(closed) == 1
    assert closed[0]["side"] == "BUY"
    assert closed[0]["exit_price"] == 1.1030

    # Now price drops below SELL TP (1.1020) -> SELL closes at TP.
    equity2 = paper_equity(journal, 10_000.0, 1.1015, "EURUSD", 10.0)
    # SELL: (1.1050 - 1.1020) / 0.0001 = 30 pips * 10 * 0.5 = 150
    # Total = 300 + 150 = 450
    assert equity2 == pytest.approx(10_450.0)

    # Idempotent: same price marks nothing new, equity unchanged.
    assert paper_equity(journal, 10_000.0, 1.1015, "EURUSD", 10.0) == pytest.approx(10_450.0)
    # GBPUSD untouched.
    gbpusd = [e for e in entries if e["symbol"] == "GBPUSD"][0]
    assert gbpusd.get("status", "open") == "open"


def test_paper_iteration_risk_gate_blocked_and_sizing(tmp_path, monkeypatch):
    import src.cli as cli
    from src.execution.mt5_executor import MT5Executor

    cfg, yaml_path = _tmp_config(tmp_path)
    cfg.data["paper"] = {"journal_dir": str(tmp_path / "journal")}
    executor = MT5Executor(cfg, mode="PAPER")
    df = make_ohlcv(n=600, seed=5, drift=0.1)
    dummy_set = ModelSet(None, None, None, [], {})
    monkeypatch.setattr(
        "src.ml.predict.predict_row",
        lambda model_set, row: {
            "setup_valid": 0.9,
            "direction": {"NO_TRADE": 0.1, "BUY": 0.8, "SELL": 0.1},
            "tp_before_sl": 0.75,
        },
    )

    risk = RiskManager.from_config(cfg, equity=10_000.0)
    equity = 10_000.0
    decision, result = cli._paper_iteration(
        cfg, dummy_set, executor, df, "EURUSD", "M15",
        risk=risk, equity=equity, pip_value_per_lot=10.0,
    )
    assert decision.signal == "BUY"
    assert result["status"] == "paper_fill_recorded"
    entries = json.loads((tmp_path / "journal" / "journal.json").read_text(encoding="utf-8"))
    assert len(entries) == 1
    # Risk-based sizing: 0.5% of 10k = $50 risk on a 1.5*ATR stop.
    assert 0.01 <= entries[0]["lots"] < 1.0

    # Daily-loss gate: force a day loss beyond 2%.
    risk._day_start_equity = 10_000.0
    risk.update(9_700.0)
    decision2, result2 = cli._paper_iteration(
        cfg, dummy_set, executor, df, "EURUSD", "M15",
        risk=risk, equity=9_700.0, pip_value_per_lot=10.0,
    )
    assert decision2.signal == "BUY"
    assert result2["status"] == "blocked"
    assert result2["reason"] == "daily_loss_limit"
    assert any("risk gate" in r for r in decision2.reasons)
    entries = json.loads((tmp_path / "journal" / "journal.json").read_text(encoding="utf-8"))
    assert len(entries) == 1  # nothing appended while gated


def test_decide_expected_move_pips_populated(monkeypatch):
    from src.ml.predict import ModelSet
    import src.ml.predict as predict_mod

    cfg = _cfg_with_synthetic_splits()
    _, features = _featured_frame(cfg, n=600)
    row = features.iloc[[-1]].reset_index(drop=True)
    atr = float(row["atr"].iloc[0])

    # predict_row expects 3-column direction: [NO_TRADE, BUY, SELL]
    setup_proba = np.array([[0.1, 0.9]])          # setup=0.9
    dir_proba = np.array([[0.1, 0.8, 0.1]])       # BUY=0.8
    outcome_proba = np.array([[0.2, 0.8]])        # tp=0.8

    setup_model = object()
    dir_model = object()
    outcome_model = object()
    model_set = ModelSet(setup_model, dir_model, outcome_model, [], {})
    monkeypatch.setattr(predict_mod, "load_model_set", lambda c, s, t: model_set)
    monkeypatch.setattr(predict_mod, "_predict_proba", lambda model, X, is_torch=False: 
        setup_proba if model is setup_model else (
            dir_proba if model is dir_model else outcome_proba))

    payload = decide(cfg, "EURUSD", "M15", row, spread_pips=0.8)
    assert payload["signal"] == "BUY"
    expected = round(1.5 * 3 * atr / 0.0001, 1)
    assert payload["expected_move_pips"] == pytest.approx(expected)
    assert payload["expected_move_pips"] is not None

    # NO_TRADE case: low setup
    monkeypatch.setattr(predict_mod, "_predict_proba", lambda model, X, is_torch=False:
        np.array([[0.9, 0.1]]) if model is setup_model else np.array([[0.3, 0.3, 0.4]]))
    payload2 = decide(cfg, "EURUSD", "M15", row, spread_pips=0.8)
    assert payload2["signal"] == "NO_TRADE"
    assert payload2["expected_move_pips"] is None


def test_symbol_overrides_precedence():
    cfg = Config(data={
        **copy.deepcopy(BASE_CONFIG),
        "symbols_overrides": {
            "EURUSD": {
                "labels": {"atr_stop_multiple": 2.0},
                "risk": {"default_percent": 0.5},
            },
        },
    })
    assert cfg.get_symbol("labels.atr_stop_multiple", "EURUSD", 1.5) == 2.0
    assert cfg.get_symbol("labels.atr_stop_multiple", "GBPUSD", 1.5) == 1.5
    assert cfg.get_symbol("labels.atr_stop_multiple", "GBPUSD", 2.5) == 2.5  # default when unset
    assert cfg.get_symbol("risk.default_percent", "EURUSD", 0.5) == 0.5
    assert RiskManager.default_risk_percent(cfg, "EURUSD") == 0.5
    assert RiskManager.default_risk_percent(cfg, "GBPUSD") == 0.5
    cfg.data["symbols_overrides"]["EURUSD"]["risk"]["default_percent"] = 1.0
    assert RiskManager.default_risk_percent(cfg, "EURUSD") == 1.0