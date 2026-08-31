"""Multi-timeframe feature alignment and merging.

Loads higher-timeframe features (H4, D1) and aligns them to the base
timeframe (H1) by forward-filling. This gives the RL agent context
about the broader market structure without lookahead bias.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector

logger = logging.getLogger(__name__)

# Features to extract from higher timeframes (reduced set for MTF)
_MTF_FEATURE_NAMES = [
    # Trend context
    "ema_20", "ema_50", "ema_100", "ema_200",
    "ema_slope_20", "distance_from_ema_50",
    # Momentum context
    "rsi_14", "macd_histogram",
    # Volatility context
    "atr_14", "volatility_percentile",
]


def merge_multitimeframe_features(
    base_df: pd.DataFrame,
    htf_dfs: dict[str, pd.DataFrame],
    feature_engine: Optional[FeatureEngine] = None,
) -> pd.DataFrame:
    """Merge higher-timeframe features into base timeframe DataFrame.

    Parameters
    ----------
    base_df : pd.DataFrame
        Base timeframe data (e.g., H1) with OHLCV + already-computed features.
    htf_dfs : dict[str, pd.DataFrame]
        Higher timeframe data keyed by timeframe name, e.g.
        {"H4": h4_df, "D1": d1_df}. Must have same symbol, DatetimeIndex.
    feature_engine : FeatureEngine, optional
        Reuse an existing engine to avoid recomputing base features.

    Returns
    -------
    pd.DataFrame
        base_df with additional columns like ``h4_ema_20``, ``d1_rsi_14``, etc.
    """
    result = base_df.copy()

    for tf_name, htf_df in htf_dfs.items():
        logger.info("Merging %s features into base timeframe", tf_name)

        # Compute features on the higher timeframe
        engine = feature_engine or FeatureEngine()
        htf_features = engine.compute_all(htf_df)

        # Add structure features
        ms = MarketStructure()
        struct = ms.compute_structure_features(htf_features)
        for col in struct.columns:
            htf_features[col] = struct[col].values

        # Add regime features
        rd = RegimeDetector()
        regime_feats = rd.compute_regime_features(htf_features)
        for col in regime_feats.columns:
            htf_features[col] = regime_feats[col].values

        # Select only the features we want
        available = [c for c in htf_features.columns if c not in
                     ("time", "open", "high", "low", "close", "volume")]
        htf_subset = htf_features[available].copy()

        # Align to base timeframe by forward-filling
        # htf_subset has its own DatetimeIndex, we align to base_df's index
        htf_subset.index = htf_subset.index.tz_localize(None) if htf_subset.index.tz else htf_subset.index
        base_idx = result.index if isinstance(result.index, pd.DatetimeIndex) else pd.RangeIndex(len(result))

        # Reindex to base timeframe and forward-fill
        htf_aligned = htf_subset.reindex(base_idx, method="ffill")

        # Prefix columns with timeframe
        prefix = tf_name.lower()
        htf_aligned.columns = [f"{prefix}_{col}" for col in htf_aligned.columns]

        # Merge
        result = pd.concat([result, htf_aligned], axis=1)

        logger.info("  Added %d %s features", len(htf_aligned.columns), tf_name)

    # Forward-fill any remaining NaN from alignment
    new_cols = [c for c in result.columns if c not in base_df.columns]
    result[new_cols] = result[new_cols].ffill().fillna(0)

    return result


def prepare_mtf_features(
    symbol: str,
    base_tf: str = "H1",
    htf_list: Optional[list[str]] = None,
    data_dir: str = "forex_rl/data/raw",
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Load base + HTF data, compute and merge all features.

    Returns
    -------
    tuple[pd.DataFrame, np.ndarray, list[str]]
        (merged_df, feature_array, feature_names)
    """
    if htf_list is None:
        htf_list = ["H4"]

    data_path = Path(data_dir)

    # Load base timeframe
    base_file = data_path / f"{symbol}_{base_tf}.parquet"
    if not base_file.exists():
        raise FileNotFoundError(f"Base data not found: {base_file}")
    base_df = pd.read_parquet(base_file)
    logger.info("Loaded base %s %s: %d bars", symbol, base_tf, len(base_df))

    # Compute base features
    engine = FeatureEngine()
    base_df = engine.compute_all(base_df)

    # Add structure features
    ms = MarketStructure()
    struct = ms.compute_structure_features(base_df)
    for col in struct.columns:
        base_df[col] = struct[col].values

    # Add supply/demand features
    sd = SupplyDemandDetector()
    sd_feats = sd.compute_sd_features(base_df)
    for col in sd_feats.columns:
        base_df[col] = sd_feats[col].values

    # Add regime features
    rd = RegimeDetector()
    regime_feats = rd.compute_regime_features(base_df)
    for col in regime_feats.columns:
        base_df[col] = regime_feats[col].values

    # Load and merge HTF features
    htf_dfs = {}
    for htf in htf_list:
        htf_file = data_path / f"{symbol}_{htf}.parquet"
        if htf_file.exists():
            htf_df = pd.read_parquet(htf_file)
            htf_dfs[htf] = htf_df
            logger.info("Loaded HTF %s %s: %d bars", symbol, htf, len(htf_df))
        else:
            logger.warning("HTF data not found: %s", htf_file)

    if htf_dfs:
        base_df = merge_multitimeframe_features(base_df, htf_dfs, engine)

    # Build feature array
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in base_df.columns if c not in exclude]
    feature_array = base_df[feature_cols].values.astype(np.float32)

    # Handle NaN
    nan_mask = np.isnan(feature_array)
    if nan_mask.any():
        nan_pct = nan_mask.mean()
        logger.warning("NaN in features: %.1f%% — filling with 0", nan_pct * 100)
        feature_array = np.nan_to_num(feature_array, nan=0.0)

    logger.info("Final feature matrix: %d bars x %d features", *feature_array.shape)

    return base_df, feature_array, feature_cols
