"""Market structure analysis agent - BOS/CHoCH detection.

Analyzes market structure: Higher Highs, Higher Lows, Lower Highs, Lower Lows,
Break of Structure (BOS), and Change of Character (CHoCH).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from .data_schema import NormalizedCandle
from .domain_models import StructureModel
from .asset import AssetClass


class StructureAgent:
    """Analyzes market structure from price action.

    Structure concepts:
    - Swing High: Candle with highest high of N bars on each side
    - Swing Low: Candle with lowest low of N bars on each side
    - Higher High (HH): New swing high above previous swing high
    - Higher Low (HL): New swing low above previous swing low
    - Lower High (LH): New swing high below previous swing high
    - Lower Low (LL): New swing low below previous swing low
    - Break of Structure (BOS): Price breaks a swing point in trend direction
    - Change of Character (CHoCH): Price breaks a swing point against trend
    """

    def __init__(self, swing_lookback: int = 3):
        self.swing_lookback = swing_lookback  # Bars on each side for swing detection

    def analyze(self, candles: list[NormalizedCandle], symbol: str, timeframe: str) -> StructureModel:
        """Analyze market structure from candle data.

        Args:
            candles: List of normalized candles, oldest first.
            symbol: Symbol name.
            timeframe: Timeframe string.

        Returns:
            StructureModel with detected structure.
        """
        if len(candles) < self.swing_lookback * 2 + 1:
            return StructureModel(symbol=symbol, timeframe=timeframe, trend="unknown")

        # Detect swing points
        swing_highs, swing_lows = self._find_swings(candles)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StructureModel(symbol=symbol, timeframe=timeframe, trend="unknown")

        # Analyze swing sequence
        structure = self._classify_structure(swing_highs, swing_lows, symbol, timeframe)

        # Detect structure breaks
        self._detect_structure_breaks(candles, structure, swing_highs, swing_lows)

        return structure

    def _find_swings(
        self, candles: list[NormalizedCandle]
    ) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        """Find swing highs and swing lows.

        Returns:
            Tuple of (swing_highs, swing_lows) as lists of (index, price) tuples.
        """
        swing_highs: list[tuple[int, float]] = []
        swing_lows: list[tuple[int, float]] = []
        n = self.swing_lookback

        for i in range(n, len(candles) - n):
            # Check for swing high
            is_high = True
            for j in range(1, n + 1):
                if candles[i].high < candles[i - j].high or candles[i].high < candles[i + j].high:
                    is_high = False
                    break
            if is_high:
                swing_highs.append((i, candles[i].high))

            # Check for swing low
            is_low = True
            for j in range(1, n + 1):
                if candles[i].low > candles[i - j].low or candles[i].low > candles[i + j].low:
                    is_low = False
                    break
            if is_low:
                swing_lows.append((i, candles[i].low))

        return swing_highs, swing_lows

    def _classify_structure(
        self,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
        symbol: str,
        timeframe: str,
    ) -> StructureModel:
        """Classify structure from swing point sequence."""
        structure = StructureModel(symbol=symbol, timeframe=timeframe)

        # Analyze last few swing highs
        recent_highs = swing_highs[-3:] if len(swing_highs) >= 3 else swing_highs
        recent_lows = swing_lows[-3:] if len(swing_lows) >= 3 else swing_lows

        # Determine high/low pattern
        highs_pattern = self._classify_swings([h[1] for h in recent_highs])
        lows_pattern = self._classify_swings([l[1] for l in recent_lows])

        # Set swing points
        structure.swing_high = swing_highs[-1][1]
        structure.swing_low = swing_lows[-1][1]

        # Classify based on pattern
        if highs_pattern == "higher" and lows_pattern == "higher":
            structure.trend = "uptrend"
            structure.structure_type = "higher_high_higher_low"
        elif highs_pattern == "lower" and lows_pattern == "lower":
            structure.trend = "downtrend"
            structure.structure_type = "lower_high_lower_low"
        elif highs_pattern == "higher" and lows_pattern == "lower":
            structure.trend = "ranging"
            structure.structure_type = "higher_high_lower_low"
        elif highs_pattern == "lower" and lows_pattern == "higher":
            structure.trend = "ranging"
            structure.structure_type = "lower_high_higher_low"
        else:
            structure.trend = "ranging"
            structure.structure_type = "range"

        return structure

    def _classify_swings(self, values: list[float]) -> Literal["higher", "lower", "mixed"]:
        """Classify a sequence of swing values as higher, lower, or mixed."""
        if len(values) < 2:
            return "mixed"

        increasing = all(values[i] > values[i - 1] for i in range(1, len(values)))
        decreasing = all(values[i] < values[i - 1] for i in range(1, len(values)))

        if increasing:
            return "higher"
        elif decreasing:
            return "lower"
        else:
            return "mixed"

    def _detect_structure_breaks(
        self,
        candles: list[NormalizedCandle],
        structure: StructureModel,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
    ) -> None:
        """Detect BOS and CHoCH events."""
        if not swing_highs or not swing_lows:
            return

        last_close = candles[-1].close

        # Check for break of swing high
        if len(swing_highs) >= 2:
            prev_high = swing_highs[-2][1]
            curr_high = swing_highs[-1][1]

            if last_close > curr_high:
                if structure.trend == "uptrend":
                    # BOS in uptrend - breaking higher
                    structure.set_bos(curr_high)
                elif structure.trend == "downtrend":
                    # CHoCH - breaking against downtrend
                    structure.set_choch(curr_high)

        # Check for break of swing low
        if len(swing_lows) >= 2:
            prev_low = swing_lows[-2][1]
            curr_low = swing_lows[-1][1]

            if last_close < curr_low:
                if structure.trend == "downtrend":
                    # BOS in downtrend - breaking lower
                    structure.set_bos(curr_low)
                elif structure.trend == "uptrend":
                    # CHoCH - breaking against uptrend
                    structure.set_choch(curr_low)

    def is_trending(self, structure: StructureModel) -> bool:
        """Check if market is trending (not ranging)."""
        return structure.trend in ("uptrend", "downtrend")

    def trend_direction(self, structure: StructureModel) -> Literal["bullish", "bearish", "neutral"]:
        """Get trend direction."""
        if structure.trend == "uptrend":
            return "bullish"
        elif structure.trend == "downtrend":
            return "bearish"
        return "neutral"

    def has_structure_break(self, structure: StructureModel) -> bool:
        """Check if a structure break (BOS/CHoCH) has occurred."""
        return structure.structure_break is not None
