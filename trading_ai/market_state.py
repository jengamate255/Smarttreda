"""Market State Engine – identifies market conditions via price-action concepts.

No RSI-based logic.  Every computation uses only past / current data so there is
zero look-ahead bias.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from trading_ai.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# ======================================================================
# Data structures
# ======================================================================

class TrendDirection(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    RANGING = "ranging"


class VolatilityRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    EXPANSION = "EXPANSION"
    COMPRESSION = "COMPRESSION"


class Session(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "ny"
    OVERLAP = "overlap"
    OFF = "off"


@dataclass(frozen=True)
class SwingPoint:
    index: Any  # pandas index label
    bar: int  # positional integer index
    price: float
    kind: str  # "high" or "low"


@dataclass(frozen=True)
class StructureEvent:
    index: Any
    bar: int
    kind: str  # "bos_bull", "bos_bear", "choch_bull", "choch_bear"
    level: float


@dataclass
class PriceZone:
    upper: float
    lower: float
    kind: str  # "supply", "demand", "support", "strength"


# ======================================================================
# Default session config (UTC hours)
# ======================================================================
_DEFAULT_SESSION_CONFIG: dict[str, tuple[int, int]] = {
    Session.ASIAN: (0, 8),     # 00:00–08:00 UTC
    Session.LONDON: (7, 16),   # 07:00–16:00 UTC
    Session.NEW_YORK: (13, 22), # 13:00–22:00 UTC
    Session.OVERLAP: (13, 16), # 13:00–16:00 UTC (London+NY)
}


# ======================================================================
# Helper: ATR
# ======================================================================

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range – vectorised, no look-ahead."""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - close).abs(), (low - close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ======================================================================
# Swing detection
# ======================================================================

def find_swing_highs_lows(
    df: pd.DataFrame, lookback: int = 5
) -> list[SwingPoint]:
    """Identify swing highs and lows.

    A bar is a swing high if its high is the unique max of the trailing
    window ``[i-lookback, i]`` (no future data).  Symmetric for swing lows.
    """
    if lookback < 1:
        lookback = 1
    highs = np.asarray(df["high"], dtype=float)
    lows = np.asarray(df["low"], dtype=float)
    n = len(df)
    if n <= lookback:
        return []

    # Windows of size lookback+1: window k = highs[k : k+lookback+1],
    # current bar is k+lookback (last element of the window).
    win_h = sliding_window_view(highs, lookback + 1)
    win_l = sliding_window_view(lows, lookback + 1)
    cur_h = highs[lookback:]
    cur_l = lows[lookback:]

    is_high = (cur_h == win_h.max(axis=1)) & (
        (win_h == cur_h[:, None]).sum(axis=1) == 1
    )
    is_low = (cur_l == win_l.min(axis=1)) & (
        (win_l == cur_l[:, None]).sum(axis=1) == 1
    )

    index = df.index
    swings: list[SwingPoint] = []
    for k in range(len(cur_h)):
        i = lookback + k
        if is_high[k]:
            swings.append(
                SwingPoint(
                    index=index[i], bar=i, price=float(cur_h[k]), kind="high"
                )
            )
        if is_low[k]:
            swings.append(
                SwingPoint(
                    index=index[i], bar=i, price=float(cur_l[k]), kind="low"
                )
            )
    swings.sort(key=lambda s: s.bar)
    return swings


# ======================================================================
# Break of Structure (BOS)
# ======================================================================

def detect_bos(df: pd.DataFrame, swings: list[SwingPoint]) -> list[StructureEvent]:
    """Detect Break of Structure events.

    * **Bullish BOS** – price breaks *above* the most recent swing high.
    * **Bearish BOS** – price breaks *below* the most recent swing low.
    """
    events: list[StructureEvent] = []
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None

    for sw in swings:
        if sw.kind == "high":
            if (
                last_swing_high is not None
                and sw.price > last_swing_high.price
                and sw.bar > last_swing_high.bar
            ):
                events.append(
                    StructureEvent(
                        index=sw.index,
                        bar=sw.bar,
                        kind="bos_bull",
                        level=last_swing_high.price,
                    )
                )
            last_swing_high = sw
        elif sw.kind == "low":
            if (
                last_swing_low is not None
                and sw.price < last_swing_low.price
                and sw.bar > last_swing_low.bar
            ):
                events.append(
                    StructureEvent(
                        index=sw.index,
                        bar=sw.bar,
                        kind="bos_bear",
                        level=last_swing_low.price,
                    )
                )
            last_swing_low = sw

    return events


# ======================================================================
# Change of Character (CHoCH)
# ======================================================================

def detect_choch(df: pd.DataFrame, swings: list[SwingPoint]) -> list[StructureEvent]:
    """Detect Change of Character events.

    * **Bullish CHoCH** – in a downtrend the price makes a *higher high*,
      breaking the prior swing high.
    * **Bearish CHoCH** – in an uptrend the price makes a *lower low*,
      breaking the prior swing low.

    This is essentially the *first* BOS against the prevailing structure.
    """
    events: list[StructureEvent] = []
    recent_highs: list[SwingPoint] = []
    recent_lows: list[SwingPoint] = []

    for sw in swings:
        if sw.kind == "high":
            recent_highs.append(sw)
        else:
            recent_lows.append(sw)

        # Need at least two swings of each type to compare
        if len(recent_highs) >= 2 and len(recent_lows) >= 1:
            prev_h = recent_highs[-2]
            curr_h = recent_highs[-1]
            prev_l = recent_lows[-1]

            # Bullish CHoCH: lower-low followed by higher-high
            if (
                prev_l.price < (recent_lows[-2].price if len(recent_lows) >= 2 else prev_l.price)
                and curr_h.price > prev_h.price
                and curr_h.bar > prev_h.bar
            ):
                events.append(
                    StructureEvent(
                        index=curr_h.index,
                        bar=curr_h.bar,
                        kind="choch_bull",
                        level=prev_h.price,
                    )
                )

        if len(recent_lows) >= 2 and len(recent_highs) >= 1:
            prev_l = recent_lows[-2]
            curr_l = recent_lows[-1]
            prev_h = recent_highs[-1]

            # Bearish CHoCH: higher-high followed by lower-low
            if (
                prev_h.price > (recent_highs[-2].price if len(recent_highs) >= 2 else prev_h.price)
                and curr_l.price < prev_l.price
                and curr_l.bar > prev_l.bar
            ):
                events.append(
                    StructureEvent(
                        index=curr_l.index,
                        bar=curr_l.bar,
                        kind="choch_bear",
                        level=prev_l.price,
                    )
                )

    return events


# ======================================================================
# Trend structure
# ======================================================================

def analyze_trend_from_swings(swings: list[SwingPoint]) -> TrendDirection:
    """Classify trend from precomputed swing points (no re-scan)."""
    if len(swings) < 4:
        return TrendDirection.RANGING
    recent_highs = [s for s in swings[-10:] if s.kind == "high"]
    recent_lows = [s for s in swings[-10:] if s.kind == "low"]
    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return TrendDirection.RANGING
    hh = recent_highs[-1].price > recent_highs[-2].price
    hl = recent_lows[-1].price > recent_lows[-2].price
    lh = recent_highs[-1].price < recent_highs[-2].price
    ll = recent_lows[-1].price < recent_lows[-2].price
    if hh and hl:
        return TrendDirection.UPTREND
    if lh and ll:
        return TrendDirection.DOWNTREND
    return TrendDirection.RANGING


def analyze_trend_structure(df: pd.DataFrame, lookback: int = 5) -> TrendDirection:
    """Classify the current trend structure from swings on *df*."""
    return analyze_trend_from_swings(find_swing_highs_lows(df, lookback=lookback))


# ======================================================================
# Supply & Demand zones
# ======================================================================

def find_supply_zones(
    df: pd.DataFrame, lookback: int = 20, min_move_bars: int = 3
) -> list[PriceZone]:
    """Identify supply zones: consolidation followed by a sharp drop.

    A supply zone is defined by:
    1. A sequence of bars where body ratio is low (consolidation).
    2. Followed by a strong bearish move (large candle or consecutive drops).

    All data is historical relative to the zone – no look-ahead.
    """
    zones: list[PriceZone] = []
    body = (df["close"] - df["open"]).abs()
    rng = df["high"] - df["low"]
    body_ratio = (body / rng.replace(0, np.nan)).to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    n = len(df)
    if n <= lookback + min_move_bars:
        return zones

    win_br = sliding_window_view(body_ratio, lookback)
    br_mean = win_br.mean(axis=1)

    i = lookback
    while i < n - min_move_bars:
        k = i - lookback
        if not np.isfinite(br_mean[k]) or br_mean[k] > 0.45:
            i += 1
            continue
        zone_high = float(highs[k:i].max())
        zone_low = float(closes[k:i].min())
        post_move = closes[i : i + min_move_bars]
        if len(post_move) >= min_move_bars and post_move[-1] < zone_low:
            zones.append(PriceZone(upper=zone_high, lower=zone_low, kind="supply"))
            i += min_move_bars
        else:
            i += 1
    return zones


def find_demand_zones(
    df: pd.DataFrame, lookback: int = 20, min_move_bars: int = 3
) -> list[PriceZone]:
    """Identify demand zones: consolidation followed by a sharp rally."""
    zones: list[PriceZone] = []
    body = (df["close"] - df["open"]).abs()
    rng = df["high"] - df["low"]
    body_ratio = (body / rng.replace(0, np.nan)).to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    n = len(df)
    if n <= lookback + min_move_bars:
        return zones

    win_br = sliding_window_view(body_ratio, lookback)
    br_mean = win_br.mean(axis=1)

    i = lookback
    while i < n - min_move_bars:
        k = i - lookback
        if not np.isfinite(br_mean[k]) or br_mean[k] > 0.45:
            i += 1
            continue
        zone_high = float(closes[k:i].max())
        zone_low = float(lows[k:i].min())
        post_move = closes[i : i + min_move_bars]
        if len(post_move) >= min_move_bars and post_move[-1] > zone_high:
            zones.append(PriceZone(upper=zone_high, lower=zone_low, kind="demand"))
            i += min_move_bars
        else:
            i += 1
    return zones


# ======================================================================
# Support / Resistance
# ======================================================================

def find_support_resistance(
    df: pd.DataFrame, lookback: int = 50, num_levels: int = 5,
    swings: list[SwingPoint] | None = None,
) -> list[PriceZone]:
    """Cluster swing highs/lows into S/R levels.

    Uses a simple binning approach: prices are binned, the most touched bins
    become S/R levels.  Pass *swings* to reuse a precomputed scan.
    """
    if swings is None:
        swings = find_swing_highs_lows(df, lookback=lookback)
    if not swings:
        return []

    prices = np.array([s.price for s in swings])
    if len(prices) < 2:
        return []

    # Quantile-based binning
    lo, hi = float(prices.min()), float(prices.max())
    if hi == lo:
        return [PriceZone(upper=hi, lower=lo, kind="support")]

    bins = np.linspace(lo, hi, num_levels + 1)
    counts, edges = np.histogram(prices, bins=bins)

    top_idx = np.argsort(counts)[::-1][:num_levels]
    levels: list[PriceZone] = []
    for idx in top_idx:
        lower = float(edges[idx])
        upper = float(edges[idx + 1])
        mid = (upper + lower) / 2
        kind = "support" if mid < df["close"].iloc[-1] else "resistance"
        levels.append(
            PriceZone(upper=upper, lower=lower, kind=kind)
        )

    levels.sort(key=lambda z: (z.upper + z.lower) / 2)
    return levels


# ======================================================================
# Volatility regime
# ======================================================================

def classify_volatility(
    df: pd.DataFrame, period: int = 14, percentiles: dict[str, float] | None = None
) -> VolatilityRegime:
    """Classify the current volatility regime using ATR percentiles.

    Thresholds (configurable via *percentiles*):
    - < p25       → LOW
    - p25 – p70   → NORMAL
    - p70 – p95   → HIGH
    - ≥ p95       → EXTREME

    Percentiles are computed over the *available* history – no look-ahead.
    """
    if percentiles is None:
        percentiles = {"low": 25, "high": 70, "extreme": 95}

    atr = _atr(df, period=period)
    current_atr = float(atr.iloc[-1])

    p_low = float(np.percentile(atr.dropna(), percentiles["low"]))
    p_high = float(np.percentile(atr.dropna(), percentiles["high"]))
    p_extreme = float(np.percentile(atr.dropna(), percentiles["extreme"]))

    if current_atr < p_low:
        return VolatilityRegime.LOW
    if current_atr < p_high:
        return VolatilityRegime.NORMAL
    if current_atr < p_extreme:
        return VolatilityRegime.HIGH
    return VolatilityRegime.EXTREME


# ======================================================================
# Session classification
# ======================================================================

def classify_session(
    timestamp: pd.Timestamp, session_config: dict[str, tuple[int, int]] | None = None
) -> Session:
    """Map a UTC timestamp to the active trading session."""
    if session_config is None:
        session_config = _DEFAULT_SESSION_CONFIG

    # Ensure UTC for comparison
    ts = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    hour = ts.hour

    # Overlap takes priority
    if _hour_in_range(hour, session_config.get(Session.OVERLAP, (13, 16))):
        return Session.OVERLAP

    for session_name in (Session.ASIAN, Session.LONDON, Session.NEW_YORK):
        rng = session_config.get(session_name)
        if rng and _hour_in_range(hour, rng):
            return session_name

    return Session.OFF


def _hour_in_range(hour: int, rng: tuple[int, int]) -> bool:
    start, end = rng
    if start <= end:
        return start <= hour < end
    # Overnight session (e.g., 22–2)
    return hour >= start or hour < end


# ======================================================================
# Market regime (overall)
# ======================================================================

def classify_market_regime_from_trend_vol(
    trend: TrendDirection, vol: VolatilityRegime
) -> MarketRegime:
    """Combine precomputed trend + volatility into a market regime."""
    if vol == VolatilityRegime.EXTREME:
        return MarketRegime.EXPANSION
    if vol == VolatilityRegime.LOW:
        return MarketRegime.COMPRESSION
    if trend == TrendDirection.UPTREND:
        return MarketRegime.TRENDING_UP
    if trend == TrendDirection.DOWNTREND:
        return MarketRegime.TRENDING_DOWN
    return MarketRegime.RANGING


def classify_market_regime(
    df: pd.DataFrame,
    trend_lookback: int = 5,
    volatility_period: int = 14,
) -> MarketRegime:
    """Combine trend, volatility and structure into a single market regime.

    Logic:
    - TRENDING_UP   – uptrend + volatility not compressed
    - TRENDING_DOWN – downtrend + volatility not compressed
    - RANGING       – no clear trend
    - EXPANSION     – extreme volatility (breakout environment)
    - COMPRESSION   – very low volatility (squeeze / coiled spring)
    """
    trend = analyze_trend_structure(df, lookback=trend_lookback)
    vol = classify_volatility(df, period=volatility_period)
    return classify_market_regime_from_trend_vol(trend, vol)


# ======================================================================
# Agent wrapper
# ======================================================================

class MarketStateAgent(BaseAgent):
    """Orchestrates all market-state analyses into a single result dict."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="market_state", config=config)

    # ------------------------------------------------------------------
    # Public helpers (thin wrappers so external callers have a clean API)
    # ------------------------------------------------------------------

    def find_swing_highs_lows(
        self, df: pd.DataFrame, lookback: int | None = None
    ) -> list[SwingPoint]:
        lb = lookback or self.config.get("swing_lookback", 5)
        return find_swing_highs_lows(df, lb)

    def detect_bos(
        self, df: pd.DataFrame, swings: list[SwingPoint] | None = None
    ) -> list[StructureEvent]:
        if swings is None:
            swings = self.find_swing_highs_lows(df)
        return detect_bos(df, swings)

    def detect_choch(
        self, df: pd.DataFrame, swings: list[SwingPoint] | None = None
    ) -> list[StructureEvent]:
        if swings is None:
            swings = self.find_swing_highs_lows(df)
        return detect_choch(df, swings)

    def analyze_trend_structure(
        self, df: pd.DataFrame, lookback: int | None = None
    ) -> TrendDirection:
        lb = lookback or self.config.get("swing_lookback", 5)
        return analyze_trend_structure(df, lb)

    def find_supply_zones(
        self, df: pd.DataFrame, lookback: int | None = None
    ) -> list[PriceZone]:
        lb = lookback or self.config.get("zone_lookback", 20)
        return find_supply_zones(df, lb)

    def find_demand_zones(
        self, df: pd.DataFrame, lookback: int | None = None
    ) -> list[PriceZone]:
        lb = lookback or self.config.get("zone_lookback", 20)
        return find_demand_zones(df, lb)

    def find_support_resistance(
        self, df: pd.DataFrame, lookback: int | None = None
    ) -> list[PriceZone]:
        lb = lookback or self.config.get("sr_lookback", 50)
        return find_support_resistance(df, lb)

    def classify_volatility(
        self, df: pd.DataFrame, period: int | None = None
    ) -> VolatilityRegime:
        p = period or self.config.get("atr_period", 14)
        return classify_volatility(df, period=p)

    def classify_market_regime(
        self,
        df: pd.DataFrame,
        trend_lookback: int | None = None,
        volatility_period: int | None = None,
    ) -> MarketRegime:
        tlb = trend_lookback or self.config.get("swing_lookback", 5)
        vp = volatility_period or self.config.get("atr_period", 14)
        return classify_market_regime(df, tlb, vp)

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    def process(self, data: pd.DataFrame) -> dict[str, Any]:
        """Compute the full market state snapshot for *data*."""
        self._ensure_configured()

        lb = int(self.config.get("swing_lookback", 5))
        swings = find_swing_highs_lows(data, lb)
        bos = detect_bos(data, swings)
        choch = detect_choch(data, swings)
        trend = analyze_trend_from_swings(swings)
        supply = find_supply_zones(
            data, lookback=int(self.config.get("zone_lookback", 20))
        )
        demand = find_demand_zones(
            data, lookback=int(self.config.get("zone_lookback", 20))
        )
        # S/R uses a longer swing window; only reuse when lookbacks match
        sr_lb = int(self.config.get("sr_lookback", 50))
        if sr_lb == lb:
            sr = find_support_resistance(data, lookback=sr_lb, swings=swings)
        else:
            sr = find_support_resistance(data, lookback=sr_lb)
        vol_regime = self.classify_volatility(data)
        regime = classify_market_regime_from_trend_vol(trend, vol_regime)

        return {
            "trend_structure": trend.value,
            "volatility_regime": vol_regime.value,
            "market_regime": regime.value,
            "swing_highs": [s for s in swings if s.kind == "high"],
            "swing_lows": [s for s in swings if s.kind == "low"],
            "bos_events": bos,
            "choch_events": choch,
            "supply_zones": supply,
            "demand_zones": demand,
            "support_resistance": sr,
            "last_close": float(data["close"].iloc[-1]),
        }
