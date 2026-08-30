"""Clean, validate and fill gaps in OHLCV DataFrames."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DataCleaner:
    """Validates and repairs OHLCV data."""

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Full cleaning pipeline: dedup → missing → outliers → OHLC validation."""
        df = df.copy()
        df = self._remove_duplicates(df)
        df = self._handle_missing(df)
        df = self._remove_outliers(df)
        df = self._validate_ohlc(df)
        logger.info("Cleaned DataFrame: %d rows", len(df))
        return df

    def detect_gaps(self, df: pd.DataFrame, expected_freq: str) -> list[pd.Timestamp]:
        """Return a list of timestamps where data is missing.

        Parameters
        ----------
        df : DataFrame with DatetimeIndex
        expected_freq : pandas offset alias, e.g. ``"15min"``, ``"1h"``, ``"4h"``
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")
        expected = pd.date_range(start=df.index.min(), end=df.index.max(), freq=expected_freq)
        actual = df.index
        missing = expected.difference(actual)
        gaps = list(missing)
        if gaps:
            logger.info("Detected %d gap(s) with freq=%s", len(gaps), expected_freq)
        return gaps

    def fill_gaps(self, df: pd.DataFrame, expected_freq: str) -> pd.DataFrame:
        """Forward-fill gaps and inject small noise to avoid stale-price artefacts."""
        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")
        expected = pd.date_range(start=df.index.min(), end=df.index.max(), freq=expected_freq)
        df = df.reindex(expected)
        n_filled = df.isna().any(axis=1).sum()
        df = df.ffill()
        # Add tiny noise to filled close prices to break flat-line artefacts
        rng = np.random.default_rng(42)
        close_col = df["close"]
        mask = close_col.duplicated(keep="first")
        noise = rng.normal(0.0, 1e-6, size=mask.sum())
        close_col.loc[mask] = close_col.loc[mask] + noise
        # Re-enforce OHLC consistency after noise
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        if n_filled:
            logger.info("Filled %d gap(s) with freq=%s", n_filled, expected_freq)
        return df

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
        before = len(df)
        df = df[~df.index.duplicated(keep="first")]
        dupes = before - len(df)
        if dupes:
            logger.warning("Removed %d duplicate timestamp(s)", dupes)
        return df

    @staticmethod
    def _handle_missing(df: pd.DataFrame) -> pd.DataFrame:
        na_count = int(df.isna().sum().sum())
        if na_count:
            logger.warning("Forward-filling %d missing value(s)", na_count)
        df = df.ffill()
        # If leading NaNs remain (first row), back-fill them
        df = df.bfill()
        return df

    @staticmethod
    def _remove_outliers(df: pd.DataFrame, n_std: float = 5.0) -> pd.DataFrame:
        """Flag rows where any price column deviates > n_std from its rolling mean."""
        before = len(df)
        price_cols = ["open", "high", "low", "close"]
        mask = pd.Series(False, index=df.index)
        for col in price_cols:
            if col not in df.columns:
                continue
            rolling_mean = df[col].rolling(window=100, min_periods=20, center=True).mean()
            rolling_std = df[col].rolling(window=100, min_periods=20, center=True).std()
            z = (df[col] - rolling_mean).abs() / (rolling_std + 1e-12)
            mask = mask | (z > n_std)
        outliers = mask.sum()
        if outliers:
            logger.warning("Removing %d outlier row(s) (> %d std)", outliers, int(n_std))
        df = df[~mask].copy()
        return df

    @staticmethod
    def _validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure high >= max(open,close) and low <= min(open,close)."""
        if not all(c in df.columns for c in ("open", "high", "low", "close")):
            return df
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        bad = ((df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["low"] > df["open"]))
        if bad.any():
            logger.warning("Corrected %d OHLC consistency violation(s)", int(bad.sum()))
        return df
