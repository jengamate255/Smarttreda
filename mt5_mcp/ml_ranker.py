"""ML signal ranker: scores strategy setups with a logistic regression trained
on backtest trade outcomes.

Usage (train + evaluate on collected backtest trades):

    python ml_ranker.py --train

The fitted model (feature weights, intercept, scaler stats) is saved to
models/signal_ranker.json and loaded lazily by rank_signal().

Features (all computable at decision time, no lookahead):
    trend_confidence, zone_strength, confirmation_confidence, rr,
    sl_pips, entry_hour
Target: win (pnl > 0).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import KFold, cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parent))

FEATURES = [
    "trend_confidence",
    "zone_strength",
    "confirmation_confidence",
    "rr",
    "sl_pips",
    "entry_hour",
]

MODEL_PATH = Path(__file__).resolve().parent / "models" / "signal_ranker.json"

_model_cache: dict | None = None


def _entry_hour(entry_time: str) -> float:
    try:
        return float(entry_time[11:13])
    except (ValueError, IndexError, TypeError):
        return 12.0


def _trade_features(trade: dict) -> list[float] | None:
    if trade.get("entry") is None or not trade.get("entry_time"):
        return None
    return [
        float(trade.get("trend_confidence") or 0.0),
        float(trade.get("zone_strength") or 0.0),
        float(trade.get("confirmation_confidence") or 0.0)
        if trade.get("confirmation_confidence") is not None
        else 0.0,
        float(trade.get("rr") or 0.0),
        float(trade.get("sl_pips") or 0.0),
        _entry_hour(str(trade.get("entry_time", ""))),
    ]


def _parse_conf_confidence(trade: dict) -> float:
    conf = str(trade.get("confirmation") or "")
    if "conf " in conf:
        try:
            return float(conf.split("conf ")[1].split(")")[0])
        except (ValueError, IndexError):
            return 0.0
    return 0.0


def load_result_trades(results_dir: Path) -> list[dict]:
    """All trades from the default-variant results (one per signal)."""
    trades: list[dict] = []
    for f in sorted(results_dir.glob("*_rr3_tp80-100.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        symbol = str(data.get("params", {}).get("symbol") or f.stem)
        for t in data.get("trades", []):
            tt = dict(t)
            tt["symbol"] = symbol
            tt["confirmation_confidence"] = _parse_conf_confidence(t)
            trades.append(tt)
    return trades


def build_dataset(trades: list[dict]) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    X, y, meta = [], [], []
    for t in trades:
        feats = _trade_features(t)
        if feats is None:
            continue
        X.append(feats)
        y.append(1 if t.get("pnl", 0.0) > 0 else 0)
        meta.append(t)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int), meta


def _standardize(X: np.ndarray, stats: dict | None = None):
    X = np.asarray(X, dtype=float)
    if stats is None:
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std < 1e-12] = 1.0
    else:
        mean = np.asarray(stats["mean"], dtype=float)
        std = np.asarray(stats["std"], dtype=float)
    return (X - mean) / std, {"mean": mean.tolist(), "std": std.tolist()}


def train_and_evaluate(X: np.ndarray, y: np.ndarray, seed: int = 42) -> dict:
    Xs, stats = _standardize(X)
    kf = KFold(n_splits=5, shuffle=True, random_state=seed)
    pred = cross_val_predict(
        LogisticRegression(C=1.0, max_iter=2000, random_state=seed), Xs, y, cv=kf
    )
    proba = cross_val_predict(
        LogisticRegression(C=1.0, max_iter=2000, random_state=seed),
        Xs,
        y,
        cv=kf,
        method="predict_proba",
    )[:, 1]
    metrics = {
        "n_trades": int(len(y)),
        "base_win_rate": round(float(y.mean()), 4),
        "cv_accuracy": round(float(accuracy_score(y, pred)), 4),
        "cv_auc": round(float(roc_auc_score(y, proba)), 4) if len(set(y)) > 1 else 0.0,
        "cv_win_rate_above_0_5": (
            round(float(y[proba >= 0.5].mean()), 4) if (proba >= 0.5).any() else 0.0
        ),
    }
    return metrics


def fit_and_save(X: np.ndarray, y: np.ndarray, path: Path, cv_auc: float = 0.0) -> dict:
    Xs, stats = _standardize(X)
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=42)
    model.fit(Xs, y)
    payload = {
        "features": FEATURES,
        "weights": [round(float(w), 6) for w in model.coef_[0]],
        "intercept": round(float(model.intercept_[0]), 6),
        "scaler": stats,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_trades": int(len(y)),
        "cv_auc": round(float(cv_auc), 4),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_model() -> dict:
    global _model_cache
    if _model_cache is None:
        _model_cache = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    return _model_cache


def rank_signal(
    trend_confidence: float,
    zone_strength: float,
    confirmation_confidence: float,
    rr: float,
    sl_pips: float,
    entry_hour: float,
) -> float:
    """Logit of the ML model in [0, 1]; >0.5 predicts a winning trade."""
    model = load_model()
    feats = np.asarray(
        [[trend_confidence, zone_strength, confirmation_confidence, rr, sl_pips, entry_hour]],
        dtype=float,
    )
    Xs, _ = _standardize(feats, model["scaler"])
    w = np.asarray(model["weights"], dtype=float)
    logit = float((Xs @ w + model["intercept"])[0])
    return 1.0 / (1.0 + np.exp(-logit))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train/evaluate the signal ranker")
    parser.add_argument("--train", action="store_true", help="train and save the model")
    parser.add_argument(
        "--results-dir",
        default=str(Path(__file__).resolve().parent / "backtest" / "results"),
    )
    args = parser.parse_args(argv)

    trades = load_result_trades(Path(args.results_dir))
    X, y, meta = build_dataset(trades)
    metrics = train_and_evaluate(X, y)
    print(f"trades: {len(trades)}  usable: {len(y)}")
    print(f"base win rate: {metrics['base_win_rate']:.3f}")
    print(f"CV accuracy:  {metrics['cv_accuracy']:.3f}")
    print(f"CV AUC:       {metrics['cv_auc']:.3f}")
    print(f"CV win rate on scored>=0.5: {metrics['cv_win_rate_above_0_5']:.3f}")

    if args.train:
        if len(set(y)) < 2 or len(y) < 20:
            print("not enough data to train; skipping save", file=sys.stderr)
            return 2
        payload = fit_and_save(X, y, MODEL_PATH, cv_auc=metrics["cv_auc"])
        print(f"model saved: {MODEL_PATH}")
        for name, w in zip(FEATURES, payload["weights"], strict=False):
            print(f"  {name:24s} {w:+.4f}")
        print(f"  intercept {payload['intercept']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
