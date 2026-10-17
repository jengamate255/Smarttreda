"""Feature Engine - creates reusable deterministic features from candle data.

Features are inputs - strategies decide how to use them.
Never turn every feature into a trading signal.

All features are deterministic given the same input data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from .data_schema import NormalizedCandle


# ---------------------------------------------------------------------------
# Return features
# ---------------------------------------------------------------------------

def returns(candle: NormalizedCandle, previous: NormalizedCandle | None = None) -> float:
    """Simple return: (close - open) / open.

    Standalone feature - not a signal.
    """
    if candle.open == 0:
        return 0.0
    return (candle.close - candle.open) / candle.open


def log_returns(candle: NormalizedCandle, previous_close: float | None = None) -> float:
    """Log return: ln(close / previous_close).

    Standalone feature - not a signal. Returns 0.0 if previous_close is None.
    """
    if previous_close is None or previous_close <= 0 or candle.close <= 0:
        return 0.0
    return np.log(candle.close / previous_close)


# ---------------------------------------------------------------------------
# Volatility features
# ---------------------------------------------------------------------------

def atr(high: float, low: float, close: float, prev_close: float) -> float:
    """Average True Range for a single candle.

    True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
    """
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    return max(tr1, tr2, tr3)


def rolling_volatility(
    candles: list[NormalizedCandle],
    window: int = 20,
    log_return: bool = True,
) -> float:
    """Rolling volatility over a window of candles.

    If log_return=True, use log returns; otherwise use simple returns.
    """
    if len(candles) < window + 1:
        return 0.0

    returns_list: list[float] = []
    for i in range(1, len(candles)):
        if log_return:
            r = log_returns(candles[i], candles[i - 1].close)
        else:
            r = returns(candles[i], candles[i - 1])
        returns_list.append(r)

    if len(returns_list) < window:
        return 0.0

    arr = np.array(returns_list[-window:])
    return float(np.std(arr) * np.sqrt(252))  # annualized assuming 252 days


# ---------------------------------------------------------------------------
# Range features
# ---------------------------------------------------------------------------

def range_pct(candle: NormalizedCandle) -> float:
    """Body range as percentage of total candle range.

    (close - open) / (high - low) normalized to [0, 1].
    """
    body = abs(candle.close - candle.open)
    tr = candle.total_range
    if tr == 0:
        return 0.0
    return body / tr


def true_range(high: float, low: float, prev_close: float) -> float:
    """True Range component (single candle)."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


# ---------------------------------------------------------------------------
# Session features
# ---------------------------------------------------------------------------

def session_range(
    candles: list[NormalizedCandle], session_hours: tuple[int, int] = (8, 17)
) -> float:
    """Range during specified session hours (UTC).

    session_hours = (start_hour, end_hour) in UTC 24h format.
    """
    if not candles:
        return 0.0

    session_candles = []
    for c in candles:
        hour = c.timestamp.hour
        start, end = session_hours
        # Handle overnight sessions
        if start < end:
            if start <= hour < end:
                session_candles.append(c)
        else:  # overnight: start..23 and 0..end
            if hour >= start or hour < end:
                session_candles.append(c)

    if not session_candles:
        return 0.0

    high_max = max(c.high for c in session_candles)
    low_min = min(c.low for c in session_candles)
    return high_max - low_min


# ---------------------------------------------------------------------------
# Historical levels
# ---------------------------------------------------------------------------

def previous_day_high_low(
    candles: list[NormalizedCandle],
) -> tuple[float, float]:
    """Previous day's high and low based on D1 timeframe."""
    # Find the most recent D1 candle boundary
    d1_candles = [c for c in candles if c.timeframe == "D1"]
    if not d1_candles:
        # Fallback: use last candle's day
        last = candles[-1]
        day_start = last.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        return last.high, last.low  # fallback

    # Get the most recent complete D1 candle
    latest_d1 = d1_candles[-1]
    day_low = latest_d1.low
    day_high = latest_d1.high

    # Look for the previous D1 candle
    if len(d1_candles) >= 2:
        prev_d1 = d1_candles[-2]
        day_low = prev_d1.low
        day_high = prev_d1.high

    return day_high, day_low


def previous_week_high_low(
    candles: list[NormalizedCandle],
) -> tuple[float, float]:
    """Previous week's high and low based on W1 timeframe."""
    w1_candles = [c for c in candles if c.timeframe == "W1"]
    if not w1_candles:
        # Fallback
        last = candles[-1]
        return last.high, last.low

    if len(w1_candles) >= 2:
        return w1_candles[-2].high, w1_candles[-2].low

    return w1_candles[-1].high, w1_candles[-1].low


def swing_points(
    candles: list[NormalizedCandle],
    lookback: int = 5,
) -> dict[str, float]:
    """Identify swing highs and lows.

    A swing high is a candle whose high is higher than lookback candles on each side.
    A swing low is a candle whose low is lower than lookback candles on each side.
    """
    if len(candles) < lookback * 2 + 1:
        return {"swing_high": 0.0, "swing_low": 0.0, "swing_high_idx": -1, "swing_low_idx": -1}

    result = {
        "swing_high": 0.0,
        "swing_low": float("inf"),
        "swing_high_idx": -1,
        "swing_low_idx": -1,
    }

    for i in range(lookback, len(candles) - lookback):
        # Check swing high
        is_high = all(
            candles[i].high > candles[j].high
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )
        # Check swing low
        is_low = all(
            candles[i].low < candles[j].low
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )

        if is_high and candles[i].high > result["swing_high"]:
            result["swing_high"] = candles[i].high
            result["swing_high_idx"] = i
        if is_low and candles[i].low < result["swing_low"]:
            result["swing_low"] = candles[i].low
            result["swing_low_idx"] = i

    if result["swing_low"] == float("inf"):
        result["swing_low"] = 0.0

    return result


# ---------------------------------------------------------------------------
# VWAP (where applicable)
# -

def vwap(
    candles: list[NormalizedCandle],
    start_idx: int = 0,
    end_idx: int | None = None,
) -> float:
    """Volume-weighted average price.

    Note: tick_volume is used if real_volume is not available.
    VWAP may not be meaningful for Forex tick data without real volume.
    """
    if end_idx is None:
        end_idx = len(candles)

    if end_idx <= start_idx or end_idx > len(candles):
        return 0.0

    total_pv = 0.0  # price * volume
    total_v = 0.0   # volume

    for i in range(start_idx, end_idx):
        c = candles[i]
        typical_price = (c.open + c.high + c.low + c.close) / 4
        volume = c.real_volume if c.real_volume is not None else c.volume
        if volume > 0:
            total_pv += typical_price * volume
            total_v += volume

    if total_v == 0:
        return 0.0
    return total_pv / total_v


# ---------------------------------------------------------------------------
# Feature bag - collect all features for a candle or candle series
# ---------------------------------------------------------------------------


class FeatureBag:
    """Container for all features computed from candle data.

    Features are organized by type and can be accessed by name.
    Strategies should not assume all features are present or meaningful
    for their asset class.
    """

    def __init__(self) -> None:
        self.returns: float = 0.0
        self.log_returns: float = 0.0
        self.atr: float = 0.0
        self.volatility_annual: float = 0.0
        self.range_pct: float = 0.0
        self.session_range_utc: float = 0.0
        self.prev_day_high: float = 0.0
        self.prev_day_low: float = 0.0
        self.prev_week_high: float = 0.0
        self.prev_week_low: float = 0.0
        self.swing_high: float = 0.0
        self.swing_low: float = 0.0
        self.vwap: float = 0.0
        self.volume_feature: float = 0.0

    def update_from_candle(self, candle: NormalizedCandle, previous: NormalizedCandle | None = None) -> None:
        """Update features from a single candle and its predecessor."""
        self.returns = returns(candle, previous)
        self.log_returns = log_returns(candle, previous.close if previous else None)

        if previous:
            self.atr = atr(candle.high, candle.low, candle.close, previous.close)
        else:
            self.atr = 0.0

        self.range_pct = range_pct(candle)

    def update_from_series(
        self,
        candles: list[NormalizedCandle],
    ) -> None:
        """Update features from a candle series."""
        if len(candles) < 2:
            return

        # Rolling volatility (20-period annualized)
        self.volatility_annual = rolling_volatility(candles, window=20)

        # Session range (London session: 08:00-17:00 UTC)
        self.session_range_utc = session_range(candles, session_hours=(8, 17))

        # Historical levels
        self.prev_day_high, self.prev_day_low = previous_day_high_low(candles)
        self.prev_week_high, self.prev_week_low = previous_week_high_low(candles)

        # Swing points
        sp = swing_points(candles)
        self.swing_high = sp["swing_high"]
        self.swing_low = sp["swing_low"]

        # VWAP
        self.vwap = vwap(candles)

        # Volume feature (normalized)
        volumes = [c.volume for c in candles if c.volume > 0]
        if volumes:
            self.volume_feature = float(np.median(volumes))
        else:
            self.volume_feature = 0.0