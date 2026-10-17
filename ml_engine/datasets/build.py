"""Leakage-safe dataset assembly.

Joins per-bar feature frames to setups at the setup bar, then attaches
labels by setup_id. Guarantees:
- feature columns come ONLY from the feature frame (labels excluded),
- every feature row is the setup-trigger bar's own row (no future rows),
- dataset hash pins feature list + meta for reproducibility.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from ml_engine.setups.composer import Setup


def build_dataset(
    setups: list[Setup],
    feature_frame: pd.DataFrame,
    labels: pd.DataFrame,
    feature_version: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (dataset, meta).

    dataset: one row per setup; first columns meta keys, then features.
    meta: setup_id, symbol, time, direction, entry, stop, target, index.
    """
    if not setups:
        return pd.DataFrame(), pd.DataFrame()

    meta_rows = []
    for s in setups:
        meta_rows.append(
            {
                "setup_id": s.setup_id,
                "symbol": s.symbol,
                "time": s.time,
                "index": s.index,
                "direction": s.direction,
                "entry": s.entry,
                "stop": s.stop,
                "target": s.target,
                "risk_reward": s.risk_reward,
            }
        )
    meta = pd.DataFrame(meta_rows)

    # Feature row = the setup bar's own feature row (trailing info only)
    ff = feature_frame.reset_index(drop=True)
    idx = meta["index"].to_numpy()
    taken = ff.iloc[idx].reset_index(drop=True)
    for col in ("symbol",):
        if col in taken.columns:
            taken[col] = meta[col].to_numpy()
    tf_names = [s.timeframes[0] if getattr(s, "timeframes", None) else "" for s in setups]
    if "timeframe" in taken.columns:
        taken["timeframe"] = tf_names
    dataset = pd.concat([meta.drop(columns=["index"]), taken], axis=1)
    # Guard against duplicate columns from meta/feature overlap (e.g. symbol)
    dataset = dataset.loc[:, ~dataset.columns.duplicated()]

    # Labels joined strictly by setup_id (quarantine boundary)
    if labels is not None and len(labels):
        lab = labels.reset_index().rename(columns={labels.index.name or "setup_id": "setup_id"})
        dataset = dataset.merge(lab, on="setup_id", how="left", validate="one_to_one")

    return dataset, meta


def feature_columns(dataset: pd.DataFrame, feature_version: str) -> list[str]:
    """The ML-usable feature list: everything except meta + label columns.

    Symbol one-hot columns (sym_*) are legitimate features — pooled models
    must be able to learn symbol heterogeneity (measured: per-symbol
    expectancy ranged +0.24R to -0.42R in the 2026-09 audit).
    """
    meta_cols = {
        "setup_id", "symbol", "time", "index", "direction", "entry", "stop",
        "target", "risk_reward", "timeframe",
    }
    label_cols = {
        "mfe_r", "mae_r", "time_to_tp_bars", "time_to_sl_bars", "tp_first",
        "sl_first", "neither", "win_1r", "win_2r", "win_3r", "win_4r",
        "r_multiple", "exit_bars", "max_hold_bars",
    }
    forbidden = meta_cols | label_cols
    cols = [c for c in dataset.columns if c not in forbidden]
    return cols


def add_symbol_onehot(dataset: pd.DataFrame) -> pd.DataFrame:
    """One-hot encode the symbol column into sym_<SYMBOL> features."""
    out = dataset.copy()
    for sym in sorted(out["symbol"].unique()):
        out[f"sym_{sym}"] = (out["symbol"] == sym).astype(float)
    return out


def dataset_hash(dataset: pd.DataFrame, feature_cols: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(f"rows={len(dataset)};".encode())
    digest.update((";".join(feature_cols) + ";").encode())
    if len(dataset):
        digest.update(
            dataset[feature_cols]
            .fillna(-9999)
            .round(8)
            .to_numpy()
            .tobytes()
        )
    return digest.hexdigest()[:16]


def xy(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    target: str = "win_2r",
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Feature matrix + binary target for ML. Drops label-incomplete rows."""
    need = [c for c in feature_cols if c in dataset.columns]
    work = dataset.dropna(subset=[target]).copy()
    X = work[need].to_numpy(dtype=float)
    y = work[target].astype(float).to_numpy()
    meta = work[["setup_id", "symbol", "time", "direction", "entry", "stop", "target"]]
    return X, y, meta


def assert_no_label_leak(feature_cols: list[str]) -> None:
    """Guard: no label-ish column may enter the feature matrix."""
    label_cols = {
        "mfe_r", "mae_r", "time_to_tp_bars", "time_to_sl_bars", "tp_first",
        "sl_first", "neither", "win_1r", "win_2r", "win_3r", "win_4r",
        "r_multiple", "exit_bars", "max_hold_bars",
    }
    bad = label_cols & set(feature_cols)
    if bad:
        raise AssertionError(f"label columns leaked into features: {bad}")
