"""SQLite experiment registry (contract C8). Append-only, hash-anchored.

Every experiment records dataset/feature/model versions, parameters,
periods, metrics, git commit and notes. Nothing is ever overwritten.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ml_engine.config import EXPERIMENTS_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    dataset_hash TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    label_version TEXT NOT NULL,
    model_type TEXT NOT NULL,
    model_version TEXT NOT NULL,
    target TEXT NOT NULL,
    params_json TEXT NOT NULL,
    train_period_json TEXT NOT NULL,
    test_period_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    git_commit TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS folds (
    experiment_id TEXT NOT NULL,
    fold INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY (experiment_id, fold),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);
"""


@contextmanager
def _conn(db_path: Path | None = None):
    path = Path(db_path) if db_path else EXPERIMENTS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_experiment(
    experiment_id: str,
    dataset_hash: str,
    dataset_version: str,
    feature_version: str,
    label_version: str,
    model_type: str,
    target: str,
    params: dict,
    train_period: tuple | list,
    test_period: tuple | list,
    metrics: dict,
    git_commit: str = "",
    notes: str = "",
    model_version: str = "mv1",
    db_path: Path | None = None,
) -> str:
    created = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                experiment_id, created, dataset_hash, dataset_version,
                feature_version, label_version, model_type, model_version,
                target,
                json.dumps(params, default=str),
                json.dumps(train_period, default=str),
                json.dumps(test_period, default=str),
                json.dumps(metrics, default=str),
                git_commit, notes,
            ),
        )
    return experiment_id


def record_fold_metrics(
    experiment_id: str, fold: int, metrics: dict, db_path: Path | None = None
) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO folds VALUES (?,?,?)",
            (experiment_id, int(fold), json.dumps(metrics, default=str)),
        )


def list_experiments(db_path: Path | None = None) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT experiment_id, created_at, model_type, target, "
            "train_period_json, test_period_json, metrics_json, notes "
            "FROM experiments ORDER BY created_at"
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "experiment_id": r[0],
                "created_at": r[1],
                "model_type": r[2],
                "target": r[3],
                "train_period": json.loads(r[4]),
                "test_period": json.loads(r[5]),
                "metrics": json.loads(r[6]),
                "notes": r[7],
            }
        )
    return out


def get_experiment(experiment_id: str, db_path: Path | None = None) -> dict | None:
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.description]
    return dict(zip(cols, row))
