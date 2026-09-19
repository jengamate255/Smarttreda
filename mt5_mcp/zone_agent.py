"""Zone detection agent - supply/demand zone identification.

Algorithmically identifies supply and demand zones from candle data.
Zones are price levels where significant buying/selling occurred.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from .data_schema import NormalizedCandle
from .domain_models import ZoneModel
from .asset import AssetClass


class ZoneAgent:
    """Detects supply/demand zones from price action.

    A demand zone forms when price drops sharply then rallies (buyers stepped in).
    A supply zone forms when price rallies sharply then drops (sellers stepped in).
    
    Algorithm:
    1. Identify "base" candles (small body, low volatility)
    2. Identify "impulse" candles (large body, high volatility) before/after base
    3. Zone = base candle's range, validated by impulse candle magnitude
    """

    def __init__(
        self,
        impulse_ratio: float = 2.0,
        min_body_pct: float = 0.6,
        max_body_pct: float = 0.4,
    ):
        self.impulse_ratio = impulse_ratio  # Impulse must be X times larger than avg
        self.min_impulse_body = min_body_pct  # Minimum body % for impulse candle
        self.max_base_body = max_body_pct  # Maximum body % for base candle

    def detect_zones(
        self,
        candles: list[NormalizedCandle],
        symbol: str,
        timeframe: str,
        lookback: int = 50,
    ) -> list[ZoneModel]:
        """Detect supply/demand zones from candle data.

        Args:
            candles: List of normalized candles, oldest first.
            symbol: Symbol name.
            timeframe: Timeframe string.
            lookback: Number of recent candles to analyze.

        Returns:
            List of detected ZoneModel objects.
        """
        if len(candles) < 5:
            return []

        # Use most recent candles
        data = candles[-lookback:] if len(candles) > lookback else candles
        zones: list[ZoneModel] = []

        # Calculate average body size for impulse detection
        avg_body = self._avg_body(data)

        for i in range(2, len(data) - 2):
            # Check for demand zone: base candle followed by bullish impulse
            if self._is_base_candle(data[i]) and self._is_bullish_impulse(data[i + 1], avg_body):
                zone = self._create_demand_zone(data, i, symbol, timeframe)
                if zone:
                    zones.append(zone)

            # Check for supply zone: base candle followed by bearish impulse
            if self._is_base_candle(data[i]) and self._is_bearish_impulse(data[i + 1], avg_body):
                zone = self._create_supply_zone(data, i, symbol, timeframe)
                if zone:
                    zones.append(zone)

            # Check for demand zone: bullish impulse followed by base candle (rally-base-rally)
            if self._is_bullish_impulse(data[i], avg_body) and self._is_base_candle(data[i + 1]):
                zone = self._create_demand_zone(data, i + 1, symbol, timeframe)
                if zone:
                    zones.append(zone)

            # Check for supply zone: bearish impulse followed by base candle (drop-base-drop)
            if self._is_bearish_impulse(data[i], avg_body) and self._is_base_candle(data[i + 1]):
                zone = self._create_supply_zone(data, i + 1, symbol, timeframe)
                if zone:
                    zones.append(zone)

        return self._dedupe_zones(zones)

    def _avg_body(self, candles: list[NormalizedCandle]) -> float:
        """Calculate average body size."""
        if not candles:
            return 0.0
        return sum(abs(c.close - c.open) for c in candles) / len(candles)

    def _is_base_candle(self, c: NormalizedCandle) -> bool:
        """Check if candle is a base (indecision) candle.

        Base candles have small bodies relative to their range.
        """
        rng = c.high - c.low
        if rng == 0:
            return False
        body = abs(c.close - c.open)
        body_pct = body / rng
        return body_pct <= self.max_base_body

    def _is_bullish_impulse(self, c: NormalizedCandle, avg_body: float) -> bool:
        """Check if candle is a bullish impulse (strong buying)."""
        body = c.close - c.open  # Positive for bullish
        rng = c.high - c.low
        if rng == 0 or avg_body == 0:
            return False
        body_pct = body / rng
        return (
            body > 0
            and body > avg_body * self.impulse_ratio
            and body_pct >= self.min_impulse_body
        )

    def _is_bearish_impulse(self, c: NormalizedCandle, avg_body: float) -> bool:
        """Check if candle is a bearish impulse (strong selling)."""
        body = c.open - c.close  # Positive for bearish
        rng = c.high - c.low
        if rng == 0 or avg_body == 0:
            return False
        body_pct = body / rng
        return (
            body > 0
            and body > avg_body * self.impulse_ratio
            and body_pct >= self.min_impulse_body
        )

    def _create_demand_zone(
        self,
        candles: list[NormalizedCandle],
        base_idx: int,
        symbol: str,
        timeframe: str,
    ) -> ZoneModel | None:
        """Create a demand zone from base candle."""
        base = candles[base_idx]
        zone = ZoneModel(
            symbol=symbol,
            timeframe=timeframe,
            zone_high=base.high,
            zone_low=base.low,
            type="demand",
            evidence=[
                f"Base candle at index {base_idx}",
                f"Body: {abs(base.close - base.open):.5f}",
                f"Range: {base.high - base.low:.5f}",
            ],
        )
        return zone

    def _create_supply_zone(
        self,
        candles: list[NormalizedCandle],
        base_idx: int,
        symbol: str,
        timeframe: str,
    ) -> ZoneModel | None:
        """Create a supply zone from base candle."""
        base = candles[base_idx]
        zone = ZoneModel(
            symbol=symbol,
            timeframe=timeframe,
            zone_high=base.high,
            zone_low=base.low,
            type="supply",
            evidence=[
                f"Base candle at index {base_idx}",
                f"Body: {abs(base.close - base.open):.5f}",
                f"Range: {base.high - base.low:.5f}",
            ],
        )
        return zone

    def _dedupe_zones(self, zones: list[ZoneModel]) -> list[ZoneModel]:
        """Remove duplicate zones (overlapping ranges of same type)."""
        if not zones:
            return []

        unique: list[ZoneModel] = []
        for z in zones:
            is_dup = False
            for u in unique:
                if (
                    z.type == u.type
                    and z.timeframe == u.timeframe
                    and z.symbol == u.symbol
                    and not (z.zone_low > u.zone_high or z.zone_high < u.zone_low)
                ):
                    is_dup = True
                    break
            if not is_dup:
                unique.append(z)
        return unique

    def zone_quality_score(self, zone: ZoneModel, current_price: float) -> float:
        """Score zone quality (0.0 - 1.0) based on distance and age.

        Closer zones are better. Fresh zones (untested) score higher.
        """
        score = 0.5  # Base score

        # Distance factor - closer is better
        dist = abs(current_price - zone.midpoint) if zone.midpoint else 0
        if dist > 0:
            # Normalize distance relative to zone width
            width = zone.zone_width or 0.001
            dist_ratio = dist / width
            if dist_ratio < 1.0:
                score += 0.3  # Price is within 1 zone width
            elif dist_ratio < 2.0:
                score += 0.2  # Price is within 2 zone widths
            elif dist_ratio < 5.0:
                score += 0.1  # Price is within 5 zone widths

        # Freshness factor - untested zones are more reliable
        if zone.tests == 0:
            score += 0.2
        elif zone.tests <= 2:
            score += 0.1

        return min(1.0, score)
