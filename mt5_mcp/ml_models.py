"""ML Strategy Models.

Lightweight ML models for pattern recognition and signal generation.
Uses only standard library + numpy (no sklearn dependency).
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, field


@dataclass
class Signal:
    direction: str  # "LONG", "SHORT", "NEUTRAL"
    strength: float  # 0.0 - 1.0
    confidence: float  # 0.0 - 1.0
    features_used: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "features_used": self.features_used,
            "timestamp": self.timestamp,
        }


class MovingAverageCrossover:
    """Simple MA crossover strategy with adaptive thresholds."""

    def __init__(self, fast_period: int = 10, slow_period: int = 30):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def _sma(self, data: list[float], period: int) -> list[float]:
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(data[i])
            else:
                result.append(sum(data[i - period + 1:i + 1]) / period)
        return result

    def analyze(self, closes: list[float]) -> Signal:
        if len(closes) < self.slow_period + 2:
            return Signal(direction="NEUTRAL", strength=0.0, confidence=0.0)

        fast_ma = self._sma(closes, self.fast_period)
        slow_ma = self._sma(closes, self.slow_period)

        current_fast = fast_ma[-1]
        current_slow = slow_ma[-1]
        prev_fast = fast_ma[-2]
        prev_slow = slow_ma[-2]

        # Crossover detection
        crossed_up = prev_fast <= prev_slow and current_fast > current_slow
        crossed_down = prev_fast >= prev_slow and current_fast < current_slow

        # Distance from slow MA as strength
        distance = abs(current_fast - current_slow) / current_slow * 100
        strength = min(1.0, distance / 0.5)

        if crossed_up:
            return Signal(direction="LONG", strength=strength, confidence=0.7,
                         features_used=["MA_crossover"])
        elif crossed_down:
            return Signal(direction="SHORT", strength=strength, confidence=0.7,
                         features_used=["MA_crossover"])
        elif current_fast > current_slow:
            return Signal(direction="LONG", strength=strength * 0.5, confidence=0.4,
                         features_used=["MA_trend"])
        elif current_fast < current_slow:
            return Signal(direction="SHORT", strength=strength * 0.5, confidence=0.4,
                         features_used=["MA_trend"])

        return Signal(direction="NEUTRAL", strength=0.0, confidence=0.0)


class RSIStrategy:
    """Relative Strength Index strategy."""

    def __init__(self, period: int = 14, overbought: float = 70.0, oversold: float = 30.0):
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def _calc_rsi(self, closes: list[float]) -> float:
        if len(closes) < self.period + 1:
            return 50.0

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[-self.period:]) / self.period
        avg_loss = sum(losses[-self.period:]) / self.period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def analyze(self, closes: list[float]) -> Signal:
        rsi = self._calc_rsi(closes)

        if rsi < self.oversold:
            strength = (self.oversold - rsi) / self.oversold
            return Signal(direction="LONG", strength=min(1.0, strength),
                         confidence=0.65, features_used=["RSI"])
        elif rsi > self.overbought:
            strength = (rsi - self.overbought) / (100 - self.overbought)
            return Signal(direction="SHORT", strength=min(1.0, strength),
                         confidence=0.65, features_used=["RSI"])

        return Signal(direction="NEUTRAL", strength=0.0, confidence=0.3,
                     features_used=["RSI"])


class VolatilityStrategy:
    """ATR-based volatility breakout strategy."""

    def __init__(self, atr_period: int = 14, multiplier: float = 1.5):
        self.atr_period = atr_period
        self.multiplier = multiplier

    def _calc_atr(self, highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
        if len(closes) < 2:
            return [0.0]

        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)

        atrs = []
        for i in range(len(trs)):
            if i < self.atr_period - 1:
                atrs.append(sum(trs[:i + 1]) / (i + 1))
            else:
                atrs.append(sum(trs[i - self.atr_period + 1:i + 1]) / self.atr_period)

        return atrs

    def analyze(self, highs: list[float], lows: list[float], closes: list[float]) -> Signal:
        if len(closes) < self.atr_period + 2:
            return Signal(direction="NEUTRAL", strength=0.0, confidence=0.0)

        atrs = self._calc_atr(highs, lows, closes)
        current_atr = atrs[-1]
        current_close = closes[-1]
        prev_close = closes[-2]

        change = current_close - prev_close
        volatility_ratio = abs(change) / current_atr if current_atr > 0 else 0

        if change > 0 and volatility_ratio > self.multiplier:
            strength = min(1.0, volatility_ratio / (self.multiplier * 2))
            return Signal(direction="LONG", strength=strength, confidence=0.6,
                         features_used=["ATR_breakout"])
        elif change < 0 and volatility_ratio > self.multiplier:
            strength = min(1.0, volatility_ratio / (self.multiplier * 2))
            return Signal(direction="SHORT", strength=strength, confidence=0.6,
                         features_used=["ATR_breakout"])

        return Signal(direction="NEUTRAL", strength=0.0, confidence=0.2,
                     features_used=["ATR"])


class EnsembleStrategy:
    """Combines multiple strategies with weighted voting."""

    def __init__(self):
        self.strategies = [
            ("MA", MovingAverageCrossover(), 0.3),
            ("RSI", RSIStrategy(), 0.35),
            ("Volatility", VolatilityStrategy(), 0.35),
        ]

    def analyze(self, closes: list[float], highs: list[float] | None = None,
                lows: list[float] | None = None) -> Signal:
        if not highs:
            highs = closes
        if not lows:
            lows = closes

        votes: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0, "NEUTRAL": 0.0}
        total_confidence = 0.0
        all_features = []

        for name, strategy, weight in self.strategies:
            try:
                if name == "Volatility":
                    sig = strategy.analyze(highs, lows, closes)
                else:
                    sig = strategy.analyze(closes)
                votes[sig.direction] += weight * sig.strength
                total_confidence += weight * sig.confidence
                all_features.extend(sig.features_used)
            except Exception:
                pass

        # Determine winner
        best_dir = max(votes, key=votes.get)
        best_score = votes[best_dir]

        if best_score < 0.1:
            return Signal(direction="NEUTRAL", strength=0.0,
                         confidence=total_confidence / len(self.strategies),
                         features_used=all_features)

        return Signal(
            direction=best_dir,
            strength=min(1.0, best_score),
            confidence=min(1.0, total_confidence / len(self.strategies)),
            features_used=list(set(all_features)),
        )

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "success",
            "strategies": [name for name, _, _ in self.strategies],
            "weights": {name: w for name, _, w in self.strategies},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
