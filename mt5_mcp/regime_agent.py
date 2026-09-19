"""Market regime classification agent.

Classifies market conditions: trending, ranging, volatile, quiet, etc.
"""

from __future__ import annotations

from typing import Literal

from .data_schema import NormalizedCandle


class RegimeAgent:
    """Classifies market regime from price action.

    Regimes:
    - trending: Clear directional movement
    - ranging: Sideways price action
    - volatile: High volatility, potential breakout
    - quiet: Low volatility, consolidation
    """

    def __init__(
        self,
        trend_threshold: float = 0.6,
        volatility_threshold: float = 1.5,
    ):
        self.trend_threshold = trend_threshold  # ADX-like threshold for trend
        self.volatility_threshold = volatility_threshold  # Multiple of avg vol for high vol

    def classify(self, candles: list[NormalizedCandle]) -> dict[str, str | float]:
        """Classify market regime from candle data.

        Args:
            candles: List of normalized candles, oldest first.

        Returns:
            Dict with regime, trend_strength, volatility_regime.
        """
        if len(candles) < 20:
            return {
                "regime": "unknown",
                "trend_strength": 0.0,
                "volatility_regime": "unknown",
            }

        return self._classify_from_candles(candles)

    def _classify_from_candles(
        self, candles: list[NormalizedCandle]
    ) -> dict[str, str | float]:
        """Classify regime from candle data."""
        # Calculate returns from closes
        returns = []
        for i in range(1, len(candles)):
            if candles[i - 1].close > 0:
                r = (candles[i].close - candles[i - 1].close) / candles[i - 1].close
                returns.append(r)
        if not returns:
            return {
                "regime": "unknown",
                "trend_strength": 0.0,
                "volatility_regime": "unknown",
            }

        # Calculate trend strength (direction consistency)
        positive_returns = sum(1 for r in returns if r > 0)
        total_returns = len(returns)
        trend_ratio = positive_returns / total_returns if total_returns > 0 else 0.5

        # Distance from 0.5 (pure random) indicates trend strength
        trend_strength = abs(trend_ratio - 0.5) * 2  # Normalize to 0-1

        # Classify trend
        if trend_strength > self.trend_threshold:
            if trend_ratio > 0.5:
                regime = "uptrend"
            else:
                regime = "downtrend"
        else:
            regime = "ranging"

        # Volatility classification
        vol = self._avg_volatility(candles[-20:]) if len(candles) >= 20 else 0.0
        avg_vol = self._avg_volatility(candles)

        if avg_vol > 0:
            vol_ratio = vol / avg_vol
        else:
            vol_ratio = 1.0

        if vol_ratio > self.volatility_threshold:
            volatility_regime = "high"
        elif vol_ratio < 0.5:
            volatility_regime = "low"
        else:
            volatility_regime = "normal"

        return {
            "regime": regime,
            "trend_strength": round(trend_strength, 3),
            "volatility_regime": volatility_regime,
        }

    def _avg_volatility(self, candles: list[NormalizedCandle]) -> float:
        """Calculate average volatility from candle ranges."""
        if len(candles) < 10:
            return 0.0

        ranges = [(c.high - c.low) for c in candles[-20:]]
        return sum(ranges) / len(ranges) if ranges else 0.0

    def is_trending(self, regime_data: dict) -> bool:
        """Check if market is trending."""
        return regime_data.get("regime", "").endswith("trend")

    def is_volatile(self, regime_data: dict) -> bool:
        """Check if market is volatile."""
        return regime_data.get("volatility_regime") == "high"
