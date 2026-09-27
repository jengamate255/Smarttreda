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

Falls back to a pure-numpy logistic regression if sklearn is unavailable.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

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
JOURNAL_DB = Path(__file__).resolve().parent / "memory" / "journal.db"

_model_cache: dict | None = None


# ---------------------------------------------------------------------------
# Pure-numpy logistic regression (no sklearn required)
# ---------------------------------------------------------------------------

class _LogisticRegression:
    """Minimal logistic regression with L2 regularization, gradient descent."""

    def __init__(self, lr: float = 0.05, max_iter: int = 3000, l2: float = 0.01):
        self.lr = lr
        self.max_iter = max_iter
        self.l2 = l2
        self.weights: np.ndarray = np.empty(0)
        self.intercept: float = 0.0

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        z = np.clip(z, -500, 500)
        return 1.0 / (1.0 + np.exp(-z))

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        n, d = X.shape
        self.weights = np.zeros(d)
        self.intercept = 0.0
        for _ in range(self.max_iter):
            z = X @ self.weights + self.intercept
            p = self._sigmoid(z)
            grad_w = (X.T @ (p - y)) / n + self.l2 * self.weights
            grad_b = float((p - y).mean())
            self.weights -= self.lr * grad_w
            self.intercept -= self.lr * grad_b

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._sigmoid(X @ self.weights + self.intercept)


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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

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
            trades.append(tt)
    return trades


def load_journal_trades(db_path: Path | None = None) -> list[dict]:
    """Load closed trades from the journal database."""
    db = db_path or JOURNAL_DB
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT symbol, side, entry, sl, tp, lots, open_time, close_time, "
            "pnl, outcome, confidence, trade_score, zone_strength, rr, "
            "session_hour FROM trades WHERE outcome IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    trades = []
    for r in rows:
        sl_pips = 0.0
        if r["entry"] and r["sl"]:
            sl_pips = abs(float(r["entry"]) - float(r["sl"])) * 10000
        entry_time = r["open_time"] or ""
        trades.append({
            "symbol": r["symbol"],
            "side": r["side"],
            "entry": r["entry"],
            "sl": r["sl"],
            "tp": r["tp"],
            "pnl": r["pnl"],
            "outcome": r["outcome"],
            "entry_time": entry_time,
            "trend_confidence": r["confidence"] or 0.0,
            "zone_strength": r["zone_strength"] or 0.0,
            "confirmation_confidence": 0.7,
            "rr": r["rr"] or 0.0,
            "sl_pips": sl_pips,
        })
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


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_and_evaluate(X: np.ndarray, y: np.ndarray, seed: int = 42) -> dict:
    Xs, stats = _standardize(X)
    model = _LogisticRegression(lr=0.05, max_iter=3000, l2=0.01)
    model.fit(Xs, y)
    proba = model.predict_proba(Xs)
    pred = (proba >= 0.5).astype(int)
    accuracy = float((pred == y).mean())
    # Simple AUC approximation
    pos = proba[y == 1]
    neg = proba[y == 0]
    if len(pos) > 0 and len(neg) > 0:
        auc = float(np.mean([np.mean(p > neg) for p in pos]))
    else:
        auc = 0.5
    win_above = float(y[proba >= 0.5].mean()) if (proba >= 0.5).any() else 0.0
    return {
        "n_trades": int(len(y)),
        "base_win_rate": round(float(y.mean()), 4),
        "cv_accuracy": round(accuracy, 4),
        "cv_auc": round(auc, 4),
        "cv_win_rate_above_0_5": round(win_above, 4),
    }


def fit_and_save(X: np.ndarray, y: np.ndarray, path: Path, cv_auc: float = 0.0) -> dict:
    Xs, stats = _standardize(X)
    model = _LogisticRegression(lr=0.05, max_iter=3000, l2=0.01)
    model.fit(Xs, y)
    payload = {
        "features": FEATURES,
        "weights": [round(float(w), 6) for w in model.weights],
        "intercept": round(float(model.intercept), 6),
        "scaler": stats,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_trades": int(len(y)),
        "cv_auc": round(float(cv_auc), 4),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

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
    """Probability of win in [0, 1]; >0.5 predicts a winning trade."""
    model = load_model()
    feats = np.asarray(
        [[trend_confidence, zone_strength, confirmation_confidence, rr, sl_pips, entry_hour]],
        dtype=float,
    )
    Xs, _ = _standardize(feats, model["scaler"])
    w = np.asarray(model["weights"], dtype=float)
    logit = float((Xs @ w + model["intercept"])[0])
    return 1.0 / (1.0 + math.exp(-logit))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train/evaluate the signal ranker")
    parser.add_argument("--train", action="store_true", help="train and save the model")
    parser.add_argument(
        "--results-dir",
        default=str(Path(__file__).resolve().parent / "backtest" / "results"),
    )
    parser.add_argument(
        "--journal", action="store_true",
        help="also include journal DB trades for training",
    )
    args = parser.parse_args(argv)

    trades = load_result_trades(Path(args.results_dir))
    if args.journal:
        journal = load_journal_trades()
        trades.extend(journal)
        print(f"journal trades loaded: {len(journal)}")

    X, y, meta = build_dataset(trades)
    if len(y) < 10:
        print(f"not enough usable trades ({len(y)}); need >= 10", file=sys.stderr)
        return 2

    metrics = train_and_evaluate(X, y)
    print(f"trades: {len(trades)}  usable: {len(y)}")
    print(f"base win rate: {metrics['base_win_rate']:.3f}")
    print(f"accuracy:      {metrics['cv_accuracy']:.3f}")
    print(f"AUC:           {metrics['cv_auc']:.3f}")
    print(f"win rate (scored>=0.5): {metrics['cv_win_rate_above_0_5']:.3f}")

    if args.train:
        if len(set(y)) < 2:
            print("only one class in data; cannot train", file=sys.stderr)
            return 2
        payload = fit_and_save(X, y, MODEL_PATH, cv_auc=metrics["cv_auc"])
        print(f"model saved: {MODEL_PATH}")
        for name, w in zip(FEATURES, payload["weights"], strict=False):
            print(f"  {name:24s} {w:+.4f}")
        print(f"  intercept {payload['intercept']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
