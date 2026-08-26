"""Chronological splitting + model training (LightGBM, XGBoost, optional PyTorch).

NEVER use random splits on market data. All splits are by date with no overlap.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from src.config import Config


def date_splits(df: pd.DataFrame, cfg: Config) -> dict[str, np.ndarray]:
    """Return index arrays for train/validation/test/walk_forward by date."""
    splits = cfg.get("model.splits", {})
    t = pd.to_datetime(df["time"], utc=True)

    def between(start: str, end: str) -> np.ndarray:
        s = pd.Timestamp(start, tz="UTC")
        e = pd.Timestamp(end, tz="UTC")
        return np.where((t >= s) & (t < e))[0]

    return {
        "train": between(splits.get("train_start", "2018-01-01"), splits.get("train_end", "2022-12-31")),
        "validation": between(splits.get("validation_start", "2023-01-01"), splits.get("validation_end", "2023-12-31")),
        "test": between(splits.get("test_start", "2024-01-01"), splits.get("test_end", "2024-12-31")),
        "walk_forward": between(
            splits.get("walk_forward_start", "2025-01-01"), splits.get("walk_forward_end", "2099-12-31")
        ),
    }


def _class_weight(y: np.ndarray) -> dict[int, float]:
    counts = np.bincount(y)
    total = counts.sum()
    weights = {i: total / (len(counts) * max(c, 1)) for i, c in enumerate(counts)}
    return weights


def predict_proba(model, X: pd.DataFrame) -> np.ndarray:
    """Class probabilities for any trained model (sklearn/Torch/raw Booster).

    ``lgb.Booster.predict`` returns a 1-D prob array for binary objectives and
    ``xgb.Booster.predict`` returns 1-D for binary:logistic as well; sklearn and
    torch models return (n, 2). Normalize to the (n, k) column convention.
    """
    if hasattr(model, "predict_proba"):
        return model.predict_proba(X)
    p = model.predict(X)
    if p.ndim == 1:
        return np.column_stack([1.0 - p, p])
    if p.shape[1] == 1:
        return np.column_stack([1.0 - p[:, 0], p[:, 0]])
    return p


def train_lightgbm(
    X: pd.DataFrame,
    y: np.ndarray,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    params: dict | None = None,
    objective: str = "multiclass",
    num_class: int | None = None,
):
    import lightgbm as lgb

    p = {
        "objective": objective,
        "metric": "multi_logloss" if objective == "multiclass" else "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_child_samples": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
        "num_class": num_class,
    }
    if params:
        p.update(params)

    y_train = y[train_idx].astype(int)
    y_val = y[val_idx].astype(int)

    sample_weight = None
    if objective == "binary":
        p["is_unbalance"] = True
    elif objective == "multiclass":
        weights = _class_weight(y_train)
        sample_weight = np.array([weights[v] for v in y_train], dtype=float)

    dtr = lgb.Dataset(X.iloc[train_idx], label=y_train, weight=sample_weight)
    dval = lgb.Dataset(X.iloc[val_idx], label=y_val, reference=dtr)
    model = lgb.train(
        p, dtr, num_boost_round=1500, valid_sets=[dval],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
    )
    return model


def train_xgboost(
    X: pd.DataFrame,
    y: np.ndarray,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    params: dict | None = None,
    objective: str = "multi:softprob",
    num_class: int | None = None,
):
    import xgboost as xgb

    p = {
        "objective": objective,
        "eval_metric": "mlogloss" if objective.startswith("multi") else "logloss",
        "eta": 0.05,
        "max_depth": 6,
        "min_child_weight": 10,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "tree_method": "hist",
        "nthread": -1,
        "seed": 42,
    }
    if params:
        p.update(params)

    dtr = xgb.DMatrix(X.iloc[train_idx], label=y[train_idx].astype(int))
    dval = xgb.DMatrix(X.iloc[val_idx], label=y[val_idx].astype(int))
    model = xgb.train(
        p, dtr, num_boost_round=1500, evals=[(dval, "val")],
        early_stopping_rounds=50, verbose_eval=False,
    )
    return model


def train_torch(
    X: pd.DataFrame,
    y: np.ndarray,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    num_class: int,
    cfg: dict,
):
    """Optional MLP classifier. Raises a clear error if torch is unavailable."""
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is not installed. Install torch (pip install torch) to use torch models, "
            "or set model.torch.enabled=false."
        ) from exc

    from src.ml.torch_models import MLPClassifier, fit_mlp

    Xt = torch.tensor(X.iloc[train_idx].to_numpy(dtype=np.float32))
    yt = torch.tensor(y[train_idx].astype(np.int64))
    Xv = torch.tensor(X.iloc[val_idx].to_numpy(dtype=np.float32))
    yv = torch.tensor(y[val_idx].astype(np.int64))

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = MLPClassifier(
        input_dim=X.shape[1],
        hidden_sizes=cfg.get("hidden_sizes", [256, 128]),
        dropout=cfg.get("dropout", 0.3),
        num_classes=num_class,
    )
    fit_mlp(model, Xt, yt, Xv, yv, device=device, cfg=cfg)
    return model


def prepare_targets(df: pd.DataFrame, cfg: Config, primary_rr: int) -> dict[str, np.ndarray]:
    """Derive the three supervised targets from label columns."""
    rr = primary_rr
    label = df[f"label_rr{rr}"].to_numpy()
    buy_tp = df[f"buy_tp_first_rr{rr}"].to_numpy().astype(np.int64)
    sell_tp = df[f"sell_tp_first_rr{rr}"].to_numpy().astype(np.int64)

    setup = (label != 0).astype(np.int64)
    direction = np.full(len(df), 0, dtype=np.int64)  # 0 NO_TRADE
    direction[buy_tp == 1] = 1
    direction[sell_tp == 1] = 2
    direction[(buy_tp == 1) & (sell_tp == 1)] = 0

    return {"setup": setup, "direction": direction, "outcome": buy_tp}


def feature_columns(df: pd.DataFrame, cfg: Config) -> list[str]:
    exclude = {"time"}
    exclude.update(
        c for c in df.columns
        if c.startswith("label_") or c.startswith("buy_") or c.startswith("sell_")
        or c in {"open", "high", "low", "close", "tick_volume", "real_volume", "spread",
                 "swing_high", "swing_low", "swing_high_confirmed", "swing_low_confirmed"}
    )
    numeric = df.select_dtypes(include=[np.number]).columns
    return [c for c in df.columns if c not in exclude and c in numeric]
