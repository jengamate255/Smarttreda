from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class MeanReversionAgent(BaseStrategy):

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

        overextended = self._detect_overextension(h4, h1)
        if overextended is None:
            return None

        mean_level = self._calculate_mean(m15, overextended)
        if mean_level is None:
            return None

        entry = self._time_entry(m5, overextended, mean_level)
        if entry is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction, entry_price, stop_loss, take_profit, reasoning, zone = entry

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        signal = Signal(
            signal_id=f"MR-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.LIMIT,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=0.0,
            timeframe="M5",
            confidence=min(0.8, 0.45 + (pips_reward / max(pips_risk, 1)) * 0.1),
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

    def _detect_overextension(self, h4: pd.DataFrame, h1: pd.DataFrame) -> Optional[dict]:
        if len(h4) < 30 or len(h1) < 20:
            return None

        h4_close = h4["close"].values
        h4_high = h4["high"].values
        h4_low = h4["low"].values

        sma_20 = sum(h4_close[-20:]) / 20
        sma_50 = sum(h4_close[-min(50, len(h4_close)):]) / min(50, len(h4_close))

        current = h4_close[-1]
        atr_h4 = self._atr(h4)

        if atr_h4 <= 0:
            return None

        deviation = (current - sma_20) / atr_h4

        if deviation > 2.0:
            h1_close = h1["close"].values
            rsi = self._rsi(h1_close, 14)
            if rsi and rsi > 65:
                return {
                    "direction": "bearish",
                    "deviation": deviation,
                    "sma_20": sma_20,
                    "sma_50": sma_50,
                    "rsi": rsi,
                    "atr": atr_h4,
                }
        elif deviation < -2.0:
            h1_close = h1["close"].values
            rsi = self._rsi(h1_close, 14)
            if rsi and rsi < 35:
                return {
                    "direction": "bullish",
                    "deviation": deviation,
                    "sma_20": sma_20,
                    "sma_50": sma_50,
                    "rsi": rsi,
                    "atr": atr_h4,
                }

        return None

    def _rsi(self, data: list, period: int = 14) -> Optional[float]:
        if len(data) < period + 1:
            return None

        deltas = [data[i] - data[i - 1] for i in range(1, len(data))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calculate_mean(self, m15: pd.DataFrame, overextended: dict) -> Optional[dict]:
        if len(m15) < 20:
            return None

        close = m15["close"].values
        sma_20 = sum(close[-20:]) / 20
        atr_m15 = self._atr(m15)

        if overextended["direction"] == "bullish":
            target = sma_20
            resistance = max(close[-20:])
            return {"target": target, "resistance": resistance, "atr": atr_m15}
        else:
            target = sma_20
            support = min(close[-20:])
            return {"target": target, "support": support, "atr": atr_m15}

    def _time_entry(
        self, m5: pd.DataFrame, overextended: dict, mean_level: dict
    ) -> Optional[tuple]:
        if m5.empty or len(m5) < 10:
            return None

        close = m5["close"].values
        atr = self._atr(m5)
        reasoning = []

        if overextended["direction"] == "bullish":
            entry_price = close[-1]
            stop_loss = entry_price - atr * 2.5
            take_profit = mean_level["target"]

            if entry_price <= take_profit:
                return None

            recent = close[-3:]
            if recent[-1] <= recent[0] or recent[-1] < recent[-2]:
                reasoning.append("M5 showing rejection from overextension")
            else:
                return None

            reasoning.append(f"Price extended {overextended['deviation']:.1f} ATR from mean")
            reasoning.append(f"RSI oversold at {overextended['rsi']:.1f}")
            reasoning.append(f"Target reversion to SMA20 at {mean_level['target']:.5f}")
            reasoning.append("H4 overextension confirmed")

            return (
                SignalDirection.BUY,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.3, entry_price + atr * 0.3),
            )

        elif overextended["direction"] == "bearish":
            entry_price = close[-1]
            stop_loss = entry_price + atr * 2.5
            take_profit = mean_level["target"]

            if entry_price >= take_profit:
                return None

            recent = close[-3:]
            if recent[-1] >= recent[0] or recent[-1] > recent[-2]:
                reasoning.append("M5 showing rejection from overextension")
            else:
                return None

            reasoning.append(f"Price extended {abs(overextended['deviation']):.1f} ATR from mean")
            reasoning.append(f"RSI overbought at {overextended['rsi']:.1f}")
            reasoning.append(f"Target reversion to SMA20 at {mean_level['target']:.5f}")
            reasoning.append("H4 overextension confirmed")

            return (
                SignalDirection.SELL,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (entry_price - atr * 0.3, entry_price + atr * 0.3),
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
