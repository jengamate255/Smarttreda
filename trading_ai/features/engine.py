"""Feature Engine – computes tradable features from OHLCV data.

All computations are strictly causal: each bar uses only values from that bar
or earlier.  No look-ahead bias.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from trading_ai.market_state import classify_session, _atr

logger = logging.getLogger(__name__)

# ======================================================================
# Config constants
# ======================================================================

DEFAULT_ATR_PERIOD = 14
DEFAULT_BOLLINGER_PERIOD = 20
DEFAULT_BOLLINGER_STD = 2.0
DEFAULT_SESSION_CONFIG: dict[str, tuple[int, int]] | None = None  # uses market_state defaults


# ======================================================================
# Core feature functions (vectorised)
# ======================================================================

def compute_candle_body_ratio(df: pd.DataFrame) -> pd.Series:
    """(|close − open|) / (high − low).  Returns 0 when range is zero."""
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (body / rng).fillna(0.0)


def compute_wick_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Upper and lower wick ratios relative to total range.

    Columns returned: ``upper_wick_ratio``, ``lower_wick_ratio``.
    """
    body_top = df[["open", "close"]].max(axis=1)
    body_bot = df[["open", "close"]].min(axis=1)
    rng = (df["high"] - df["low"]).replace(0, np.nan)

    upper_wick = ((df["high"] - body_top) / rng).fillna(0.0)
    lower_wick = ((body_bot - df["low"]) / rng).fillna(0.0)

    return pd.DataFrame(
        {"upper_wick_ratio": upper_wick, "lower_wick_ratio": lower_wick},
        index=df.index,
    )


def compute_atr(df: pd.DataFrame, period: int = DEFAULT_ATR_PERIOD) -> pd.Series:
    """Average True Range with configurable period."""
    return _atr(df, period)


def compute_atr_ratio(df: pd.DataFrame, period: int = DEFAULT_ATR_PERIOD) -> pd.Series:
    """Current ATR / rolling mean ATR – a relative volatility gauge."""
    atr = _atr(df, period)
    atr_mean = atr.rolling(window=period * 2, min_periods=1).mean()
    return (atr / atr_mean.replace(0, np.nan)).fillna(1.0)


def compute_bollinger_bandwidth(
    df: pd.DataFrame,
    period: int = DEFAULT_BOLLINGER_PERIOD,
    num_std: float = DEFAULT_BOLLINGER_STD,
) -> pd.Series:
    """Bollinger bandwidth = (upper − lower) / middle.  No RSI."""
    mid = df["close"].rolling(window=period, min_periods=1).mean()
    std = df["close"].rolling(window=period, min_periods=1).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    bw = ((upper - lower) / mid.replace(0, np.nan)).fillna(0.0)
    return bw


def compute_session_range_features(
    df: pd.DataFrame, session_config: dict[str, tuple[int, int]] | None = None
) -> pd.DataFrame:
    """Per-bar session metadata and rolling session statistics.

    Columns:
        session            – current session label
        session_hour       – hour within the session (0-based)
        bar_range           – high − low for this bar
        session_avg_range   – rolling average range for the session type
    """
    rng = df["high"] - df["low"]

    sessions = pd.Series(
        [classify_session(ts, session_config) for ts in df.index], index=df.index
    )

    # Causal rolling average range per session type: only past bars of the
    # same session (last 50). Built via list append (O(n)) then one Series.
    from collections import deque

    avg_vals: list[float] = []
    _accum: dict[str, deque[float]] = {}
    for sess, r in zip(sessions, rng):
        dq = _accum.setdefault(sess, deque(maxlen=50))
        dq.append(float(r))
        avg_vals.append(float(sum(dq) / len(dq)))
    avg_range = pd.Series(avg_vals, index=df.index, dtype=float)

    return pd.DataFrame(
        {
            "session": sessions,
            "bar_range": rng,
            "session_avg_range": avg_range,
        },
        index=df.index,
    )


def compute_structure_features(
    df: pd.DataFrame,
    sr_levels: list[tuple[float, float]] | None = None,
    zones: list[tuple[float, float]] | None = None,
) -> pd.DataFrame:
    """Distance-based structure features.

    Parameters
    ----------
    sr_levels : list of (lower, upper) tuples for S/R zones.
    zones     : list of (lower, upper) tuples for supply/demand zones.

    Columns:
        dist_to_sr         – signed distance to nearest S/R level
        in_sr_zone         – 1 if price is inside any S/R zone
        dist_to_zone       – signed distance to nearest supply/demand zone
        in_supply_demand   – 1 if price is inside any supply/demand zone
    """
    close = df["close"].values
    n = len(df)

    dist_sr = np.full(n, np.nan)
    in_sr = np.zeros(n, dtype=int)
    dist_z = np.full(n, np.nan)
    in_z = np.zeros(n, dtype=int)

    sr_arr = np.array(sr_levels) if sr_levels else np.empty((0, 2))
    z_arr = np.array(zones) if zones else np.empty((0, 2))

    for i in range(n):
        c = close[i]

        # S/R
        if sr_arr.size:
            mids = (sr_arr[:, 0] + sr_arr[:, 1]) / 2
            dists = np.abs(c - mids)
            nearest = int(np.argmin(dists))
            dist_sr[i] = c - mids[nearest]
            in_sr[i] = int(sr_arr[nearest, 0] <= c <= sr_arr[nearest, 1])

        # Supply/demand
        if z_arr.size:
            mids = (z_arr[:, 0] + z_arr[:, 1]) / 2
            dists = np.abs(c - mids)
            nearest = int(np.argmin(dists))
            dist_z[i] = c - mids[nearest]
            in_z[i] = int(z_arr[nearest, 0] <= c <= z_arr[nearest, 1])

    return pd.DataFrame(
        {
            "dist_to_sr": dist_sr,
            "in_sr_zone": in_sr,
            "dist_to_zone": dist_z,
            "in_supply_demand": in_z,
        },
        index=df.index,
    )


def compute_momentum_features(df: pd.DataFrame, periods: list[int] | None = None) -> pd.DataFrame:
    """Rate-of-change momentum at multiple look-backs (no RSI)."""
    if periods is None:
        periods = [5, 10, 20]

    cols: dict[str, pd.Series] = {}
    for p in periods:
        cols[f"roc_{p}"] = df["close"].pct_change(periods=p)

    return pd.DataFrame(cols, index=df.index)


# ======================================================================
# FeatureEngine
# ======================================================================


class FeatureEngine:
    """Computes a full feature set from OHLCV data.

    Usage::

        engine = FeatureEngine({"atr_period": 14, "bollinger_period": 20})
        features = engine.compute(df)
        # features is a DataFrame indexed like df
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.atr_period: int = cfg.get("atr_period", DEFAULT_ATR_PERIOD)
        self.bollinger_period: int = cfg.get("bollinger_period", DEFAULT_BOLLINGER_PERIOD)
        self.bollinger_std: float = cfg.get("bollinger_std", DEFAULT_BOLLINGER_STD)
        self.session_config: dict[str, tuple[int, int]] | None = cfg.get(
            "session_config", DEFAULT_SESSION_CONFIG
        )
        self.momentum_periods: list[int] = cfg.get("momentum_periods", [5, 10, 20])
        self.sr_levels: list[tuple[float, float]] | None = cfg.get("sr_levels")
        self.zones: list[tuple[float, float]] | None = cfg.get("zones")
        logger.info(
            "FeatureEngine initialised (atr=%d, bb=%d/%.1f)",
            self.atr_period,
            self.bollinger_period,
            self.bollinger_std,
        )

    # ------------------------------------------------------------------

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all features and return a DataFrame aligned to *df*."""
        parts: list[pd.DataFrame] = []

        # 1. Candle shape
        body = compute_candle_body_ratio(df).to_frame("body_ratio")
        wicks = compute_wick_ratios(df)
        parts.extend([body, wicks])

        # 2. Volatility
        atr = compute_atr(df, self.atr_period).to_frame("atr")
        atr_ratio = compute_atr_ratio(df, self.atr_period).to_frame("atr_ratio")
        bb_bw = compute_bollinger_bandwidth(
            df, self.bollinger_period, self.bollinger_std
        ).to_frame("bollinger_bandwidth")
        parts.extend([atr, atr_ratio, bb_bw])

        # 3. Session features
        sess = compute_session_range_features(df, self.session_config)
        parts.append(sess)

        # 4. Structure features
        struct = compute_structure_features(df, self.sr_levels, self.zones)
        parts.append(struct)

        # 5. Momentum
        mom = compute_momentum_features(df, self.momentum_periods)
        parts.append(mom)

        features = pd.concat(parts, axis=1)
        logger.debug("Computed %d features across %d bars", len(features.columns), len(features))
        return features

    # ------------------------------------------------------------------

    def compute_incremental(
        self, df: pd.DataFrame, window: int = 500
    ) -> pd.DataFrame:
        """Compute features over the last *window* bars only (for speed)."""
        tail = df.iloc[-window:] if len(df) > window else df
        return self.compute(tail)
