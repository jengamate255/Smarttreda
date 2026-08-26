"""Walk-forward evaluation: expanding-window retraining + out-of-sample backtest.

For each chunk in [walk_forward_start, walk_forward_end):
    train on all bars strictly before chunk start
    predict the chunk with the freshly trained model
    run the backtest engine on the chunk with the ML signals

This is the honest out-of-sample evaluation the project is judged on.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from src.backtest.engine import run_backtest
from src.backtest.metrics import breakdown, compute_metrics
from src.config import Config


def ml_signals_from_predictions(
    frame: pd.DataFrame,
    setup_proba: np.ndarray,
    direction_proba: np.ndarray,
    cfg: Config,
) -> list[dict]:
    """Convert model probabilities into engine signals using ATR-based stops."""
    min_conf = float(cfg.get("agent.min_confidence", 0.65))
    min_tp = float(cfg.get("agent.min_tp_probability", 0.65))
    sl_mult = float(cfg.get("labels.atr_stop_multiple", 1.5))
    tp_mult = sl_mult * float(cfg.get("labels.primary_rr", 3))
    atr = frame["atr"].to_numpy(dtype=float) if "atr" in frame.columns else np.full(len(frame), 1e-9)
    close = frame["close"].to_numpy(dtype=float)
    times = pd.to_datetime(frame["time"], utc=True)
    signals: list[dict] = []
    for i in range(len(frame)):
        setup_p = setup_proba[i, 1] if setup_proba.ndim == 2 and setup_proba.shape[1] > 1 else setup_proba[i]
        if setup_p < min_conf:
            continue
        buy = direction_proba[i, 1] if direction_proba.shape[1] > 2 else direction_proba[i, 0]
        sell = direction_proba[i, 2] if direction_proba.shape[1] > 2 else 0.0
        no = direction_proba[i, 0]
        a = atr[i] if atr[i] > 0 else 1e-9
        if buy >= min_conf and buy >= sell and buy >= no and _tp_prob_ok(frame, i, min_tp):
            signals.append({
                "time": times.iloc[i], "side": "BUY",
                "sl": float(close[i] - sl_mult * a), "tp": float(close[i] + tp_mult * a),
            })
        elif sell >= min_conf and sell >= buy and sell >= no and _tp_prob_ok(frame, i, min_tp):
            signals.append({
                "time": times.iloc[i], "side": "SELL",
                "sl": float(close[i] + sl_mult * a), "tp": float(close[i] - tp_mult * a),
            })
    return signals


def _tp_prob_ok(frame: pd.DataFrame, i: int, min_tp: float) -> bool:
    col = "buy_tp_prob"
    if col not in frame.columns:
        return True
    return float(frame[col].iloc[i]) >= min_tp


def walk_forward_backtest(
    features: pd.DataFrame,
    cfg: Config,
    symbol: str,
    timeframe: str,
    task_config: dict,
    chunk_months: int = 6,
) -> dict:
    from src.ml.train import date_splits, feature_columns, predict_proba, prepare_targets, train_lightgbm

    splits = date_splits(features, cfg)
    wf_start = pd.Timestamp(cfg.get("model.splits.walk_forward_start", "2025-01-01"), tz="UTC")
    wf_end = pd.Timestamp(cfg.get("model.splits.walk_forward_end", "2099-12-31"), tz="UTC")

    times = pd.to_datetime(features["time"], utc=True)
    X_cols = feature_columns(features, cfg)
    targets = prepare_targets(features, cfg, int(cfg.get("labels.primary_rr", 3)))

    cursor = wf_start
    all_trades = []
    all_equity = []
    chunk_results = []
    backend = task_config.get("backend", "lightgbm")

    while cursor < wf_end:
        chunk_end = min(cursor + pd.DateOffset(months=chunk_months), wf_end)
        mask = (times >= cursor) & (times < chunk_end)
        chunk_idx = np.where(mask)[0]
        if len(chunk_idx) == 0:
            cursor = chunk_end
            continue
        train_idx = np.where(times < cursor)[0]
        if len(train_idx) < 2000:
            cursor = chunk_end
            continue

        X = features[X_cols].reset_index(drop=True)
        y_setup = targets["setup"]
        y_dir = targets["direction"]
        y_out = targets["outcome"]
        val_idx = train_idx[- len(train_idx) // 5 :]

        setup_model = train_lightgbm(X, y_setup, train_idx, val_idx, objective="binary")
        dir_model = train_lightgbm(
            X, y_dir, train_idx, val_idx,
            objective="multiclass", num_class=3,
        )

        chunk_features = features.iloc[chunk_idx].reset_index(drop=True)
        X_chunk = X.iloc[chunk_idx]
        setup_proba = predict_proba(setup_model, X_chunk)
        dir_proba = predict_proba(dir_model, X_chunk)

        signals = ml_signals_from_predictions(chunk_features, setup_proba, dir_proba, cfg)
        result = run_backtest(
            chunk_features, lambda f, _s=signals: _s,
            cfg=cfg.get("backtest", {}), symbol=symbol, timeframe=timeframe,
        )
        all_trades.extend(result.trades)
        all_equity.append(result.equity_curve)
        chunk_results.append({
            "chunk_start": str(cursor.date()),
            "chunk_end": str(chunk_end.date()),
            "n_trades": len(result.trades),
            "metrics": compute_metrics(result.trades, result.equity_curve),
        })
        cursor = chunk_end

    equity = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame(
        columns=["time", "equity"]
    )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "backend": backend,
        "chunks": chunk_results,
        "metrics": compute_metrics(all_trades, equity),
        "breakdown": breakdown(all_trades, features),
    }
