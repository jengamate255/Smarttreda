from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class TrendAgent(BaseStrategy):

    def analyze(
        self,
        df_multi_timeframe: dict[str, pd.DataFrame],
        market_state: dict,
    ) -> Optional[Signal]:
        h4 = df_multi_timeframe.get("H4")
        h1 = df_multi_timeframe.get("H1")
        m15 = df_multi_timeframe.get("M15")
        m5 = df_multi_timeframe.get("M5")

        if any(df is None or df.empty for df in (h4, h1, m15, m5)):
            return None

        if not self._passes_filters(market_state):
            return None

        h4_trend = self._identify_trend(h4)
        h1_trend = self._identify_trend(h1)

        if h4_trend == "neutral" or h1_trend == "neutral":
            return None
        if h4_trend != h1_trend:
            return None

        structure_level = self._find_structure_level(h4, h1, h4_trend)
        if structure_level is None:
            return None

        m15_pullback = self._check_pullback(m15, structure_level, h4_trend)
        if not m15_pullback:
            return None

        entry = self._time_entry(m5, h4_trend, structure_level)
        if entry is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction, entry_price, stop_loss, take_profit, reasoning, zone = entry

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        signal = Signal(
            signal_id=f"TR-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.LIMIT,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=0.0,
            timeframe="M5",
            confidence=min(0.85, 0.5 + (pips_reward / max(pips_risk, 1)) * 0.1),
            reasoning=reasoning,
            pips_risk=round(pips_risk, 1),
            pips_reward=round(pips_reward, 1),
            entry_zone_low=zone[0],
            entry_zone_high=zone[1],
        )

        if not self.validate_signal(signal):
            return None
        return signal

    def _passes_filters(self, market_state: dict) -> bool:
        for f in self.genome.filters:
            if f["type"] == "session":
                allowed = f["params"].get("sessions", [])
                if allowed and market_state.get("session", "") not in allowed:
                    return False
            elif f["type"] == "volatility":
                min_atr = f["params"].get("min_atr", 0)
                max_atr = f["params"].get("max_atr", float("inf"))
                if not (min_atr <= market_state.get("atr", 0) <= max_atr):
                    return False
        return True

    def _identify_trend(self, df: pd.DataFrame) -> str:
        if len(df) < 20:
            return "neutral"

        close = df["close"].values
        ema_20 = self._ema(close, 20)
        ema_50 = self._ema(close, min(50, len(close) - 1))

        if ema_20 is None or ema_50 is None:
            return "neutral"

        recent_close = close[-1]
        # Small tolerance: EMA stacks rarely perfect on noisy data
        if recent_close > ema_20 and ema_20 >= ema_50 * 0.999:
            return "bullish"
        if recent_close < ema_20 and ema_20 <= ema_50 * 1.001:
            return "bearish"
        return "neutral"

    def _ema(self, data: list, period: int) -> Optional[float]:
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = (price - ema_val) * multiplier + ema_val
        return ema_val

    def _find_structure_level(self, h4: pd.DataFrame, h1: pd.DataFrame, trend: str) -> Optional[dict]:
        if len(h4) < 10 or len(h1) < 10:
            return None

        if trend == "bullish":
            h1_lows = []
            low = h1["low"].values
            for i in range(2, min(20, len(h1) - 1)):
                if low[-i] < low[-i - 1] and low[-i] < low[-i + 1]:
                    h1_lows.append(low[-i])
            if h1_lows:
                return {"price": h1_lows[0], "type": "support", "trend": "bullish"}
        else:
            h1_highs = []
            high = h1["high"].values
            for i in range(2, min(20, len(h1) - 1)):
                if high[-i] > high[-i - 1] and high[-i] > high[-i + 1]:
                    h1_highs.append(high[-i])
            if h1_highs:
                return {"price": h1_highs[0], "type": "resistance", "trend": "bearish"}

        return None

    def _check_pullback(self, m15: pd.DataFrame, structure: dict, trend: str) -> bool:
        if m15.empty:
            return False

        close = m15["close"].values
        level = structure["price"]
        atr = self._atr(m15)
        tolerance = atr * 2.5

        recent = close[-5:]
        # Near level within tolerance (pullback touch), not exact only
        if trend == "bullish":
            return any(abs(price - level) < tolerance for price in recent)
        else:
            return any(abs(price - level) < tolerance for price in recent)

    def _time_entry(self, m5: pd.DataFrame, trend: str, structure: dict) -> Optional[tuple]:
        if m5.empty or len(m5) < 10:
            return None

        close = m5["close"].values
        atr = self._atr(m5)
        level = structure["price"]
        reasoning = []

        if trend == "bullish":
            entry_price = level
            stop_loss = level - atr * 1.5
            take_profit = level + atr * 3.0

            recent = close[-3:]
            if recent[-1] >= recent[0] or recent[-1] > recent[-2]:
                reasoning.append("M5 bullish momentum confirms pullback entry")
            else:
                return None

            reasoning.append(f"Pullback to support level at {level:.5f}")
            reasoning.append("H4/H1 trend aligned bullish")
            reasoning.append("MTF trend confirmation complete")

            return (
                SignalDirection.BUY,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (level - atr * 0.5, level + atr * 0.5),
            )

        elif trend == "bearish":
            entry_price = level
            stop_loss = level + atr * 1.5
            take_profit = level - atr * 3.0

            recent = close[-3:]
            if recent[-1] <= recent[0] or recent[-1] < recent[-2]:
                reasoning.append("M5 bearish momentum confirms pullback entry")
            else:
                return None

            reasoning.append(f"Pullback to resistance level at {level:.5f}")
            reasoning.append("H4/H1 trend aligned bearish")
            reasoning.append("MTF trend confirmation complete")

            return (
                SignalDirection.SELL,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (level - atr * 0.5, level + atr * 0.5),
            )

        return None

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.001
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = []
        for i in range(1, len(df)):
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        return sum(tr[-period:]) / period if tr else 0.001
