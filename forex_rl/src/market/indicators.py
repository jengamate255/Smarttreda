"""Core feature engineering pipeline for the RL agent.

Computes technical indicators, session/time encodings, and volatility metrics
from OHLCV DataFrames.  All features are strictly lookahead-safe — only data
up to and including the current bar is used.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── constant definitions ─────────────────────────────────────────────────────

_FEATURE_NAMES: list[str] = [
    # Price
    "returns",
    "log_returns",
    "candle_body",
    "upper_wick",
    "lower_wick",
    "candle_range",
    # Trend
    "ema_20",
    "ema_50",
    "ema_100",
    "ema_200",
    "ema_slope_20",
    "distance_from_ema_50",
    # Momentum
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "roc_10",
    # Volatility
    "atr_14",
    "rolling_volatility_20",
    "volatility_percentile",
    # Session
    "session_asian",
    "session_london",
    "session_newyork",
    "session_overlap",
    # Time
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
]


class FeatureEngine:
    """Compute a comprehensive feature set from OHLCV data.

    Every feature is computed with only past and present data — no look-ahead
    bias is introduced.  The recommended workflow is::

        engine = FeatureEngine()
        df = engine.compute_all(ohlcv_df)
        features = df[engine.feature_names()]
    """

    # ── public API ───────────────────────────────────────────────────────

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute every feature group and return *df* with new columns.

        Parameters
        ----------
        df : OHLCV DataFrame with columns ``open, high, low, close, volume``
             and a ``DatetimeIndex`` (UTC recommended).

        Returns
        -------
        DataFrame with all feature columns appended.
        """
        out = df.copy()
        out = pd.concat(
            [
                out,
                self.compute_returns(out),
                self.compute_trend(out),
                self.compute_momentum(out),
                self.compute_volatility(out),
                self.compute_sessions(out),
                self.compute_time(out),
            ],
            axis=1,
        )
        logger.debug("FeatureEngine produced %d columns", len(out.columns))
        return out

    def feature_names(self) -> list[str]:
        """Return the ordered list of all feature column names."""
        return list(_FEATURE_NAMES)

    # ── Price features ───────────────────────────────────────────────────

    def compute_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Close-to-close and log returns."""
        close = df["close"]
        prev_close = close.shift(1)

        returns = close.pct_change()
        log_returns = np.log(close / prev_close.replace(0, np.nan))

        candle_body = (close - df["open"]) / df["open"].replace(0, np.nan)
        upper_wick = (df["high"] - pd.concat([df["open"], close], axis=1).max(axis=1)) / close.replace(0, np.nan)
        lower_wick = (pd.concat([df["open"], close], axis=1).min(axis=1) - df["low"]) / close.replace(0, np.nan)
        candle_range = (df["high"] - df["low"]) / close.replace(0, np.nan)

        return pd.DataFrame(
            {
                "returns": returns,
                "log_returns": log_returns,
                "candle_body": candle_body,
                "upper_wick": upper_wick,
                "lower_wick": lower_wick,
                "candle_range": candle_range,
            },
            index=df.index,
        )

    # ── Trend features ───────────────────────────────────────────────────

    def compute_trend(self, df: pd.DataFrame) -> pd.DataFrame:
        """EMA levels, EMA slope, and distance from EMA 50."""
        close = df["close"]

        ema_20 = close.ewm(span=20, adjust=False, min_periods=1).mean()
        ema_50 = close.ewm(span=50, adjust=False, min_periods=1).mean()
        ema_100 = close.ewm(span=100, adjust=False, min_periods=1).mean()
        ema_200 = close.ewm(span=200, adjust=False, min_periods=1).mean()

        # EMA slope normalised by ATR-14 (lookahead-safe: ATR is also computed
        # with only past data).
        atr_14 = self._compute_true_range(df).ewm(span=14, adjust=False, min_periods=1).mean()
        ema_slope_20 = (ema_20 - ema_20.shift(5)) / atr_14.replace(0, np.nan)

        distance_from_ema_50 = (close - ema_50) / ema_50.replace(0, np.nan)

        return pd.DataFrame(
            {
                "ema_20": ema_20,
                "ema_50": ema_50,
                "ema_100": ema_100,
                "ema_200": ema_200,
                "ema_slope_20": ema_slope_20,
                "distance_from_ema_50": distance_from_ema_50,
            },
            index=df.index,
        )

    # ── Momentum features ────────────────────────────────────────────────

    def compute_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        """RSI, MACD, and rate-of-change."""
        close = df["close"]

        rsi_14 = self._compute_rsi(close, period=14)

        macd_line, macd_signal, macd_histogram = self._compute_macd(close)
        roc_10 = close.pct_change(periods=10)

        return pd.DataFrame(
            {
                "rsi_14": rsi_14,
                "macd_line": macd_line,
                "macd_signal": macd_signal,
                "macd_histogram": macd_histogram,
                "roc_10": roc_10,
            },
            index=df.index,
        )

    # ── Volatility features ──────────────────────────────────────────────

    def compute_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        """ATR, rolling volatility, and percentile rank."""
        atr_14 = self._compute_true_range(df).ewm(span=14, adjust=False, min_periods=1).mean()

        returns = df["close"].pct_change()
        rolling_vol_20 = returns.rolling(window=20, min_periods=10).std()

        # Percentile rank of ATR-14 over a rolling 100-bar window.
        # Uses min_periods=1 so values are available early, expanding window
        # provides a sensible baseline when fewer than 100 bars are seen.
        vol_percentile = atr_14.rolling(window=100, min_periods=1).apply(
            self._percentile_rank, raw=True
        )

        return pd.DataFrame(
            {
                "atr_14": atr_14,
                "rolling_volatility_20": rolling_vol_20,
                "volatility_percentile": vol_percentile,
            },
            index=df.index,
        )

    # ── Session features ─────────────────────────────────────────────────

    def compute_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Binary session flags derived from the UTC hour of each bar.

        Sessions (UTC):
        - Asian:   00:00–08:00
        - London:  07:00–16:00
        - NewYork: 12:00–21:00
        - Overlap: 12:00–16:00 (London & NY simultaneous)
        """
        hour = df.index.hour

        session_asian = ((hour >= 0) & (hour < 8)).astype(np.float64)
        session_london = ((hour >= 7) & (hour < 16)).astype(np.float64)
        session_newyork = ((hour >= 12) & (hour < 21)).astype(np.float64)
        session_overlap = ((hour >= 12) & (hour < 16)).astype(np.float64)

        return pd.DataFrame(
            {
                "session_asian": session_asian,
                "session_london": session_london,
                "session_newyork": session_newyork,
                "session_overlap": session_overlap,
            },
            index=df.index,
        )

    # ── Time features ────────────────────────────────────────────────────

    def compute_time(self, df: pd.DataFrame) -> pd.DataFrame:
        """Cyclical hour-of-day and day-of-week encodings."""
        hour = df.index.hour.astype(np.float64)
        dow = df.index.dayofweek.astype(np.float64)

        hour_sin = np.sin(2.0 * np.pi * hour / 24.0)
        hour_cos = np.cos(2.0 * np.pi * hour / 24.0)
        dow_sin = np.sin(2.0 * np.pi * dow / 7.0)
        dow_cos = np.cos(2.0 * np.pi * dow / 7.0)

        return pd.DataFrame(
            {
                "hour_sin": hour_sin,
                "hour_cos": hour_cos,
                "dow_sin": dow_sin,
                "dow_cos": dow_cos,
            },
            index=df.index,
        )

    # ── Normalisation ────────────────────────────────────────────────────

    def normalize(
        self,
        df: pd.DataFrame,
        method: Literal["zscore", "minmax"] = "zscore",
    ) -> pd.DataFrame:
        """Normalise feature columns in-place.

        Parameters
        ----------
        df : DataFrame whose columns are a superset of ``feature_names()``.
        method : ``'zscore'`` (zero-mean, unit-var) or ``'minmax'`` (0–1).

        Returns
        -------
        DataFrame with feature columns transformed.
        """
        out = df.copy()
        cols = [c for c in self.feature_names() if c in out.columns]

        if method == "zscore":
            mean = out[cols].mean()
            std = out[cols].std().replace(0, 1.0)
            out[cols] = (out[cols] - mean) / std
        elif method == "minmax":
            mn = out[cols].min()
            mx = out[cols].max()
            rng = (mx - mn).replace(0, 1.0)
            out[cols] = (out[cols] - mn) / rng
        else:
            raise ValueError(f"Unknown normalisation method: {method!r}")

        return out

    # ── Private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _compute_true_range(df: pd.DataFrame) -> pd.Series:
        """True Range series (no look-ahead)."""
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        # First bar has no previous close — set TR to simple range
        tr.iloc[0] = high.iloc[0] - low.iloc[0]
        return tr

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """Wilder's RSI — ema-based gain/loss averaging (no look-ahead)."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        # Wilder's smoothing ≡ ewm with alpha = 1/period
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _compute_macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """MACD line, signal, and histogram (no look-ahead)."""
        ema_fast = close.ewm(span=fast, adjust=False, min_periods=1).mean()
        ema_slow = close.ewm(span=slow, adjust=False, min_periods=1).mean()
        macd_line = ema_fast - ema_slow
        macd_signal = macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()
        macd_histogram = macd_line - macd_signal
        return macd_line, macd_signal, macd_histogram

    @staticmethod
    def _percentile_rank(arr: np.ndarray) -> float:
        """Compute percentile rank of the last element within the window."""
        arr = arr[~np.isnan(arr)]
        if len(arr) < 2:
            return np.nan
        return float(np.sum(arr[:-1] < arr[-1])) / float(len(arr) - 1) * 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# Stub classes — full implementations to follow
# ═══════════════════════════════════════════════════════════════════════════════


class MarketStructure:
    """Identify market structure (HH/HL/LH/LL) and swing points.

    Stub — to be implemented in ``structure.py``.
    """

    def __init__(self, swing_lookback: int = 5) -> None:
        self.swing_lookback = swing_lookback

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame with ``trend`` (bullish/bearish/sideways) columns."""
        logger.info("MarketStructure.detect called (stub)")
        return pd.DataFrame(
            {"market_structure": np.nan, "swing_high": np.nan, "swing_low": np.nan},
            index=df.index,
        )


class SupplyDemandDetector:
    """Detect supply and demand zones from price action.

    Stub — to be implemented in ``zones.py``.
    """

    def __init__(self, min_touches: int = 2, zone_lookback: int = 100) -> None:
        self.min_touches = min_touches
        self.zone_lookback = zone_lookback

    def detect(self, df: pd.DataFrame) -> list[dict]:
        """Return a list of zone dicts with keys ``type``, ``upper``, ``lower``, ``strength``."""
        logger.info("SupplyDemandDetector.detect called (stub)")
        return []


class RegimeDetector:
    """Classify the current market regime (trending / ranging / volatile).

    Stub — to be implemented in ``regime.py``.
    """

    def __init__(self, window: int = 100) -> None:
        self.window = window

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """Return a Series of regime labels for each bar."""
        logger.info("RegimeDetector.detect called (stub)")
        return pd.Series("unknown", index=df.index, name="regime")
