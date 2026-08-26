"""Model registry: versioned model storage with full provenance metadata.

Layout:
    models/<SYMBOL>/<TIMEFRAME>/<TASK>/v<N>/model.joblib|model.pt, metadata.json, metrics.json
    models/registry.json  (append-only index)
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Config, PROJECT_ROOT

TASKS = ("setup_classifier", "direction", "outcome_probability")


def _models_root(cfg: Config) -> Path:
    return Path(cfg.get("models.root", str(PROJECT_ROOT / "models")))


def task_dir(cfg: Config, symbol: str, timeframe: str, task: str) -> Path:
    if task not in TASKS:
        raise ValueError(f"task must be one of {TASKS}")
    return _models_root(cfg) / symbol.upper() / timeframe.upper() / task


def next_version(cfg: Config, symbol: str, timeframe: str, task: str) -> str:
    base = task_dir(cfg, symbol, timeframe, task)
    if not base.exists():
        return "v001"
    existing = [int(d.name[1:]) for d in base.iterdir() if d.is_dir() and d.name.startswith("v")]
    return f"v{max(existing, default=0) + 1:03d}"


def save_model(
    cfg: Config,
    symbol: str,
    timeframe: str,
    task: str,
    model_obj: Any,
    metadata: dict,
    metrics: dict,
) -> str:
    """Persist a trained model + provenance. Returns the version string."""
    version = metadata.get("version") or next_version(cfg, symbol, timeframe, task)
    base = task_dir(cfg, symbol, timeframe, task) / version
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    model_path = base / "model.joblib"
    import joblib
    joblib.dump(model_obj, model_path)

    meta = {
        "model_name": f"{symbol}_{timeframe}_{task}",
        "version": version,
        "symbol": symbol.upper(),
        "timeframe": timeframe.upper(),
        "task": task,
        "algorithm": metadata.get("algorithm"),
        "feature_version": metadata.get("feature_version"),
        "dataset_version": metadata.get("dataset_version"),
        "training_period": metadata.get("training_period"),
        "validation_period": metadata.get("validation_period"),
        "test_period": metadata.get("test_period"),
        "hyperparameters": metadata.get("hyperparameters", {}),
        "feature_columns": metadata.get("feature_columns", []),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "created_by": "local",
    }
    (base / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (base / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    registry_path = _models_root(cfg) / "registry.json"
    entries = []
    if registry_path.exists():
        entries = json.loads(registry_path.read_text(encoding="utf-8"))
    entries.append({"task": task, "symbol": meta["symbol"], "timeframe": meta["timeframe"], "version": version})
    registry_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return version


def load_model(cfg: Config, symbol: str, timeframe: str, task: str, version: str | None = None):
    import joblib

    base = task_dir(cfg, symbol, timeframe, task)
    if version is None:
        versions = [d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("v")]
        if not versions:
            raise FileNotFoundError(f"no saved models for {symbol}/{timeframe}/{task}")
        version = sorted(versions, key=lambda v: int(v[1:]))[-1]
    model_path = base / version / "model.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"model not found: {model_path}")
    return joblib.load(model_path), version


def load_metadata(cfg: Config, symbol: str, timeframe: str, task: str, version: str | None = None) -> dict:
    base = task_dir(cfg, symbol, timeframe, task)
    if version is None:
        versions = [d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("v")]
        if not versions:
            raise FileNotFoundError(f"no saved models for {symbol}/{timeframe}/{task}")
        version = sorted(versions, key=lambda v: int(v[1:]))[-1]
    meta_path = base / version / "metadata.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def list_models(cfg: Config) -> list[dict]:
    registry_path = _models_root(cfg) / "registry.json"
    if not registry_path.exists():
        return []
    return json.loads(registry_path.read_text(encoding="utf-8"))
