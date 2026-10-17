from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class VolatilityAgent(BaseStrategy):

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

        compression = self._detect_compression(h4, h1)
        if compression is None:
            return None

        expansion_signal = self._detect_expansion(m15, compression)
        if expansion_signal is None:
            return None

        entry = self._time_entry(m5, expansion_signal, compression)
        if entry is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction, entry_price, stop_loss, take_profit, reasoning, zone = entry

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        signal = Signal(
            signal_id=f"VA-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.MARKET,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=0.0,
            timeframe="M5",
            confidence=min(0.82, 0.45 + (pips_reward / max(pips_risk, 1)) * 0.1),
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

    def _detect_compression(self, h4: pd.DataFrame, h1: pd.DataFrame) -> Optional[dict]:
        if len(h4) < 30 or len(h1) < 20:
            return None

        h4_atr_history = self._atr_series(h4, 14)
        h1_atr_history = self._atr_series(h1, 14)

        if len(h4_atr_history) < 10 or len(h1_atr_history) < 10:
            return None

        h4_current_atr = h4_atr_history[-1]
        h4_avg_atr = sum(h4_atr_history[-20:]) / min(20, len(h4_atr_history))

        h1_current_atr = h1_atr_history[-1]
        h1_avg_atr = sum(h1_atr_history[-20:]) / min(20, len(h1_atr_history))

        if h4_current_atr < h4_avg_atr * 0.75 and h1_current_atr < h1_avg_atr * 0.80:
            range_h4 = h4["high"].values[-20:] - h4["low"].values[-20:]
            avg_range = sum(range_h4) / len(range_h4)
            bollinger_width = self._bollinger_width(h1)

            return {
                "h4_atr_ratio": h4_current_atr / h4_avg_atr if h4_avg_atr > 0 else 0,
                "h1_atr_ratio": h1_current_atr / h1_avg_atr if h1_avg_atr > 0 else 0,
                "avg_range": avg_range,
                "bollinger_width": bollinger_width,
                "current_atr": h4_current_atr,
            }

        return None

    def _atr_series(self, df: pd.DataFrame, period: int = 14) -> list[float]:
        if len(df) < period + 1:
            return []
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = []
        for i in range(1, len(df)):
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        atr_vals = []
        for i in range(period - 1, len(tr)):
            atr_vals.append(sum(tr[i - period + 1 : i + 1]) / period)
        return atr_vals

    def _bollinger_width(self, df: pd.DataFrame, period: int = 20) -> float:
        if len(df) < period:
            return 0.0
        close = df["close"].values[-period:]
        sma = sum(close) / period
        variance = sum((c - sma) ** 2 for c in close) / period
        std_dev = variance ** 0.5
        return (2 * std_dev * 2) / sma if sma > 0 else 0.0

    def _detect_expansion(self, m15: pd.DataFrame, compression: dict) -> Optional[dict]:
        if len(m15) < 15:
            return None

        close = m15["close"].values
        high = m15["high"].values
        low = m15["low"].values

        recent_range = high[-1] - low[-1]
        avg_range = sum(high[-10:] - low[-10:]) / 10

        if recent_range > avg_range * 1.3:
            candle_body = abs(close[-1] - close[-2])
            candle_range = high[-1] - low[-1]

            if candle_range <= 0:
                return None

            body_ratio = candle_body / candle_range

            if body_ratio > 0.5:
                direction = SignalDirection.BUY if close[-1] >= close[-2] else SignalDirection.SELL
                return {
                    "direction": direction,
                    "expansion_ratio": recent_range / avg_range,
                    "body_ratio": body_ratio,
                    "atr": compression["current_atr"],
                }

        return None

    def _time_entry(
        self, m5: pd.DataFrame, expansion: dict, compression: dict
    ) -> Optional[tuple]:
        if m5.empty or len(m5) < 10:
            return None

        close = m5["close"].values
        atr = compression["current_atr"]
        reasoning = []

        direction = expansion["direction"]

        if direction == SignalDirection.BUY:
            entry_price = close[-1]
            stop_loss = entry_price - atr * 2.0
            take_profit = entry_price + atr * 3.0

            recent = close[-3:]
            if recent[-1] >= recent[0] or recent[-1] > recent[-2]:
                reasoning.append("M5 bullish breakout from compression")
            else:
                return None

            reasoning.append(f"Volatility expansion detected ({expansion['expansion_ratio']:.1f}x average)")
            reasoning.append(f"H4 compression ratio: {compression['h4_atr_ratio']:.2f}")
            reasoning.append("Body ratio confirms strong directional move")
            reasoning.append("Breakout from consolidation validated")

            return (
                SignalDirection.BUY,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.5, entry_price + atr * 0.5),
            )

        elif direction == SignalDirection.SELL:
            entry_price = close[-1]
            stop_loss = entry_price + atr * 2.0
            take_profit = entry_price - atr * 3.0

            recent = close[-3:]
            if recent[-1] <= recent[0] or recent[-1] < recent[-2]:
                reasoning.append("M5 bearish breakout from compression")
            else:
                return None

            reasoning.append(f"Volatility expansion detected ({expansion['expansion_ratio']:.1f}x average)")
            reasoning.append(f"H4 compression ratio: {compression['h4_atr_ratio']:.2f}")
            reasoning.append("Body ratio confirms strong directional move")
            reasoning.append("Breakout from consolidation validated")

            return (
                SignalDirection.SELL,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.5, entry_price + atr * 0.5),
            )

        return None
