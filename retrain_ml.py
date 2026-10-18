"""Retrain ML ranker with sklearn on all available data."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from pathlib import Path
from mt5_mcp.ml_ranker import (
    load_result_trades, load_journal_trades, build_dataset,
    MODEL_PATH, FEATURES,
)
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict, StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report

# Load all data
bt_trades = load_result_trades(Path("E:/Project/Mt5 Ea/mt5_mcp/backtest/results"))
jrn_trades = load_journal_trades()
all_trades = bt_trades + jrn_trades

# Build dataset
X, y, meta = build_dataset(all_trades)
print(f"Total: {len(all_trades)}, Usable: {len(y)}")
print(f"Win rate: {y.mean():.3f} ({int(y.sum())} wins / {len(y)} total)")

# Show feature distributions
for i, name in enumerate(FEATURES):
    vals = X[:, i]
    print(f"  {name:24s}  mean={np.mean(vals):7.2f}  std={np.std(vals):7.2f}  min={np.min(vals):7.2f}  max={np.max(vals):7.2f}")

# Standardize
mean = X.mean(axis=0)
std = X.std(axis=0)
std[std < 1e-12] = 1.0
Xs = (X - mean) / std

# Train with sklearn
print("\n--- sklearn LogisticRegression ---")
model = LogisticRegression(C=1.0, max_iter=3000, random_state=42)
model.fit(Xs, y)

# Cross-validation
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
proba = cross_val_predict(model, Xs, y, cv=cv, method="predict_proba")[:, 1]
pred = cross_val_predict(model, Xs, y, cv=cv)

accuracy = accuracy_score(y, pred)
auc = roc_auc_score(y, proba) if len(set(y)) > 1 else 0.5
win_above = y[proba >= 0.5].mean() if (proba >= 0.5).any() else 0.0

print(f"CV accuracy:     {accuracy:.3f}")
print(f"CV AUC:          {auc:.3f}")
print(f"Win rate (>=0.5): {win_above:.3f}")

print("\nWeights:")
for name, w in zip(FEATURES, model.coef_[0]):
    print(f"  {name:24s} {w:+.4f}")
print(f"  intercept {model.intercept_[0]:+.4f}")

# Save model
payload = {
    "features": FEATURES,
    "weights": [round(float(w), 6) for w in model.coef_[0]],
    "intercept": round(float(model.intercept_[0]), 6),
    "scaler": {"mean": mean.tolist(), "std": std.tolist()},
    "trained_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    "n_trades": int(len(y)),
    "cv_auc": round(float(auc), 4),
    "cv_accuracy": round(float(accuracy), 4),
}
MODEL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(f"\nModel saved: {MODEL_PATH}")

# Test on AUDCAD-like features
test = np.array([[88, 85, 0.74, 3.0, 20.0, 7.0]])
test_s = (test - mean) / std
score = model.predict_proba(test_s)[0][1]
print(f"\nAUDCAD test (conf=88, zone=85, conf=0.74, rr=3, sl=20, hr=7): {score:.3f}")
