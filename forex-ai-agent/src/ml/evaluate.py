"""Model evaluation: classification metrics + trade-oriented expected value."""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def _safe_auc(y_true: np.ndarray, proba: np.ndarray, classes: list[int]) -> float | None:
    if len(classes) < 2 or len(np.unique(y_true)) < 2:
        return None
    try:
        if len(classes) == 2:
            return float(roc_auc_score(y_true, proba[:, 1]))
        return float(roc_auc_score(y_true, proba, multi_class="ovr", average="macro"))
    except ValueError:
        return None


def evaluate_classifier(
    y_true: np.ndarray,
    proba: np.ndarray,
    classes: list[int],
    split_name: str = "test",
) -> dict[str, Any]:
    """Classification metrics for multiclass or binary problems."""
    y_pred = np.argmax(proba, axis=1)
    if len(classes) == 2:
        y_pred_bin = proba[:, 1] >= 0.5
    else:
        y_pred_bin = y_pred

    metrics: dict[str, Any] = {
        "split": split_name,
        "n_samples": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred_bin)),
        "brier": float(brier_score_loss(y_true, proba[:, 1])) if len(classes) == 2 else None,
    }
    try:
        metrics["logloss"] = float(log_loss(y_true, proba, labels=classes))
    except ValueError:
        metrics["logloss"] = None
    metrics["auc"] = _safe_auc(y_true, proba, classes)
    try:
        metrics["f1_macro"] = float(f1_score(y_true, y_pred_bin, average="macro", zero_division=0))
        metrics["precision_macro"] = float(precision_score(y_true, y_pred_bin, average="macro", zero_division=0))
        metrics["recall_macro"] = float(recall_score(y_true, y_pred_bin, average="macro", zero_division=0))
    except ValueError:
        pass

    dist = {}
    for i, cls in enumerate(classes):
        mask = y_true == cls
        dist[str(cls)] = {
            "count": int(mask.sum()),
            "mean_pred_prob": float(proba[mask, i].mean()) if mask.any() else None,
        }
    metrics["class_distribution"] = dist
    return metrics


def expected_value_table(
    proba_tp: np.ndarray,
    y_tp: np.ndarray,
    r_targets: np.ndarray,
    thresholds: list[float],
) -> list[dict]:
    """Expected R (in R units) and trade counts when filtering by P(TP before SL).

    r_targets: realized R for each bar (+RR on TP, -1 on SL, 0 on no-result),
    evaluated on the buy side.
    """
    rows = []
    for thr in thresholds:
        take = proba_tp >= thr
        n_take = int(take.sum())
        if n_take == 0:
            rows.append({"threshold": thr, "n_trades": 0, "expected_r": 0.0, "win_rate": None})
            continue
        r = r_targets[take]
        rows.append({
            "threshold": thr,
            "n_trades": n_take,
            "expected_r": float(r.mean()),
            "win_rate": float((y_tp[take] == 1).mean()),
        })
    return rows


def realized_r_from_labels(label: np.ndarray, rr: int) -> np.ndarray:
    """Convert buy-side label (-1/0/1) into realized R (RR, 0, -1)."""
    r = np.zeros(len(label))
    r[label == 1] = rr
    r[label == -1] = -1.0
    return r


def evaluate_all(
    y_true: dict[str, np.ndarray],
    proba: dict[str, np.ndarray],
    classes: dict[str, list[int]],
    cfg: Any,
    rr: int,
    label: np.ndarray,
) -> dict[str, Any]:
    out = {}
    for task in ("setup", "direction", "outcome"):
        if task in proba:
            out[task] = evaluate_classifier(y_true[task], proba[task], classes[task], "test")

    if "outcome" in proba:
        r_targets = realized_r_from_labels(label, rr)
        thr = [float(x) for x in cfg.get("model.evaluate_thresholds", [0.5, 0.6, 0.65, 0.7, 0.75, 0.8])]
        out["outcome"]["expected_value"] = expected_value_table(
            proba["outcome"][:, 1] if proba["outcome"].shape[1] > 1 else proba["outcome"],
            y_true["outcome"],
            r_targets,
            thr,
        )
    return out


def save_metrics(path, metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
