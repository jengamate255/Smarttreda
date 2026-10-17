from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class BreakoutAgent(BaseStrategy):

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

        consolidation = self._detect_consolidation(h1, m15)
        if consolidation is None:
            return None

        m15_breakout = self._check_breakout(m15, consolidation)
        if m15_breakout is None:
            return None

        m5_entry = self._confirm_breakout(m5, m15_breakout)
        if m5_entry is None:
            return None

        target = self._calculate_target(h4, m15_breakout, m5_entry["direction"])
        if target is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction = m5_entry["direction"]
        entry_price = m5_entry["entry"]
        stop_loss = m5_entry["stop"]
        take_profit = target

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        reasoning = [
            f"Consolidation detected: {consolidation['low']:.5f}-{consolidation['high']:.5f}",
            f"Breakout confirmed on M15 {'above' if direction == SignalDirection.BUY else 'below'} range",
            "M5 entry confirmed with momentum",
            f"Target based on H4 structure at {take_profit:.5f}",
        ]

        signal = Signal(
            signal_id=f"BK-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.MARKET,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=0.0,
            timeframe="M5",
            confidence=min(0.8, 0.4 + (pips_reward / max(pips_risk, 1)) * 0.1),
            reasoning=reasoning,
            pips_risk=round(pips_risk, 1),
            pips_reward=round(pips_reward, 1),
            entry_zone_low=consolidation["low"],
            entry_zone_high=consolidation["high"],
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

    def _detect_consolidation(self, h1: pd.DataFrame, m15: pd.DataFrame) -> Optional[dict]:
        if len(h1) < 20 or len(m15) < 20:
            return None

        h1_range = h1["high"].values[-20:] - h1["low"].values[-20:]
        avg_range = sum(h1_range) / len(h1_range)
        recent_range = sum(h1_range[-5:]) / 5

        if recent_range > avg_range * 0.85:
            return None

        m15_high = m15["high"].values[-20:]
        m15_low = m15["low"].values[-20:]

        range_high = max(m15_high)
        range_low = min(m15_low)
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        touches_high = sum(1 for h in m15_high if abs(h - range_high) < range_size * 0.05)
        touches_low = sum(1 for l in m15_low if abs(l - range_low) < range_size * 0.05)

        if touches_high >= 1 and touches_low >= 1:
            return {"high": range_high, "low": range_low, "size": range_size}

        return None

    def _check_breakout(self, m15: pd.DataFrame, consolidation: dict) -> Optional[dict]:
        if m15.empty:
            return None

        last_close = m15["close"].iloc[-1]
        last_high = m15["high"].iloc[-1]
        last_low = m15["low"].iloc[-1]
        range_size = consolidation["size"]

        if last_close > consolidation["high"] and (last_close - consolidation["high"]) > range_size * 0.05:
            stop = consolidation["high"] - range_size * 0.3
            return {"direction": SignalDirection.BUY, "level": consolidation["high"], "stop": stop}

        if last_close < consolidation["low"] and (consolidation["low"] - last_close) > range_size * 0.05:
            stop = consolidation["low"] + range_size * 0.3
            return {"direction": SignalDirection.SELL, "level": consolidation["low"], "stop": stop}

        return None

    def _confirm_breakout(self, m5: pd.DataFrame, breakout: dict) -> Optional[dict]:
        if m5.empty or len(m5) < 5:
            return None

        close = m5["close"].values[-5:]
        direction = breakout["direction"]

        if direction == SignalDirection.BUY:
            if close[-1] > breakout["level"] and close[-1] >= close[-2]:
                return {
                    "direction": direction,
                    "entry": close[-1],
                    "stop": breakout["stop"],
                }
        else:
            if close[-1] < breakout["level"] and close[-1] <= close[-2]:
                return {
                    "direction": direction,
                    "entry": close[-1],
                    "stop": breakout["stop"],
                }

        return None

    def _calculate_target(self, h4: pd.DataFrame, breakout: dict, direction: SignalDirection) -> Optional[float]:
        if h4.empty:
            return None

        range_size = breakout["level"] - (breakout["stop"] if direction == SignalDirection.BUY else breakout["level"])

        if direction == SignalDirection.BUY:
            return breakout["level"] + abs(breakout["level"] - breakout["stop"]) * 2.0
        else:
            return breakout["level"] - abs(breakout["stop"] - breakout["level"]) * 2.0
