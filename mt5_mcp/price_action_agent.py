"""Price action pattern detection agent.

Detects candlestick patterns: pin bars, engulfing candles, shooting stars,
rejection candles, inside bars, breakout candles, and displacement candles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from .data_schema import NormalizedCandle
from .domain_models import ConfirmationModel
from .asset import AssetClass


class PriceActionAgent:
    """Detects price action patterns from candle data.

    Patterns:
    - Pin bar: Long wick, small body (rejection)
    - Engulfing: Current candle body covers previous candle body
    - Shooting star: Long upper wick, small body at low (bearish reversal)
    - Rejection: Candle with very long wick relative to body
    - Inside bar: Current candle range within previous candle range
    - Breakout: Current candle breaks above/below previous candle range
    - Displacement: Strong directional candle with high volume
    """

    def __init__(
        self,
        min_wick_pct: float = 0.6,
        min_body_pct_engulf: float = 0.5,
        min_displacement_body: float = 1.5,
    ):
        self.min_wick_pct = min_wick_pct  # Minimum wick % for pin bar/rejection
        self.min_body_pct_engulf = min_body_pct_engulf  # Minimum body % for engulfing
        self.min_displacement_body = min_displacement_body  # Body multiplier for displacement

    def analyze(
        self, candles: list[NormalizedCandle], symbol: str, timeframe: str
    ) -> ConfirmationModel | None:
        """Analyze recent candles for price action patterns.

        Args:
            candles: List of normalized candles, oldest first.
            symbol: Symbol name.
            timeframe: Timeframe string.

        Returns:
            ConfirmationModel if a pattern is detected, None otherwise.
        """
        if len(candles) < 3:
            return None

        # Analyze last 2-3 candles
        recent = candles[-3:]
        curr = recent[-1]
        prev = recent[-2] if len(recent) >= 2 else None

        # Check each pattern type
        patterns = []

        pin = self._check_pin_bar(curr, symbol, timeframe)
        if pin:
            patterns.append(pin)

        eng = self._check_engulfing(curr, prev, symbol, timeframe)
        if eng:
            patterns.append(eng)

        shoot = self._check_shooting_star(curr, symbol, timeframe)
        if shoot:
            patterns.append(shoot)

        inside = self._check_inside_bar(curr, prev, symbol, timeframe)
        if inside:
            patterns.append(inside)

        breakout = self._check_breakout(curr, prev, symbol, timeframe)
        if breakout:
            patterns.append(breakout)

        displacement = self._check_displacement(recent, symbol, timeframe)
        if displacement:
            patterns.append(displacement)

        # Return strongest pattern (prioritize reversals)
        if patterns:
            # Pin bars and shooting stars are strongest
            for p in patterns:
                if p.pattern in ("pin_bar", "shooting_star"):
                    return p
            return patterns[0]

        return None

    def _check_pin_bar(
        self, c: NormalizedCandle, symbol: str, timeframe: str
    ) -> ConfirmationModel | None:
        """Check for pin bar (hammer/hanging man)."""
        rng = c.high - c.low
        if rng == 0:
            return None

        body = abs(c.close - c.open)
        body_pct = body / rng

        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low

        # Pin bar: small body, long wick on one side
        if body_pct > 0.4:  # Body too large
            return None

        # Bullish pin bar (hammer): long lower wick
        if lower_wick > body * 2 and lower_wick > upper_wick * 2:
            wick_pct = lower_wick / rng
            if wick_pct >= self.min_wick_pct:
                return ConfirmationModel(
                    symbol=symbol,
                    timeframe=timeframe,
                    pattern="pin_bar",
                    is_bullish=True,
                    is_bearish=False,
                    body_size_pct=body_pct,
                    wick_pct=wick_pct,
                )

        # Bearish pin bar (hanging man/shooting star): long upper wick
        if upper_wick > body * 2 and upper_wick > lower_wick * 2:
            wick_pct = upper_wick / rng
            if wick_pct >= self.min_wick_pct:
                return ConfirmationModel(
                    symbol=symbol,
                    timeframe=timeframe,
                    pattern="pin_bar",
                    is_bullish=False,
                    is_bearish=True,
                    body_size_pct=body_pct,
                    wick_pct=wick_pct,
                )

        return None

    def _check_engulfing(
        self,
        curr: NormalizedCandle,
        prev: NormalizedCandle | None,
        symbol: str,
        timeframe: str,
    ) -> ConfirmationModel | None:
        """Check for engulfing pattern."""
        if prev is None:
            return None

        curr_body = abs(curr.close - curr.open)
        prev_body = abs(prev.close - prev.open)
        curr_range = curr.high - curr.low

        if curr_range == 0 or prev_body == 0:
            return None

        body_pct = curr_body / curr_range

        # Bullish engulfing: prev bearish, curr bullish, curr body engulfs prev
        prev_bearish = prev.close < prev.open
        curr_bullish = curr.close > curr.open

        if prev_bearish and curr_bullish:
            if curr.open <= prev.close and curr.close >= prev.open:
                if body_pct >= self.min_body_pct_engulf:
                    return ConfirmationModel(
                        symbol=symbol,
                        timeframe=timeframe,
                        pattern="engulfing",
                        is_bullish=True,
                        is_bearish=False,
                        body_size_pct=body_pct,
                    )

        # Bearish engulfing: prev bullish, curr bearish, curr body engulfs prev
        prev_bullish = prev.close > prev.open
        curr_bearish = curr.close < curr.open

        if prev_bullish and curr_bearish:
            if curr.open >= prev.close and curr.close <= prev.open:
                if body_pct >= self.min_body_pct_engulf:
                    return ConfirmationModel(
                        symbol=symbol,
                        timeframe=timeframe,
                        pattern="engulfing",
                        is_bullish=False,
                        is_bearish=True,
                        body_size_pct=body_pct,
                    )

        return None

    def _check_shooting_star(
        self, c: NormalizedCandle, symbol: str, timeframe: str
    ) -> ConfirmationModel | None:
        """Check for shooting star (bearish reversal)."""
        rng = c.high - c.low
        if rng == 0:
            return None

        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low

        body_pct = body / rng
        upper_pct = upper_wick / rng

        # Shooting star: small body at low, long upper wick
        if (
            body_pct <= 0.35
            and upper_pct >= 0.6
            and lower_wick < upper_wick * 0.3
        ):
            return ConfirmationModel(
                symbol=symbol,
                timeframe=timeframe,
                pattern="shooting_star",
                is_bullish=False,
                is_bearish=True,
                body_size_pct=body_pct,
                wick_pct=upper_pct,
            )

        return None

    def _check_inside_bar(
        self,
        curr: NormalizedCandle,
        prev: NormalizedCandle | None,
        symbol: str,
        timeframe: str,
    ) -> ConfirmationModel | None:
        """Check for inside bar (consolidation)."""
        if prev is None:
            return None

        if curr.high < prev.high and curr.low > prev.low:
            rng = prev.high - prev.low
            if rng == 0:
                return None

            body = abs(curr.close - curr.open)
            body_pct = body / rng if rng > 0 else 0

            return ConfirmationModel(
                symbol=symbol,
                timeframe=timeframe,
                pattern="inside_bar",
                body_size_pct=body_pct,
            )

        return None

    def _check_breakout(
        self,
        curr: NormalizedCandle,
        prev: NormalizedCandle | None,
        symbol: str,
        timeframe: str,
    ) -> ConfirmationModel | None:
        """Check for breakout candle."""
        if prev is None:
            return None

        rng = prev.high - prev.low
        if rng == 0:
            return None

        # Bullish breakout: close above previous high
        if curr.close > prev.high:
            body = abs(curr.close - curr.open)
            body_pct = body / rng
            return ConfirmationModel(
                symbol=symbol,
                timeframe=timeframe,
                pattern="breakout",
                is_bullish=True,
                is_bearish=False,
                body_size_pct=body_pct,
            )

        # Bearish breakout: close below previous low
        if curr.close < prev.low:
            body = abs(curr.close - curr.open)
            body_pct = body / rng
            return ConfirmationModel(
                symbol=symbol,
                timeframe=timeframe,
                pattern="breakout",
                is_bullish=False,
                is_bearish=True,
                body_size_pct=body_pct,
            )

        return None

    def _check_displacement(
        self,
        candles: list[NormalizedCandle],
        symbol: str,
        timeframe: str,
    ) -> ConfirmationModel | None:
        """Check for displacement (strong directional move)."""
        if len(candles) < 3:
            return None

        curr = candles[-1]

        # Calculate average body of previous candles
        prev_bodies = [abs(c.close - c.open) for c in candles[:-1]]
        if not prev_bodies:
            return None
        avg_body = sum(prev_bodies) / len(prev_bodies)

        if avg_body == 0:
            return None

        curr_body = abs(curr.close - curr.open)
        body_ratio = curr_body / avg_body

        if body_ratio >= self.min_displacement_body:
            is_bullish = curr.close > curr.open
            return ConfirmationModel(
                symbol=symbol,
                timeframe=timeframe,
                pattern="displacement",
                is_bullish=is_bullish,
                is_bearish=not is_bullish,
                body_size_pct=curr_body / (curr.high - curr.low) if curr.high > curr.low else 0,
            )

        return None
