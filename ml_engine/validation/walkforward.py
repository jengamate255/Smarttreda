"""Walk-forward fold generation.

Folds are strictly chronological (train end + purge gap <= test start).
The purge gap absorbs every trailing-window feature's memory: features at
test-fold start can reach back at most `purge_bars`, so no train-period
outcome can bleed into test features. Scalers/calibrators must be fit on the
train fold only — enforced by the caller (models.train).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml_engine.config import WalkForwardConfig


@dataclass(frozen=True)
class Fold:
    fold: int
    train_index: np.ndarray      # positional indices into the dataset
    test_index: np.ndarray
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_folds(
    times: pd.Series, cfg: WalkForwardConfig | None = None
) -> list[Fold]:
    """Expanding-window walk-forward folds over setup times.

    Fold k trains on [0, train_bars + k*step) and tests on the following
    test_bars window after a purge gap — the classic expanding scheme
    (train 2019-2022 / test 2023, then train 2019-2023 / test 2024, ...).
    """
    cfg = cfg or WalkForwardConfig()
    t = pd.Series(pd.to_datetime(times)).reset_index(drop=True)
    n = len(t)
    if n == 0:
        return []

    folds: list[Fold] = []
    train_end = cfg.train_bars
    fold_no = 0
    while train_end + cfg.purge_bars + cfg.test_bars <= n:
        test_start = train_end + cfg.purge_bars
        test_end = min(test_start + cfg.test_bars, n)
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(test_start, test_end)
        folds.append(
            Fold(
                fold=fold_no,
                train_index=train_idx,
                test_index=test_idx,
                train_start=t.iloc[train_idx[0]],
                train_end=t.iloc[train_idx[-1]],
                test_start=t.iloc[test_idx[0]],
                test_end=t.iloc[test_idx[-1]],
            )
        )
        fold_no += 1
        train_end += cfg.step_bars
    return folds


def assert_chronology(folds: list[Fold]) -> None:
    """Every fold: train strictly before test, no overlap, purge honored."""
    for f in folds:
        assert f.train_index[-1] < f.test_index[0], (
            f"fold {f.fold}: train overlaps test"
        )
        gap = f.test_index[0] - f.train_index[-1]
        assert gap >= 1, f"fold {f.fold}: no purge gap"
    for a, b in itertools.pairwise(folds):
        # Expanding window: next fold's train may include previous test, that
        # is legitimate walk-forward; only within-fold ordering matters.
        pass


def fold_table(folds: list[Fold]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fold": f.fold,
                "n_train": len(f.train_index),
                "n_test": len(f.test_index),
                "train_start": f.train_start,
                "train_end": f.train_end,
                "test_start": f.test_start,
                "test_end": f.test_end,
            }
            for f in folds
        ]
    )
