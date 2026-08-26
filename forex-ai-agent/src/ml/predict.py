"""Inference: turn a feature frame into the agent-facing decision payload."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.config import Config
from src.ml import model_registry
from src.ml.train import feature_columns, predict_proba

DIRECTION_NAMES = {0: "NO_TRADE", 1: "BUY", 2: "SELL"}


@dataclass
class ModelSet:
    setup: Any
    direction: Any
    outcome: Any
    feature_cols: list[str]
    versions: dict[str, str]


def _predict_proba(model, X: pd.DataFrame, is_torch: bool = False) -> np.ndarray:
    if is_torch:
        import torch
        Xt = torch.tensor(X.to_numpy(dtype=np.float32), device=next(model.parameters()).device)
        return model.predict_proba(Xt)
    return predict_proba(model, X)


def load_model_set(cfg: Config, symbol: str, timeframe: str) -> ModelSet:
    def load(task: str):
        model, version = model_registry.load_model(cfg, symbol, timeframe, task)
        meta = model_registry.load_metadata(cfg, symbol, timeframe, task, version)
        # torch is an optional dependency; treat non-torch models as plain sklearn-like.
        try:
            from src.ml.torch_models import MLPClassifier
            is_torch = isinstance(model, MLPClassifier)
        except ModuleNotFoundError:
            is_torch = False
        return model, version, is_torch

    setup, v_s, torch_s = load("setup_classifier")
    direction, v_d, torch_d = load("direction")
    outcome, v_o, torch_o = load("outcome_probability")

    meta = model_registry.load_metadata(cfg, symbol, timeframe, "direction", v_d)
    feature_cols = feature_columns_from_meta(meta, cfg)
    return ModelSet(setup, direction, outcome, feature_cols, {"setup": v_s, "direction": v_d, "outcome": v_o})


def feature_columns_from_meta(meta: dict, cfg: Config) -> list[str]:
    cols = meta.get("feature_columns")
    if cols:
        return cols
    return []


def predict_row(
    model_set: ModelSet,
    X: pd.DataFrame,
) -> dict[str, Any]:
    missing = [c for c in model_set.feature_cols if c not in X.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing[:10]} ...")
    Xm = X[model_set.feature_cols]
    setup_proba = _predict_proba(model_set.setup, Xm)
    direction_proba = _predict_proba(model_set.direction, Xm)
    outcome_proba = _predict_proba(model_set.outcome, Xm)

    return {
        "setup_valid": float(setup_proba[0, 1]) if setup_proba.shape[1] > 1 else float(setup_proba[0, 0]),
        "direction": {
            "NO_TRADE": float(direction_proba[0, 0]),
            "BUY": float(direction_proba[0, 1]) if direction_proba.shape[1] > 2 else float(direction_proba[0, 0]),
            "SELL": float(direction_proba[0, 2]) if direction_proba.shape[1] > 2 else 0.0,
        },
        "tp_before_sl": float(outcome_proba[0, 1]) if outcome_proba.shape[1] > 1 else float(outcome_proba[0, 0]),
    }


def decide(
    cfg: Config,
    symbol: str,
    timeframe: str,
    feature_row: pd.DataFrame,
    spread_pips: float = 0.0,
) -> dict:
    """Produce the agent-facing decision payload (JSON-shaped per README)."""
    model_set = load_model_set(cfg, symbol, timeframe)
    raw = predict_row(model_set, feature_row)

    buy_p = raw["direction"]["BUY"]
    sell_p = raw["direction"]["SELL"]
    no_p = raw["direction"]["NO_TRADE"]
    tp_p = raw["tp_before_sl"]

    min_conf = float(cfg.get("agent.min_confidence", 0.65))
    min_tp = float(cfg.get("agent.min_tp_probability", 0.65))
    max_spread = float(cfg.get("agent.max_spread_pips", 2.5))

    signal = "NO_TRADE"
    if raw["setup_valid"] >= min_conf:
        if buy_p >= sell_p and buy_p >= no_p and buy_p >= min_conf and tp_p >= min_tp and spread_pips <= max_spread:
            signal = "BUY"
        elif sell_p >= buy_p and sell_p >= no_p and sell_p >= min_conf and tp_p >= min_tp and spread_pips <= max_spread:
            signal = "SELL"

    expected_move_pips = None
    if signal != "NO_TRADE":
        try:
            from src.utils import pip_size
            atr = float(feature_row["atr"].iloc[0]) if "atr" in feature_row.columns else 0.0
            sl_mult = float(cfg.get_symbol("labels.atr_stop_multiple", symbol, 1.5))
            rr = int(cfg.get_symbol("labels.primary_rr", symbol, 3))
            if atr > 0:
                expected_move_pips = round(sl_mult * rr * atr / pip_size(symbol), 1)
        except Exception:
            expected_move_pips = None

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": signal,
        "buy_probability": round(buy_p, 3),
        "sell_probability": round(sell_p, 3),
        "no_trade_probability": round(no_p, 3),
        "setup_probability": round(raw["setup_valid"], 3),
        "tp_before_sl_probability": round(tp_p, 3),
        "expected_move_pips": expected_move_pips,
        "recommended_rr": int(cfg.get("labels.primary_rr", 3)),
        "confidence": round(max(buy_p, sell_p, no_p), 3),
        "spread_pips": spread_pips,
        "spread_acceptable": spread_pips <= max_spread,
    }
