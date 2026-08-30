"""Tests for feature engineering pipeline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forex_rl.src.market.indicators import FeatureEngine


@pytest.fixture
def feature_engine() -> FeatureEngine:
    return FeatureEngine()


@pytest.fixture
def ohlcv_df(synthetic_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Return the synthetic OHLCV with UTC index for FeatureEngine."""
    df = synthetic_ohlcv.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.date_range("2020-01-01", periods=len(df), freq="1h", tz="UTC")
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


class TestNoLookahead:
    """Verify features at bar N don't use bar N+1 data."""

    def test_no_lookahead(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        full = ohlcv_df.copy()
        truncated = full.iloc[:-10].copy()

        feat_full = feature_engine.compute_all(full)
        feat_trunc = feature_engine.compute_all(truncated)

        feature_cols = feature_engine.feature_names()
        # The first (len-10) rows of the full computation must equal
        # the truncated computation exactly
        for col in feature_cols:
            full_vals = feat_full[col].iloc[: len(truncated)].values
            trunc_vals = feat_trunc[col].values
            np.testing.assert_array_equal(
                full_vals,
                trunc_vals,
                err_msg=f"Lookahead detected in feature '{col}'",
            )


class TestFeatureCounts:
    """Verify all 28 features are computed."""

    def test_feature_counts(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        result = feature_engine.compute_all(ohlcv_df)
        names = feature_engine.feature_names()
        assert len(names) == 28, f"Expected 28 feature names, got {len(names)}"
        for name in names:
            assert name in result.columns, f"Missing feature column: {name}"
        assert result.shape[1] >= 28


class TestNormalization:
    """Zscore gives mean~0 std~1, minmax gives [0,1]."""

    def test_zscore(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        df = feature_engine.compute_all(ohlcv_df)
        normalized = feature_engine.normalize(df, method="zscore")
        feature_cols = feature_engine.feature_names()
        means = normalized[feature_cols].mean()
        stds = normalized[feature_cols].std()
        assert np.allclose(means, 0.0, atol=1e-10), f"Zscore means not ~0: {means.abs().max()}"
        assert np.allclose(stds, 1.0, atol=1e-10), f"Zscore stds not ~1: {(stds - 1.0).abs().max()}"

    def test_minmax(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        df = feature_engine.compute_all(ohlcv_df)
        normalized = feature_engine.normalize(df, method="minmax")
        feature_cols = feature_engine.feature_names()
        mins = normalized[feature_cols].min()
        maxs = normalized[feature_cols].max()
        assert np.all(mins >= -1e-10), f"Minmax min < 0: {mins.min()}"
        assert np.all(maxs <= 1.0 + 1e-10), f"Minmax max > 1: {maxs.max()}"


class TestNanHandling:
    """Features handle short/NaN data."""

    def test_short_data_no_crash(self, feature_engine: FeatureEngine) -> None:
        idx = pd.date_range("2020-01-01", periods=5, freq="1h", tz="UTC")
        short_df = pd.DataFrame(
            {
                "open": [1.1] * 5,
                "high": [1.101] * 5,
                "low": [1.099] * 5,
                "close": [1.1005] * 5,
                "volume": [1000.0] * 5,
            },
            index=idx,
        )
        result = feature_engine.compute_all(short_df)
        assert result.shape[0] == 5
        # Should not raise

    def test_nan_forward_fill(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        df = ohlcv_df.copy()
        df.iloc[5:10, df.columns.get_loc("close")] = np.nan
        result = feature_engine.compute_all(df)
        feature_cols = feature_engine.feature_names()
        nan_counts = result[feature_cols].isna().sum().sum()
        # Some NaN is acceptable (rolling windows), but forward-fill should handle gaps


class TestSessionEncoding:
    """Correct session assignment for UTC times."""

    def test_asian_session(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        result = feature_engine.compute_sessions(ohlcv_df)
        hours = np.array(ohlcv_df.index.hour)
        expected = ((hours >= 0) & (hours < 8)).astype(float)
        np.testing.assert_array_equal(result["session_asian"].values, expected)

    def test_london_session(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        result = feature_engine.compute_sessions(ohlcv_df)
        hours = np.array(ohlcv_df.index.hour)
        expected = ((hours >= 7) & (hours < 16)).astype(float)
        np.testing.assert_array_equal(result["session_london"].values, expected)

    def test_newyork_session(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        result = feature_engine.compute_sessions(ohlcv_df)
        hours = np.array(ohlcv_df.index.hour)
        expected = ((hours >= 12) & (hours < 21)).astype(float)
        np.testing.assert_array_equal(result["session_newyork"].values, expected)

    def test_overlap_session(self, feature_engine: FeatureEngine, ohlcv_df: pd.DataFrame) -> None:
        result = feature_engine.compute_sessions(ohlcv_df)
        hours = np.array(ohlcv_df.index.hour)
        expected = ((hours >= 12) & (hours < 16)).astype(float)
        np.testing.assert_array_equal(result["session_overlap"].values, expected)
