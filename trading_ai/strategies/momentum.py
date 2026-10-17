from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class MomentumAgent(BaseStrategy):

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

        momentum = self._detect_momentum(h1, m15)
        if momentum is None:
            return None

        continuation = self._find_continuation(m15, momentum)
        if continuation is None:
            return None

        entry = self._time_entry(m5, momentum)
        if entry is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction, entry_price, stop_loss, take_profit, reasoning, zone = entry

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        signal = Signal(
            signal_id=f"MO-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.MARKET,
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

    def _detect_momentum(self, h1: pd.DataFrame, m15: pd.DataFrame) -> Optional[dict]:
        if len(h1) < 20 or len(m15) < 10:
            return None

        h1_close = h1["close"].values
        h1_high = h1["high"].values
        h1_low = h1["low"].values

        body_sizes = [abs(h1_close[i] - h1_close[i - 1]) for i in range(-5, 0)]
        wick_sizes = [h1_high[i] - h1_low[i] for i in range(-5, 0)]

        avg_body = sum(body_sizes) / len(body_sizes) if body_sizes else 0
        avg_wick = sum(wick_sizes) / len(wick_sizes) if wick_sizes else 0

        if avg_wick <= 0 or avg_body / avg_wick < 0.45:
            return None

        up_count = sum(
            1 for i in range(-3, 0) if h1_close[i] > h1_close[i - 1]
        )
        down_count = sum(
            1 for i in range(-3, 0) if h1_close[i] < h1_close[i - 1]
        )
        consecutive_up = up_count >= 2
        consecutive_down = down_count >= 2

        if not consecutive_up and not consecutive_down:
            return None

        m15_close = m15["close"].values
        if consecutive_up and m15_close[-1] > m15_close[-2]:
            return {"direction": "bullish", "strength": avg_body / avg_wick}
        elif consecutive_down and m15_close[-1] < m15_close[-2]:
            return {"direction": "bearish", "strength": avg_body / avg_wick}

        return None

    def _find_continuation(self, m15: pd.DataFrame, momentum: dict) -> Optional[dict]:
        if len(m15) < 15:
            return None

        close = m15["close"].values
        atr = self._atr(m15)

        if momentum["direction"] == "bullish":
            pullback_low = min(close[-10:-3])
            current = close[-1]
            if current > pullback_low and current >= close[-2]:
                return {"type": "bull_flag", "support": pullback_low, "atr": atr}
        else:
            pullback_high = max(close[-10:-3])
            current = close[-1]
            if current < pullback_high and current <= close[-2]:
                return {"type": "bear_flag", "resistance": pullback_high, "atr": atr}

        return None

    def _time_entry(self, m5: pd.DataFrame, momentum: dict) -> Optional[tuple]:
        if m5.empty or len(m5) < 10:
            return None

        close = m5["close"].values
        atr = self._atr(m5)
        reasoning = []

        if momentum["direction"] == "bullish":
            entry_price = close[-1]
            stop_loss = entry_price - atr * 2.0
            take_profit = entry_price + atr * 3.0

            recent = close[-3:]
            if recent[-1] >= recent[0] or recent[-1] > recent[-2]:
                reasoning.append("M5 bullish continuation confirmed")
            else:
                return None

            reasoning.append(f"Strong H1 momentum detected (strength: {momentum['strength']:.2f})")
            reasoning.append("M15 continuation pattern validated")
            reasoning.append("MTF alignment confirmed")

            return (
                SignalDirection.BUY,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.5, entry_price + atr * 0.5),
            )

        elif momentum["direction"] == "bearish":
            entry_price = close[-1]
            stop_loss = entry_price + atr * 2.0
            take_profit = entry_price - atr * 3.0

            recent = close[-3:]
            if recent[-1] <= recent[0] or recent[-1] < recent[-2]:
                reasoning.append("M5 bearish continuation confirmed")
            else:
                return None

            reasoning.append(f"Strong H1 momentum detected (strength: {momentum['strength']:.2f})")
            reasoning.append("M15 continuation pattern validated")
            reasoning.append("MTF alignment confirmed")

            return (
                SignalDirection.SELL,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.5, entry_price + atr * 0.5),
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
