"""Market regime detection using weighted multi-indicator classification.

Classifies each bar into one of six regimes: TREND_UP, TREND_DOWN, RANGE,
HIGH_VOLATILITY, LOW_VOLATILITY, or TRANSITION.  All calculations are
rolling to prevent lookahead bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Regime Labels ──────────────────────────────────────────────────────

class Regime(str, Enum):
    """Possible market regime states."""

    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    TRANSITION = "TRANSITION"


# ── Configuration ──────────────────────────────────────────────────────

@dataclass
class RegimeConfig:
    """Tunable parameters for regime detection.

    Weights must sum to 1.0.  Thresholds follow the spec directly.
    """

    # Indicator weights (must sum to 1.0)
    w_ema_rel: float = 0.25
    w_ema_slope: float = 0.15
    w_adx: float = 0.25
    w_atr: float = 0.20
    w_price_struct: float = 0.15

    # EMA periods
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 100

    # ADX parameters
    adx_period: int = 14
    adx_trend_thresh: float = 25.0
    adx_range_thresh: float = 20.0

    # ATR parameters
    atr_period: int = 14
    atr_lookback: int = 200  # rolling window for percentile
    atr_high_pct: float = 80.0
    atr_low_pct: float = 20.0

    # Price‑structure lookback (bars for swing detection)
    swing_lookback: int = 5

    # EMA slope normalisation window (bars)
    slope_lookback: int = 20

    # Minimum warm‑up bars before regime is assigned
    warmup: int = 0  # auto‑set to max(ema_slow, atr_lookback, adx_period) + swing_lookback

    def __post_init__(self) -> None:
        total = (
            self.w_ema_rel
            + self.w_ema_slope
            + self.w_adx
            + self.w_atr
            + self.w_price_struct
        )
        if not np.isclose(total, 1.0):
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.4f}"
            )
        self.warmup = max(self.ema_slow, self.atr_lookback, self.adx_period * 2) + self.swing_lookback


# ── Core Detector ──────────────────────────────────────────────────────

class RegimeDetector:
    """Rolling, no‑lookahead regime classifier.

    Usage::

        detector = RegimeDetector()
        regimes = detector.detect(df)          # pd.Series of Regime strings
        features = detector.compute_regime_features(df)  # numeric matrix
        stats = detector.regime_stats(df, regimes)        # per‑regime stats
    """

    def __init__(self, config: Optional[RegimeConfig] = None) -> None:
        self.cfg = config or RegimeConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """Return a pd.Series of regime labels aligned to *df* index.

        No lookahead: every value at bar *t* is computed using only bars
        up to and including *t*.
        """
        self._validate(df)
        scores = self._score_all(df)          # shape (n, 6) order = enum order
        labels = self._argmax_to_regime(scores)
        labels.iloc[: self.cfg.warmup] = Regime.TRANSITION.value
        return labels

    def compute_regime_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame of numeric regime features for RL input."""
        self._validate(df)
        n = len(df)

        # --- raw per-indicator scores (0‑1 each) -----------------------
        ema_rel_score = self._ema_relationship_score(df)
        ema_slope_score = self._ema_slope_score(df)
        adx_score = self._adx_score(df)
        atr_score = self._atr_score(df)
        pscore = self._price_structure_score(df)

        # --- weighted total confidence ----------------------------------
        cfg = self.cfg
        total = (
            cfg.w_ema_rel * ema_rel_score
            + cfg.w_ema_slope * ema_slope_score
            + cfg.w_adx * adx_score
            + cfg.w_atr * atr_score
            + cfg.w_price_struct * pscore
        )

        # --- regime direction / type ------------------------------------
        regimes = self.detect(df)
        regime_up = (regimes == Regime.TREND_UP.value).astype(np.float64)
        regime_dn = (regimes == Regime.TREND_DOWN.value).astype(np.float64)
        regime_range = (regimes == Regime.RANGE.value).astype(np.float64)
        regime_hvol = (regimes == Regime.HIGH_VOLATILITY.value).astype(np.float64)
        regime_lvol = (regimes == Regime.LOW_VOLATILITY.value).astype(np.float64)
        regime_trans = (regimes == Regime.TRANSITION.value).astype(np.float64)

        # --- normalised ADX value ---------------------------------------
        adx_vals = self._rolling_adx(df)
        adx_norm = adx_vals / 100.0  # [0,1] since ADX ∈ [0,100]

        # --- trend direction [-1, +1] -----------------------------------
        ema_fast = df["close"].ewm(span=self.cfg.ema_fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.cfg.ema_slow, adjust=False).mean()
        spread = ema_fast - ema_slow
        spread_abs = spread.abs()
        # Normalise by ATR so the scale is meaningful across instruments
        atr = self._rolling_atr(df)
        # Avoid div by zero
        denom = atr.replace(0, np.nan)
        trend_dir = (spread / denom).clip(-1.0, 1.0).fillna(0.0)

        # --- confidence (strength) --------------------------------------
        strength = total.copy()
        strength.iloc[: self.cfg.warmup] = 0.0

        out = pd.DataFrame(
            {
                "regime_trend_up": regime_up,
                "regime_trend_down": regime_dn,
                "regime_range": regime_range,
                "regime_high_vol": regime_hvol,
                "regime_low_vol": regime_lvol,
                "regime_transition": regime_trans,
                "regime_strength": strength.values,
                "adx_value": adx_norm.values,
                "trend_direction": trend_dir.values,
            },
            index=df.index,
        )
        return out

    @staticmethod
    def regime_stats(df: pd.DataFrame, regimes: pd.Series) -> dict[str, dict]:
        """Compute per‑regime statistics: count, return, Sharpe, win rate, etc.

        Parameters
        ----------
        df : DataFrame
            OHLCV with at least ``close``.
        regimes : Series
            Regime labels aligned to *df* index (from :meth:`detect`).

        Returns
        -------
        dict mapping regime name → stat dict.
        """
        if len(df) != len(regimes):
            raise ValueError("df and regimes must have the same length")

        close = df["close"]
        ret = close.pct_change()

        stats: dict[str, dict] = {}
        for regime in Regime:
            mask = regimes == regime.value
            count = int(mask.sum())
            if count == 0:
                stats[regime.value] = {
                    "count": 0,
                    "mean_return": np.nan,
                    "sharpe": np.nan,
                    "max_drawdown": np.nan,
                    "win_rate": np.nan,
                    "pnl_total": np.nan,
                }
                continue

            r = ret[mask]
            total_ret = float(close[mask].iloc[-1] / close[mask].iloc[0] - 1.0) if count > 1 else 0.0

            # Sharpe (annualised assuming 252 trading days)
            mean_r = float(r.mean())
            std_r = float(r.std())
            sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0

            # Max drawdown within the regime's close series
            regime_close = close[mask]
            cummax = regime_close.cummax()
            dd = (regime_close - cummax) / cummax
            max_dd = float(dd.min())

            # Win rate
            wins = int((r > 0).sum())
            win_rate = wins / count if count > 0 else 0.0

            stats[regime.value] = {
                "count": count,
                "pct_of_total": count / len(df),
                "mean_return": mean_r,
                "sharpe": sharpe,
                "max_drawdown": max_dd,
                "win_rate": win_rate,
                "pnl_total": total_ret,
            }

        return stats

    # ------------------------------------------------------------------
    # Scoring helpers – each returns a pd.Series in [0, 1]
    # ------------------------------------------------------------------

    def _ema_relationship_score(self, df: pd.DataFrame) -> pd.Series:
        """Score based on EMA alignment.

        Perfect alignment (fast > mid > slow) → 1.0 (TREND_UP).
        Perfect alignment (fast < mid < slow) → 1.0 (TREND_DOWN).
        Fully converged → 0.0.
        """
        close = df["close"]
        ema_f = close.ewm(span=self.cfg.ema_fast, adjust=False).mean()
        ema_m = close.ewm(span=self.cfg.ema_mid, adjust=False).mean()
        ema_s = close.ewm(span=self.cfg.ema_slow, adjust=False).mean()

        # Count ordered pairs that are correctly aligned
        bull_pairs = ((ema_f > ema_m).astype(np.float64) + (ema_m > ema_s).astype(np.float64)) / 2.0
        bear_pairs = ((ema_f < ema_m).astype(np.float64) + (ema_m < ema_s).astype(np.float64)) / 2.0
        # Score is whichever side is more aligned
        score = pd.concat([bull_pairs, bear_pairs], axis=1).max(axis=1)
        return score

    def _ema_slope_score(self, df: pd.DataFrame) -> pd.Series:
        """Score based on EMA50 normalised slope over *slope_lookback* bars."""
        close = df["close"]
        ema = close.ewm(span=self.cfg.ema_mid, adjust=False).mean()
        lb = self.cfg.slope_lookback
        slope = ema.diff(lb) / ema.shift(lb).replace(0, np.nan)
        # Normalise by ATR so the slope is scale‑invariant
        atr = self._rolling_atr(df)
        norm = (slope / atr.replace(0, np.nan)).abs().clip(0, 1).fillna(0.0)
        return norm

    def _adx_score(self, df: pd.DataFrame) -> pd.Series:
        """Score based on ADX value.

        ADX > threshold → 1.0; ADX < range_thresh → 0.0; linear in between.
        """
        adx = self._rolling_adx(df)
        cfg = self.cfg
        # Linear interpolation between range_thresh and trend_thresh
        score = ((adx - cfg.adx_range_thresh) / (cfg.adx_trend_thresh - cfg.adx_range_thresh)).clip(
            0.0, 1.0
        )
        return score.fillna(0.0)

    def _atr_score(self, df: pd.DataFrame) -> pd.Series:
        """ATR percentile score.

        High ATR (top percentile) → score 1.0.
        Low ATR (bottom percentile) → score 1.0 (both extremes are notable).
        Mid ATR → score 0.0.
        """
        atr = self._rolling_atr(df)
        cfg = self.cfg
        # Rolling percentile rank
        pct_rank = atr.rolling(cfg.atr_lookback, min_periods=max(1, cfg.atr_lookback // 2)).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )
        # Distance from the centre (0.5) → extremes score high
        score = (pct_rank - 0.5).abs() * 2.0  # maps 0/1 → 1.0, 0.5 → 0.0
        return score.clip(0.0, 1.0).fillna(0.0)

    def _price_structure_score(self, df: pd.DataFrame) -> pd.Series:
        """Score based on swing‑high / swing‑low structure.

        Pure HH+HL → 1.0 (trending), mixed → lower score.
        """
        lb = self.cfg.swing_lookback
        highs = df["high"]
        lows = df["low"]

        # Local highs: rolling max centre == value
        roll_high = highs.rolling(2 * lb + 1, center=True).max()
        swing_highs = (highs == roll_high) & (highs.shift(1) != roll_high.shift(1))

        # Local lows
        roll_low = lows.rolling(2 * lb + 1, center=True).min()
        swing_lows = (lows == roll_low) & (lows.shift(1) != roll_low.shift(1))

        # Assign swing values (forward‑filled from the swing bar)
        sh_vals = highs.where(swing_highs).ffill(limit=lb)
        sl_vals = lows.where(swing_lows).ffill(limit=lb)

        # Higher‑high / lower‑low
        hh = sh_vals > sh_vals.shift(1)
        ll = sl_vals < sl_vals.shift(1)
        hl = sl_vals > sl_vals.shift(1)
        lh = sh_vals < sh_vals.shift(1)

        bull_struct = hh.astype(np.float64) + hl.astype(np.float64)
        bear_struct = ll.astype(np.float64) + lh.astype(np.float64)

        total_struct = bull_struct + bear_struct
        # Score = max proportion of aligned structure signals
        bull_prop = (bull_struct / total_struct.replace(0, np.nan)).fillna(0.0)
        bear_prop = (bear_struct / total_struct.replace(0, np.nan)).fillna(0.0)
        score = pd.concat([bull_prop, bear_prop], axis=1).max(axis=1)
        return score.fillna(0.0)

    # ------------------------------------------------------------------
    # Regime classification from composite scores
    # ------------------------------------------------------------------

    def _score_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return (n, 6) score matrix – one column per Regime enum value.

        Each cell is a weighted confidence that the bar belongs to that regime.
        """
        ema_rel = self._ema_relationship_score(df)
        ema_slope = self._ema_slope_score(df)
        adx_s = self._adx_score(df)
        atr_s = self._atr_score(df)
        ps = self._price_structure_score(df)

        adx_raw = self._rolling_adx(df)
        atr_pct = self._atr_percentile_rank(df)

        cfg = self.cfg

        # Trend scores combine EMA relationship + slope + ADX direction
        ema_f = df["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
        ema_s = df["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
        bull_ema = (ema_f > ema_s).astype(np.float64)
        bear_ema = (ema_f < ema_s).astype(np.float64)

        # ADX DI direction
        plus_di, minus_di = self._rolling_di(df)
        bull_di = (plus_di > minus_di).astype(np.float64)
        bear_di = (minus_di > plus_di).astype(np.float64)

        # Weighted trend direction confidence
        trend_conf = (
            cfg.w_ema_rel * ema_rel
            + cfg.w_ema_slope * ema_slope
            + cfg.w_adx * adx_s
        )

        # --- Build 6 columns in enum order ------------------------------
        n = len(df)
        mat = np.zeros((n, 6), dtype=np.float64)

        # TREND_UP
        mat[:, 0] = (
            cfg.w_ema_rel * ema_rel * bull_ema
            + cfg.w_ema_slope * ema_slope * bull_ema
            + cfg.w_adx * adx_s * bull_di
            + cfg.w_price_struct * ps * bull_ema
        ).values

        # TREND_DOWN
        mat[:, 1] = (
            cfg.w_ema_rel * ema_rel * bear_ema
            + cfg.w_ema_slope * ema_slope * bear_ema
            + cfg.w_adx * adx_s * bear_di
            + cfg.w_price_struct * ps * bear_ema
        ).values

        # RANGE – high when ADX and EMA scores are low
        range_indicator = (1.0 - adx_s) * (1.0 - ema_rel)
        mat[:, 2] = (cfg.w_adx * (1.0 - adx_s) + cfg.w_ema_rel * (1.0 - ema_rel) + 0.3 * range_indicator).values

        # HIGH_VOLATILITY – ATR > high percentile
        high_vol_signal = ((atr_pct * 2.0 - 1.0).clip(0, 1))  # above 50th pctl
        mat[:, 3] = (cfg.w_atr * atr_s + 0.5 * high_vol_signal).values

        # LOW_VOLATILITY – ATR < low percentile
        low_vol_signal = ((1.0 - atr_pct * 2.0).clip(0, 1))  # below 50th pctl
        mat[:, 4] = (cfg.w_atr * atr_s + 0.5 * low_vol_signal).values

        # TRANSITION – moderate ADX (20‑25) or EMA convergence
        transition_signal = ((1.0 - np.abs(adx_raw / 100.0 - 0.225) / 0.275).clip(0, 1))
        convergence = (1.0 - ema_rel) * adx_s  # low EMA alignment but some ADX
        mat[:, 5] = (cfg.w_adx * transition_signal * 0.5 + cfg.w_ema_rel * convergence * 0.5).values

        # Mask warmup
        mat[: cfg.warmup, :] = 0.0

        return pd.DataFrame(mat, index=df.index, columns=[r.value for r in Regime])

    def _argmax_to_regime(self, scores: pd.DataFrame) -> pd.Series:
        """Map the highest‑scoring regime per bar to a label string."""
        idx = scores.values.argmax(axis=1)
        labels = np.array([list(Regime)[i].value for i in idx])
        return pd.Series(labels, index=scores.index)

    # ------------------------------------------------------------------
    # Indicator calculations (rolling, no lookahead)
    # ------------------------------------------------------------------

    def _rolling_atr(self, df: pd.DataFrame) -> pd.Series:
        """Average True Range over *atr_period* bars."""
        h = df["high"]
        l = df["low"]
        c = df["close"].shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        return tr.rolling(self.cfg.atr_period, min_periods=self.cfg.atr_period).mean()

    def _rolling_adx(self, df: pd.DataFrame) -> pd.Series:
        """Average Directional Index (Wilder smoothing)."""
        period = self.cfg.adx_period
        h = df["high"]
        l = df["low"]
        c = df["close"]

        # True Range
        prev_c = c.shift(1)
        tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)

        # Directional Movement
        up_move = h - h.shift(1)
        down_move = l.shift(1) - l
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=df.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=df.index,
        )

        # Wilder smoothing (EMA with alpha = 1/period)
        alpha = 1.0 / period
        atr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

        # +DI / −DI
        plus_di = 100.0 * plus_dm_smooth / atr_smooth.replace(0, np.nan)
        minus_di = 100.0 * minus_dm_smooth / atr_smooth.replace(0, np.nan)

        # DX and ADX
        di_diff = (plus_di - minus_di).abs()
        di_sum = plus_di + minus_di
        dx = 100.0 * di_diff / di_sum.replace(0, np.nan)
        adx = dx.ewm(alpha=alpha, adjust=False).mean()

        return adx.fillna(0.0)

    def _rolling_di(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Return (+DI, −DI) as a tuple of Series."""
        period = self.cfg.adx_period
        h = df["high"]
        l = df["low"]
        c = df["close"]

        prev_c = c.shift(1)
        tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)

        up_move = h - h.shift(1)
        down_move = l.shift(1) - l
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=df.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=df.index,
        )

        alpha = 1.0 / period
        atr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

        plus_di = 100.0 * plus_dm_smooth / atr_smooth.replace(0, np.nan)
        minus_di = 100.0 * minus_dm_smooth / atr_smooth.replace(0, np.nan)
        return plus_di.fillna(0.0), minus_di.fillna(0.0)

    def _atr_percentile_rank(self, df: pd.DataFrame) -> pd.Series:
        """Rolling percentile rank of ATR within *atr_lookback* bars.

        Returns values in [0, 1] where 1 = highest ATR in the window.
        """
        atr = self._rolling_atr(df)
        lb = self.cfg.atr_lookback
        return atr.rolling(lb, min_periods=max(1, lb // 2)).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        ).fillna(0.5)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        if len(df) < 2:
            raise ValueError("DataFrame must have at least 2 rows")
