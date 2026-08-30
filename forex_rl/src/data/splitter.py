"""Chronological train / val / test splitting with walk-forward support."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataSplitter:
    """Split time-series data chronologically – never shuffle."""

    def split(
        self,
        df: pd.DataFrame,
        train_dates: dict[str, str],
        val_dates: dict[str, str],
        test_dates: dict[str, str],
    ) -> dict[str, pd.DataFrame]:
        """Split into train / val / test by explicit date ranges.

        Parameters
        ----------
        df : DataFrame with DatetimeIndex
        train_dates / val_dates / test_dates : dicts with ``"start"`` and ``"end"`` keys
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")
        start = df.index.min()
        end = df.index.max()

        train_start = pd.Timestamp(train_dates["start"], tz="UTC").tz_localize(None)
        train_end = pd.Timestamp(train_dates["end"], tz="UTC").tz_localize(None)
        val_start = pd.Timestamp(val_dates["start"], tz="UTC").tz_localize(None)
        val_end = pd.Timestamp(val_dates["end"], tz="UTC").tz_localize(None)
        test_start = pd.Timestamp(test_dates["start"], tz="UTC").tz_localize(None)
        test_end = pd.Timestamp(test_dates["end"], tz="UTC").tz_localize(None)

        train_df = df.loc[train_start:train_end].copy()
        val_df = df.loc[val_start:val_end].copy()
        test_df = df.loc[test_start:test_end].copy()

        self._verify_no_leakage(train_df, val_df, test_df)

        logger.info(
            "Split: train=%d  val=%d  test=%d  (requested %s–%s / %s–%s / %s–%s)",
            len(train_df), len(val_df), len(test_df),
            train_start.date(), train_end.date(),
            val_start.date(), val_end.date(),
            test_start.date(), test_end.date(),
        )

        return {"train": train_df, "val": val_df, "test": test_df}

    def walk_forward_splits(
        self,
        df: pd.DataFrame,
        train_window: int,
        test_window: int,
        step: int,
    ) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        """Generate walk-forward (rolling) splits.

        Parameters
        ----------
        df : DataFrame with DatetimeIndex
        train_window : number of bars for training
        test_window : number of bars for testing
        step : number of bars to advance each fold
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")
        n = len(df)
        if train_window + test_window > n:
            raise ValueError(
                f"train_window ({train_window}) + test_window ({test_window}) > "
                f"total rows ({n})"
            )

        splits: list[tuple[pd.DataFrame, pd.DataFrame]] = []
        start = 0
        fold = 0
        while start + train_window + test_window <= n:
            train_end = start + train_window
            test_end = train_end + test_window
            train_df = df.iloc[start:train_end].copy()
            test_df = df.iloc[train_end:test_end].copy()

            # Verify no leakage: last train timestamp < first test timestamp
            assert train_df.index[-1] < test_df.index[0], (
                f"Data leakage in fold {fold}: "
                f"train ends {train_df.index[-1]} >= test starts {test_df.index[0]}"
            )

            splits.append((train_df, test_df))
            logger.debug(
                "Fold %d: train [%s – %s] (%d) → test [%s – %s] (%d)",
                fold,
                train_df.index[0], train_df.index[-1], len(train_df),
                test_df.index[0], test_df.index[-1], len(test_df),
            )
            fold += 1
            start += step

        logger.info("Generated %d walk-forward split(s)", len(splits))
        return splits

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_no_leakage(
        train: pd.DataFrame,
        val: pd.DataFrame,
        test: pd.DataFrame,
    ) -> None:
        """Assert strict chronological ordering with no overlap."""
        pairs = [
            ("train", "val", train, val),
            ("val", "test", val, test),
            ("train", "test", train, test),
        ]
        for name_a, name_b, df_a, df_b in pairs:
            if len(df_a) == 0 or len(df_b) == 0:
                continue
            last_a = df_a.index[-1]
            first_b = df_b.index[0]
            if last_a >= first_b:
                raise ValueError(
                    f"Data leakage: {name_a} ends at {last_a} but "
                    f"{name_b} starts at {first_b}"
                )
