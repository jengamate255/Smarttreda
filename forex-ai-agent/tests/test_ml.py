"""ML layer tests: training, registry, prediction shape, agent gating."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.agent.trading_agent import Decision, TradingAgent, market_regime_from_features
from src.config import Config
from src.ml import model_registry
from src.ml.predict import decide, load_model_set, predict_row
from src.ml.train import feature_columns, prepare_targets
from tests.conftest import BASE_CONFIG, make_ohlcv

import lightgbm


def _trainable_frame(cfg: Config) -> pd.DataFrame:
    df = make_ohlcv(n=1200, seed=7, drift=0.03)
    # Add engineered feature columns so feature_columns finds features.
    tech = __import__("src.features.technical", fromlist=["build_technical"]).build_technical(
        df, cfg.get("features", {})
    )
    labels = __import__("src.labels.trade_outcome", fromlist=["simulate_labels"]).simulate_labels(
        df, cfg=cfg.data, symbol="EURUSD"
    )
    out = pd.concat([df.reset_index(drop=True), tech, labels], axis=1)
    return out


def test_feature_columns_excludes_labels_and_raw_ohlc(cfg):
    frame = _trainable_frame(cfg)
    cols = feature_columns(frame, cfg)
    assert "label_rr3" not in cols
    assert "close" not in cols
    assert "atr" in cols
    assert "rsi" in cols


def test_train_lightgbm_end_to_end(cfg, tmp_path):
    import lightgbm as lgb

    frame = _trainable_frame(cfg)
    targets = prepare_targets(frame, cfg, 3)
    cols = feature_columns(frame, cfg)
    X = frame[cols].reset_index(drop=True)
    y = targets["setup"]
    train_idx = np.arange(0, 800)
    val_idx = np.arange(800, 1000)
    model = lgb.train(
        {"objective": "binary", "metric": "binary_logloss", "verbose": -1,
         "num_leaves": 7, "num_boost_round": 30, "learning_rate": 0.1},
        lgb.Dataset(X.iloc[train_idx], label=y[train_idx]),
        valid_sets=[lgb.Dataset(X.iloc[val_idx], label=y[val_idx])],
        callbacks=[lgb.early_stopping(10)],
    )
    proba = model.predict(X.iloc[val_idx])
    assert proba.shape == (200,)
    assert proba.min() >= 0 and proba.max() <= 1


def test_registry_save_load_roundtrip(cfg, tmp_path):
    import lightgbm as lgb

    frame = _trainable_frame(cfg)
    X = frame[feature_columns(frame, cfg)].reset_index(drop=True)
    model = lgb.train({"objective": "binary", "verbose": -1, "num_boost_round": 5},
                      lgb.Dataset(X.iloc[:100], label=np.zeros(100)))
    version = model_registry.save_model(
        cfg, "EURUSD", "M15", "setup_classifier", model,
        {"algorithm": "lightgbm", "feature_version": "t", "dataset_version": "d",
         "training_period": "a..b", "validation_period": "c..d", "test_period": "e..f",
         "hyperparameters": {}, "feature_columns": feature_columns(frame, cfg)},
        {"test": {"accuracy": 0.5}},
    )
    assert version.startswith("v")
    loaded, loaded_v = model_registry.load_model(cfg, "EURUSD", "M15", "setup_classifier", version)
    assert loaded_v == version
    meta = model_registry.load_metadata(cfg, "EURUSD", "M15", "setup_classifier", version)
    assert meta["algorithm"] == "lightgbm"
    assert meta["symbol"] == "EURUSD"
    assert len(model_registry.list_models(cfg)) >= 1


def test_agent_gates_on_probabilities(cfg):
    agent = TradingAgent(cfg)
    d = agent.evaluate(
        {"setup_probability": 0.9, "buy_probability": 0.75, "sell_probability": 0.1,
         "no_trade_probability": 0.15, "tp_before_sl_probability": 0.72},
        market_regime="TRENDING_UP", session="LONDON", spread_pips=1.0,
    )
    assert d.signal == "BUY"

    d2 = agent.evaluate(
        {"setup_probability": 0.9, "buy_probability": 0.5, "sell_probability": 0.2,
         "no_trade_probability": 0.3, "tp_before_sl_probability": 0.40},
        market_regime="TRENDING_UP", session="LONDON", spread_pips=1.0,
    )
    assert d2.signal == "NO_TRADE"
    assert any("tp_before_sl" in r for r in d2.contradictions)

    d3 = agent.evaluate(
        {"setup_probability": 0.9, "buy_probability": 0.75, "sell_probability": 0.1,
         "no_trade_probability": 0.15, "tp_before_sl_probability": 0.72},
        market_regime="TRENDING_DOWN", session="LONDON", spread_pips=1.0,
    )
    assert d3.signal == "NO_TRADE"  # BUY against regime


def test_regime_from_features(cfg):
    frame = _trainable_frame(cfg)
    frame["structure_state"] = 1
    assert market_regime_from_features(frame, 0) == "TRENDING_UP"


def test_executor_paper_records_journal(cfg, tmp_path):
    cfg.data["paper"] = {"journal_dir": str(tmp_path / "journal")}
    from src.execution.mt5_executor import MT5Executor, OrderRequest

    ex = MT5Executor(cfg, mode="PAPER")
    result = ex.place_order(OrderRequest(
        symbol="EURUSD", side="BUY", lots=0.5, entry=1.1000, sl=1.0990, tp=1.1030,
    ))
    assert result["status"] == "paper_fill_recorded"
    assert (tmp_path / "journal" / "journal.json").exists()


def test_executor_live_blocked_without_env(cfg, tmp_path):
    from src.execution.mt5_executor import ExecutionError, MT5Executor

    ex = MT5Executor(cfg, mode="BACKTEST")
    ex.mode = "LIVE"
    assert ex.mode == "LIVE"
    with pytest.raises(ExecutionError):
        ex._assert_live_authorized(cfg)
