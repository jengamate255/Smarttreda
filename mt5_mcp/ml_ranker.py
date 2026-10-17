"""ML signal ranker: scores strategy setups with a logistic regression trained
on backtest trade outcomes.

Usage (train + evaluate on collected backtest trades):

    python ml_ranker.py --train

The fitted model (feature weights, intercept, scaler stats) is saved to
models/signal_ranker.json and loaded lazily by rank_signal().

Features (all computable at decision time, no lookahead):
    trend_confidence, zone_strength, confirmation_confidence, rr,
    sl_pips, entry_hour, session, trend_alignment,
    separation_atr, mtf_aligned, atr_val, zone_proximity, zone_touches
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
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

FEATURES = [
    "trend_confidence",
    "zone_strength",
    "confirmation_confidence",
    "rr",
    "sl_pips",
    "entry_hour",
    "session",  # 1=Asian, 2=London, 3=NY
    "trend_alignment",  # 1.0 if trend_confidence > 0.5, else 0.0
    "separation_atr",  # EMA separation normalized by ATR
    "mtf_aligned",  # count of timeframes aligned with trend
    "atr_val",  # ATR value (volatility level)
    "zone_proximity",  # 0.6/0.8/1.0 how close price is to zone
    "zone_touches",  # number of zone retests
]

MODEL_PATH = Path(__file__).resolve().parent / "models" / "signal_ranker.json"
JOURNAL_DB = Path(__file__).resolve().parent / "memory" / "journal.db"
RETRAIN_STATE_PATH = Path(__file__).resolve().parent / "models" / "retrain_state.json"

RETRAIN_THRESHOLD = 50  # retrain after this many new trades

_model_cache: dict | None = None
_model_cache_signature: tuple | None = None


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


def _session_from_hour(hour: float) -> float:
    """Map hour to session: 1=Asian (00-08), 2=London (08-13), 3=NY (13-22)."""
    if 0 <= hour < 8:
        return 1.0  # Asian
    elif 8 <= hour < 13:
        return 2.0  # London
    else:
        return 3.0  # NY


def _trade_features(trade: dict) -> list[float] | None:
    if trade.get("entry") is None or not trade.get("entry_time"):
        return None
    hour = _entry_hour(str(trade.get("entry_time", "")))
    trend_conf = float(trade.get("trend_confidence") or 0.0)
    return [
        trend_conf,
        float(trade.get("zone_strength") or 0.0),
        float(trade.get("confirmation_confidence") or 0.0)
        if trade.get("confirmation_confidence") is not None
        else 0.0,
        float(trade.get("rr") or 0.0),
        float(trade.get("sl_pips") or 0.0),
        hour,
        _session_from_hour(hour),
        1.0 if trend_conf > 0.5 else 0.0,  # trend_alignment
        float(trade.get("separation_atr") or 0.0),
        float(trade.get("mtf_aligned") or 0.0),
        float(trade.get("atr_val") or 0.0),
        float(trade.get("zone_proximity") or 0.0),
        float(trade.get("zone_touches") or 0.0),
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
            # Filter out extreme sl_pips outliers (>500 pips)
            if tt.get("sl_pips", 0) > 500:
                continue
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
            "session_hour, sl_pips, trend_confidence, confirmation_confidence, "
            "separation_atr, mtf_aligned, atr_val, zone_proximity, zone_touches "
            "FROM trades WHERE outcome IN ('win','loss','breakeven') "
            "AND pnl IS NOT NULL AND close_time IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    trades = []
    for r in rows:
        sl_pips = float(r["sl_pips"] or 0)
        if sl_pips <= 0 and r["entry"] and r["sl"]:
            sym = (r["symbol"] or "").upper()
            entry_f = float(r["entry"])
            sl_f = float(r["sl"])
            raw = abs(entry_f - sl_f)
            if "BTC" in sym or "ETH" in sym:
                sl_pips = raw * 100
            elif "XAU" in sym or "XAG" in sym:
                sl_pips = raw * 10
            elif "INDEX" in sym or "JP225" in sym or "SPX" in sym or "NDX" in sym or "DJI" in sym or "BOOM" in sym or "CRASH" in sym:
                sl_pips = raw * 10
            else:
                sl_pips = raw * 10000
        # Filter out extreme sl_pips outliers
        if sl_pips > 500:
            continue
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
            "exit_time": r["close_time"],
            "trend_confidence": float(r["trend_confidence"] or r["confidence"] or 0.0),
            "zone_strength": r["zone_strength"] or 0.0,
            "confirmation_confidence": float(r["confirmation_confidence"] or 0.7),
            "rr": r["rr"] or 0.0,
            "sl_pips": sl_pips,
            "separation_atr": r["separation_atr"] or 0.0,
            "mtf_aligned": r["mtf_aligned"] or 0,
            "atr_val": r["atr_val"] or 0.0,
            "zone_proximity": r["zone_proximity"] or 0.0,
            "zone_touches": r["zone_touches"] or 0,
        })
    return trades


def _epoch(value: str) -> float:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def build_dataset(trades: list[dict]) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    X, y, meta = [], [], []
    usable = []
    seen = set()
    for t in trades:
        try:
            feats = _trade_features(t)
            stamp = _epoch(t.get("entry_time"))
            pnl = float(t.get("pnl"))
        except (ValueError, TypeError, OverflowError):
            continue
        if feats is None or not math.isfinite(pnl) or not np.isfinite(feats).all():
            continue
        key = (t.get("symbol"), t.get("side"), stamp, t.get("entry"), t.get("sl"), t.get("tp"))
        if key in seen:
            continue
        seen.add(key)
        usable.append((stamp, feats, pnl, t))
    for stamp, feats, pnl, t in sorted(usable, key=lambda item: item[0]):
        X.append(feats)
        y.append(1 if pnl > 0 else 0)
        meta.append(t)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int), meta


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_and_evaluate(X: np.ndarray, y: np.ndarray, seed: int = 42, meta: list[dict] | None = None) -> dict:
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
        auc = float(np.mean([np.mean((p > neg) + 0.5 * (p == neg)) for p in pos]))
    else:
        auc = 0.5
    win_above = float(y[proba >= 0.5].mean()) if (proba >= 0.5).any() else 0.0
    validation = walk_forward_validation(X, y, meta=meta)
    return {
        "n_trades": int(len(y)),
        "base_win_rate": round(float(y.mean()), 4),
        "training_accuracy": round(accuracy, 4),
        "training_auc": round(auc, 4),
        "training_win_rate_above_0_5": round(win_above, 4),
        "cv_accuracy": validation.get("test_accuracy", 0.0),
        "cv_auc": validation.get("test_auc", 0.5),
        "cv_win_rate_above_0_5": validation.get("test_win_rate_above_0_5", 0.0),
        "validation_ok": meta is not None and validation.get("n_test_trades", 0) >= 20 and "error" not in validation,
        "validation": validation,
    }


def walk_forward_validation(X: np.ndarray, y: np.ndarray, n_splits: int = 5, meta: list[dict] | None = None) -> dict:
    """Chronological train/test split to prevent lookahead bias.

    Splits data into n_splits consecutive chunks. For each split k:
    - Train on chunks 0..k
    - Test on chunk k+1
    Returns aggregated metrics across all test sets.
    """
    n = len(y)
    chunk_size = n // (n_splits + 1)
    if chunk_size < 10:
        return {"error": "Not enough data for walk-forward validation"}

    test_probas = []
    test_labels = []
    if meta is not None:
        try:
            starts = np.array([_epoch(row["entry_time"]) for row in meta])
            finishes = np.array([_epoch(row["exit_time"]) for row in meta])
            if len(meta) != n or np.any(np.diff(starts) < 0) or np.any(finishes < starts):
                return {"error": "Invalid chronological outcome metadata"}
        except (KeyError, ValueError, TypeError):
            return {"error": "Completed entry/exit timestamps required for purged validation"}

    for k in range(n_splits):
        train_end = (k + 1) * chunk_size
        test_start = train_end
        test_end = min((k + 2) * chunk_size, n)

        if test_end <= test_start:
            break

        X_train, y_train = X[:train_end], y[:train_end]
        X_test, y_test = X[test_start:test_end], y[test_start:test_end]
        if meta is not None:
            known = (starts[:train_end] < starts[test_start]) & (finishes[:train_end] < starts[test_start])
            X_train, y_train = X_train[known], y_train[known]
        if len(y_train) < 10 or len(set(y_train)) < 2:
            continue

        # Standardize using training stats
        Xs_train, stats = _standardize(X_train)
        Xs_test, _ = _standardize(X_test, stats)

        # Train and predict
        model = _LogisticRegression(lr=0.05, max_iter=3000, l2=0.01)
        model.fit(Xs_train, y_train)
        proba = model.predict_proba(Xs_test)

        test_probas.extend(proba.tolist())
        test_labels.extend(y_test.tolist())

    if not test_labels:
        return {"error": "No test data"}

    test_probas = np.array(test_probas)
    test_labels = np.array(test_labels)
    if len(set(test_labels)) < 2:
        return {"error": "Both win and loss outcomes required in held-out trades"}

    # Calculate metrics
    pred = (test_probas >= 0.5).astype(int)
    accuracy = float((pred == test_labels).mean())

    pos = test_probas[test_labels == 1]
    neg = test_probas[test_labels == 0]
    if len(pos) > 0 and len(neg) > 0:
        auc = float(np.mean([np.mean((p > neg) + 0.5 * (p == neg)) for p in pos]))
    else:
        auc = 0.5

    win_above = float(test_labels[test_probas >= 0.5].mean()) if (test_probas >= 0.5).any() else 0.0

    return {
        "n_test_trades": len(test_labels),
        "test_accuracy": round(accuracy, 4),
        "test_auc": round(auc, 4),
        "test_win_rate_above_0_5": round(win_above, 4),
        "n_splits": n_splits,
    }


def kelly_fraction(X: np.ndarray, y: np.ndarray, safety: float = 0.5) -> dict:
    """Calculate Kelly criterion for optimal position sizing.

    Kelly fraction = p - q/b where:
    - p = win probability
    - q = 1 - p (loss probability)
    - b = win/loss ratio (avg win / avg loss)

    Uses half-Kelly (safety=0.5) by default for conservative sizing.
    Calculates Kelly for both the full dataset and the ML-filtered subset
    (trades the model scores >= 0.5), since we only take filtered trades.
    """
    # First train the model to get predictions
    Xs, stats = _standardize(X)
    model = _LogisticRegression(lr=0.05, max_iter=3000, l2=0.01)
    model.fit(Xs, y)
    proba = model.predict_proba(Xs)

    def _calc_kelly(labels: np.ndarray, b: float = 2.0) -> dict:
        n = len(labels)
        if n == 0:
            return None
        wins = int(labels.sum())
        p = wins / n
        q = 1.0 - p
        kelly = p - q / b
        return {
            "n": n,
            "wins": wins,
            "win_rate": round(p, 4),
            "kelly": round(kelly, 4),
            "adjusted_kelly": round(kelly * safety, 4),
        }

    # Full dataset
    full = _calc_kelly(y)

    # ML-filtered subset (model predicts win, score >= 0.30 = actual filter threshold)
    mask = proba >= 0.30
    filtered = _calc_kelly(y[mask]) if mask.any() else None

    # Use filtered Kelly if available (that's what we actually trade)
    best = filtered or full
    kelly = best["kelly"]
    adjusted = best["adjusted_kelly"]

    result = {
        "win_loss_ratio": 2.0,
        "safety_factor": safety,
        "full_dataset": full,
        "kelly_fraction": round(kelly, 4),
        "adjusted_kelly": round(adjusted, 4),
    }
    if filtered:
        result["filtered_dataset"] = filtered
    return result


def kelly_risk_multiplier(ml_score: float, base_kelly: float, safety: float = 0.5) -> float:
    """Calculate per-trade risk multiplier based on ML confidence.

    Higher ML scores (more confident winners) get closer to full Kelly.
    Lower scores get reduced risk. Returns a multiplier in [0.3, 1.0]
    to apply to the base risk_amount.

    ml_score: model's predicted win probability for this trade
    base_kelly: overall Kelly fraction from kelly_fraction()
    safety: safety factor (0.5 = half-Kelly)
    """
    if ml_score <= 0 or base_kelly <= 0:
        return 0.3  # minimum risk for uncertain signals

    # Scale multiplier by confidence: high-confidence signals get more risk
    # ml_score of 0.5 (coin flip) → 0.3x risk
    # ml_score of 0.8 (high confidence) → 1.0x risk
    confidence_scale = max(0.3, min(1.0, (ml_score - 0.3) / 0.5))

    return round(confidence_scale, 3)


def fit_and_save(X: np.ndarray, y: np.ndarray, path: Path, cv_auc: float = 0.0, validated: bool = False) -> dict:
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
        "validation_method": "purged_walk_forward" if validated else "unvalidated",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        json.dump(payload, file, indent=2)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def load_model() -> dict:
    global _model_cache, _model_cache_signature
    stat = MODEL_PATH.stat()
    signature = (str(MODEL_PATH.resolve()), stat.st_mtime_ns, stat.st_size)
    if _model_cache is None or signature != _model_cache_signature:
        _model_cache = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        _model_cache_signature = signature
    return _model_cache


def rank_signal(
    trend_confidence: float,
    zone_strength: float,
    confirmation_confidence: float,
    rr: float,
    sl_pips: float,
    entry_hour: float,
    session: float | None = None,
    trend_alignment: float | None = None,
    separation_atr: float = 0.0,
    mtf_aligned: float = 0.0,
    atr_val: float = 0.0,
    zone_proximity: float = 0.0,
    zone_touches: float = 0.0,
) -> float:
    """Probability of win in [0, 1]; >0.5 predicts a winning trade."""
    model = load_model()
    if session is None:
        session = _session_from_hour(entry_hour)
    if trend_alignment is None:
        trend_alignment = 1.0 if trend_confidence > 0.5 else 0.0
    feats = np.asarray(
        [[trend_confidence, zone_strength, confirmation_confidence, rr, sl_pips,
          entry_hour, session, trend_alignment,
          separation_atr, mtf_aligned, atr_val, zone_proximity, zone_touches]],
        dtype=float,
    )
    n_model_feats = len(model["weights"])
    if feats.shape[1] != n_model_feats:
        feats = feats[:, :n_model_feats]
    Xs, _ = _standardize(feats, model["scaler"])
    w = np.asarray(model["weights"], dtype=float)
    logit = float((Xs @ w + model["intercept"])[0])
    return 1.0 / (1.0 + math.exp(-logit))


# ---------------------------------------------------------------------------
# Live feedback loop — auto-retrain when enough new trades accumulate
# ---------------------------------------------------------------------------

def _load_retrain_state() -> dict:
    if RETRAIN_STATE_PATH.exists():
        try:
            return json.loads(RETRAIN_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_n_trades": 0, "last_retrained": None, "retrain_count": 0}


def _save_retrain_state(state: dict) -> None:
    RETRAIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETRAIN_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def model_staleness(results_dir: Path | None = None, include_journal: bool = True) -> dict:
    """Check how stale the current model is.

    Returns dict with current trade count, model's trained trade count,
    and whether retraining is recommended.
    """
    results = results_dir or Path(__file__).resolve().parent / "backtest" / "results"
    trades = load_result_trades(results)
    if include_journal:
        trades.extend(load_journal_trades())
    X, y, meta = build_dataset(trades)

    current_n = len(y)

    model_n = 0
    trained_at = None
    if MODEL_PATH.exists():
        try:
            model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
            model_n = model.get("n_trades", 0)
            trained_at = model.get("trained_at")
        except (json.JSONDecodeError, OSError):
            pass

    new_trades = current_n - model_n
    state = _load_retrain_state()

    return {
        "current_trades": current_n,
        "model_trades": model_n,
        "new_trades_since_train": new_trades,
        "trained_at": trained_at,
        "retrain_threshold": RETRAIN_THRESHOLD,
        "needs_retrain": new_trades >= RETRAIN_THRESHOLD,
        "last_retrained": state.get("last_retrained"),
        "retrain_count": state.get("retrain_count", 0),
    }


def retrain_with_new_data(
    results_dir: Path | None = None,
    include_journal: bool = True,
    auc_tolerance: float = 0.02,
) -> dict:
    """Retrain model with new data, comparing AUC to the old model.

    Promotion requires purged chronological validation, AUC >= 0.55 and
    no degradation beyond tolerance against a previously validated model.
    Legacy in-sample scores cannot supply that baseline.

    Returns dict with status, old/new AUC, and decision.
    """
    results = results_dir or Path(__file__).resolve().parent / "backtest" / "results"

    # Load old model AUC
    old_auc = 0.0
    old_n = 0
    if MODEL_PATH.exists():
        try:
            old_model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
            old_auc = float(old_model.get("cv_auc", 0.0))
            old_n = int(old_model.get("n_trades", 0))
        except (json.JSONDecodeError, OSError):
            pass

    # Gather all trades
    trades = load_result_trades(results)
    if include_journal:
        trades.extend(load_journal_trades())
    X, y, meta = build_dataset(trades)
    new_n = int(len(y))

    # If no new trades since last training, skip (only when a model exists)
    if old_n > 0 and new_n <= old_n:
        return {
            "status": "skipped",
            "reason": f"no new trades (old={old_n}, current={new_n})",
            "old_auc": old_auc,
            "new_auc": old_auc,
            "old_n_trades": old_n,
            "new_n_trades": new_n,
        }

    if new_n < 10 or len(set(y)) < 2:
        return {"status": "error", "reason": "not enough data to train"}

    # Train new model and evaluate
    metrics = train_and_evaluate(X, y, meta=meta)
    new_auc = metrics["cv_auc"]

    # Compare: accept if new AUC >= old AUC - tolerance
    auc_delta = new_auc - old_auc
    old_validated = old_n > 0 and old_model.get("validation_method") == "purged_walk_forward"
    baseline = old_auc if old_validated else 0.5
    accepted = bool(metrics.get("validation_ok")) and new_auc >= max(0.55, baseline - auc_tolerance)

    result = {
        "status": "retrained" if accepted else "rejected",
        "old_auc": old_auc,
        "new_auc": new_auc,
        "auc_delta": round(auc_delta, 4),
        "auc_tolerance": auc_tolerance,
        "old_n_trades": old_n,
        "new_n_trades": new_n,
        "new_trades_added": new_n - old_n,
        "accepted": accepted,
        "validation": metrics.get("validation"),
    }

    if accepted:
        # Walk-forward validation for extra confidence
        wf = metrics["validation"]
        result["walk_forward_auc"] = wf.get("test_auc")

        # Save the new model
        payload = fit_and_save(X, y, MODEL_PATH, cv_auc=new_auc, validated=True)
        result["weights"] = payload["weights"]

        # Update retrain state
        state = _load_retrain_state()
        state["last_n_trades"] = new_n
        state["last_retrained"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state["retrain_count"] = state.get("retrain_count", 0) + 1
        _save_retrain_state(state)

        # Invalidate model cache
        global _model_cache
        _model_cache = None
    else:
        result["reason"] = (
            "purged future-trade validation unavailable or below the 0.55/baseline quality floor"
        )

    return result


def retrain_if_stale(
    results_dir: Path | None = None,
    threshold: int | None = None,
    include_journal: bool = True,
) -> dict:
    """Retrain the model if enough new trades have accumulated.

    Returns a dict describing what happened: 'retrained', 'skipped', or 'error'.
    """
    threshold = threshold if threshold is not None else RETRAIN_THRESHOLD
    results = results_dir or Path(__file__).resolve().parent / "backtest" / "results"

    staleness = model_staleness(results, include_journal)

    if staleness["new_trades_since_train"] < threshold:
        return {
            "status": "skipped",
            "reason": f"only {staleness['new_trades_since_train']} new trades (threshold {threshold})",
            "staleness": staleness,
        }

    return retrain_with_new_data(results_dir=results, include_journal=include_journal)


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
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="run walk-forward validation (chronological train/test splits)",
    )
    parser.add_argument(
        "--n-splits", type=int, default=5,
        help="number of walk-forward splits (default 5)",
    )
    parser.add_argument(
        "--kelly", action="store_true",
        help="calculate Kelly criterion for position sizing",
    )
    parser.add_argument(
        "--staleness", action="store_true",
        help="check if model needs retraining",
    )
    parser.add_argument(
        "--auto-retrain", action="store_true",
        help="retrain model if enough new trades have accumulated",
    )
    parser.add_argument(
        "--retrain-threshold", type=int, default=RETRAIN_THRESHOLD,
        help=f"min new trades before auto-retrain (default {RETRAIN_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    if args.staleness:
        s = model_staleness(
            Path(args.results_dir) if args.results_dir else None,
            include_journal=args.journal,
        )
        print(f"model trained at:  {s['trained_at'] or 'unknown'}")
        print(f"model trades:      {s['model_trades']}")
        print(f"current trades:    {s['current_trades']}")
        print(f"new since train:   {s['new_trades_since_train']}")
        print(f"retrain threshold: {s['retrain_threshold']}")
        print(f"needs retrain:     {s['needs_retrain']}")
        print(f"retrain count:     {s['retrain_count']}")
        if not any([args.train, args.walk_forward, args.kelly, args.auto_retrain]):
            return 0

    if args.auto_retrain:
        result = retrain_if_stale(
            Path(args.results_dir) if args.results_dir else None,
            threshold=args.retrain_threshold,
            include_journal=args.journal,
        )
        print(f"\nauto-retrain: {result['status']}")
        if result["status"] == "retrained":
            print(f"  retrained on {result['n_trades']} trades (was {result['previous_model_trades']})")
            print(f"  AUC: {result['cv_auc']:.4f}")
        else:
            print(f"  {result.get('reason', '')}")
        if not any([args.train, args.walk_forward, args.kelly]):
            return 0

    trades = load_result_trades(Path(args.results_dir))
    if args.journal:
        journal = load_journal_trades()
        trades.extend(journal)
        print(f"journal trades loaded: {len(journal)}")

    X, y, meta = build_dataset(trades)
    if len(y) < 10:
        print(f"not enough usable trades ({len(y)}); need >= 10", file=sys.stderr)
        return 2

    metrics = train_and_evaluate(X, y, meta=meta)
    print(f"trades: {len(trades)}  usable: {len(y)}")
    print(f"base win rate: {metrics['base_win_rate']:.3f}")
    print(f"accuracy:      {metrics['cv_accuracy']:.3f}")
    print(f"AUC:           {metrics['cv_auc']:.3f}")
    print(f"win rate (scored>=0.5): {metrics['cv_win_rate_above_0_5']:.3f}")

    if args.walk_forward:
        wf = walk_forward_validation(X, y, n_splits=args.n_splits, meta=meta)
        if "error" in wf:
            print(f"walk-forward: {wf['error']}", file=sys.stderr)
        else:
            print(f"\nwalk-forward validation ({wf['n_splits']} splits):")
            print(f"  test trades:  {wf['n_test_trades']}")
            print(f"  test accuracy: {wf['test_accuracy']:.3f}")
            print(f"  test AUC:      {wf['test_auc']:.3f}")
            print(f"  test win rate (>=0.5): {wf['test_win_rate_above_0_5']:.3f}")

    if args.kelly:
        kf = kelly_fraction(X, y)
        if "error" in kf:
            print(f"kelly: {kf['error']}", file=sys.stderr)
        else:
            full = kf.get("full_dataset", {})
            filt = kf.get("filtered_dataset", {})
            print(f"\nKelly criterion (half-Kelly, safety={kf['safety_factor']}):")
            if full:
                print(f"  all trades:  {full['n']} trades, WR={full['win_rate']:.3f}, kelly={full['kelly']:+.4f}")
            if filt:
                print(f"  ML-filtered: {filt['n']} trades, WR={filt['win_rate']:.3f}, kelly={filt['kelly']:+.4f}")
            print(f"  → using kelly={kf['kelly_fraction']:+.4f}, adjusted={kf['adjusted_kelly']:+.4f}")
            if kf['adjusted_kelly'] > 0:
                print(f"  → risk {kf['adjusted_kelly']*100:.1f}% of balance per trade")
            else:
                print(f"  → negative kelly: reduce risk or improve model")

    if args.train:
        if not metrics["validation_ok"] or metrics["cv_auc"] < 0.55:
            print("model not replaced: insufficient purged validation or AUC below 0.55", file=sys.stderr)
            return 2
        if len(set(y)) < 2:
            print("only one class in data; cannot train", file=sys.stderr)
            return 2
        payload = fit_and_save(X, y, MODEL_PATH, cv_auc=metrics["cv_auc"], validated=True)
        print(f"model saved: {MODEL_PATH}")
        for name, w in zip(FEATURES, payload["weights"], strict=False):
            print(f"  {name:24s} {w:+.4f}")
        print(f"  intercept {payload['intercept']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
