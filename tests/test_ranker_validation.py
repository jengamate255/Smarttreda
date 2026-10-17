"""Promotion uses unseen, completed trades; training scores cannot justify deployment."""
from datetime import datetime, timedelta, timezone
import json

import numpy as np

from mt5_mcp import ml_ranker as ranker


def sample(i):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i)
    return {"symbol": "EURUSD", "side": "BUY", "entry": 1.1 + i / 10000,
            "entry_time": start.isoformat(), "exit_time": (start + timedelta(minutes=30)).isoformat(),
            "pnl": 10 if i % 2 else -10}


def test_dataset_orders_deduplicates_and_ignores_unknown_outcomes():
    rows = [sample(2), sample(0), sample(1), sample(1), {**sample(3), "pnl": None}]
    X, y, meta = ranker.build_dataset(rows)
    assert len(y) == 3
    assert [row["entry_time"] for row in meta] == [sample(i)["entry_time"] for i in range(3)]


def test_purging_removes_outcomes_unfinished_at_test_entry(monkeypatch):
    meta = [sample(i) for i in range(120)]
    for i in range(0, 120, 7):
        meta[i]["exit_time"] = "2027-01-01T00:00:00+00:00"
    X = np.arange(120, dtype=float).reshape(-1, 1)
    y = np.arange(120) % 2
    seen = []

    class Model:
        def __init__(self, **_):
            pass

        def fit(self, X, y):
            seen.append(X[:, 0].copy())

        def predict_proba(self, X):
            return np.full(len(X), 0.5)

    monkeypatch.setattr(ranker, "_standardize", lambda X, stats=None: (X, {}))
    monkeypatch.setattr(ranker, "_LogisticRegression", Model)
    verdict = ranker.walk_forward_validation(X, y, meta=meta)
    assert seen and all(not any(int(i) % 7 == 0 for i in indices) for indices in seen)
    assert verdict["test_auc"] == 0.5, "tied predictions have chance AUC"


def test_high_training_score_does_not_publish_candidate(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    before = json.dumps({"n_trades": 10, "cv_auc": 0.65, "validation_method": "purged_walk_forward"})
    path.write_text(before)
    monkeypatch.setattr(ranker, "MODEL_PATH", path)
    monkeypatch.setattr(ranker, "load_result_trades", lambda _: [sample(i) for i in range(80)])
    monkeypatch.setattr(ranker, "load_journal_trades", lambda: [])
    monkeypatch.setattr(ranker, "train_and_evaluate", lambda *a, **kw: {
        "training_auc": 0.99, "cv_auc": 0.49, "validation_ok": True, "validation": {"test_auc": 0.49}})
    verdict = ranker.retrain_with_new_data(include_journal=True, auc_tolerance=0.9)
    assert verdict["status"] == "rejected"
    assert path.read_text() == before


def test_validated_candidate_can_be_saved(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    monkeypatch.setattr(ranker, "MODEL_PATH", path)
    monkeypatch.setattr(ranker, "RETRAIN_STATE_PATH", tmp_path / "retrain.json")
    monkeypatch.setattr(ranker, "load_result_trades", lambda _: [sample(i) for i in range(80)])
    monkeypatch.setattr(ranker, "load_journal_trades", lambda: [])
    monkeypatch.setattr(ranker, "train_and_evaluate", lambda *a, **kw: {
        "cv_auc": 0.66, "validation_ok": True, "validation": {"test_auc": 0.66}})
    verdict = ranker.retrain_with_new_data(include_journal=True)
    assert verdict["status"] == "retrained"
    assert json.loads(path.read_text())["validation_method"] == "purged_walk_forward"


def test_inference_reloads_an_updated_model(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    monkeypatch.setattr(ranker, "MODEL_PATH", path)
    path.write_text(json.dumps({"version": 1}))
    assert ranker.load_model()["version"] == 1
    path.write_text(json.dumps({"version": 200}))
    assert ranker.load_model()["version"] == 200
