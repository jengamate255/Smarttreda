from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import OrderType, Signal, SignalDirection
from trading_ai.strategies.base import BaseStrategy


class SupplyDemandStrategy(BaseStrategy):

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

        h4_structure = self._analyze_structure(h4)
        h1_structure = self._analyze_structure(h1)

        if h4_structure["trend"] == "neutral" and h1_structure["trend"] == "neutral":
            return None

        demand_zones = self._find_demand_zones(h1, h4)
        supply_zones = self._find_supply_zones(h1, h4)

        m15_confirmation = self._confirm_zones(m15, demand_zones, supply_zones)
        if not m15_confirmation:
            return None

        entry_signal = self._find_entry(m5, m15_confirmation)
        if entry_signal is None:
            return None

        symbol = market_state.get("symbol", self.genome.market[0])
        direction, entry_price, stop_loss, take_profit, reasoning, entry_zone = entry_signal

        pips_risk = abs(entry_price - stop_loss) * (10000 if "JPY" not in symbol else 100)
        pips_reward = abs(take_profit - entry_price) * (10000 if "JPY" not in symbol else 100)

        signal = Signal(
            signal_id=f"SD-{uuid.uuid4().hex[:8]}",
            strategy_id=self.genome.strategy_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.LIMIT,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=0.0,
            timeframe="M5",
            confidence=min(0.9, 0.5 + (pips_reward / max(pips_risk, 1)) * 0.1),
            reasoning=reasoning,
            pips_risk=round(pips_risk, 1),
            pips_reward=round(pips_reward, 1),
            entry_zone_low=entry_zone[0],
            entry_zone_high=entry_zone[1],
        )

        if not self.validate_signal(signal):
            return None
        return signal

    def _passes_filters(self, market_state: dict) -> bool:
        for f in self.genome.filters:
            if f["type"] == "session":
                if not self._check_session(f["params"], market_state):
                    return False
            elif f["type"] == "volatility":
                if not self._check_volatility(f["params"], market_state):
                    return False
        return True

    def _check_session(self, params: dict, market_state: dict) -> bool:
        allowed = params.get("sessions", [])
        if not allowed:
            return True
        current_session = market_state.get("session", "")
        return current_session in allowed

    def _check_volatility(self, params: dict, market_state: dict) -> bool:
        min_atr = params.get("min_atr", 0)
        max_atr = params.get("max_atr", float("inf"))
        current_atr = market_state.get("atr", 0)
        return min_atr <= current_atr <= max_atr

    def _analyze_structure(self, df: pd.DataFrame) -> dict:
        if len(df) < 20:
            return {"trend": "neutral", "highs": [], "lows": []}

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        recent_highs = []
        recent_lows = []
        lookback = min(50, len(df) - 1)

        for i in range(2, lookback):
            if high[-i] > high[-i - 1] and high[-i] > high[-i + 1] if i + 1 < lookback else True:
                recent_highs.append(high[-i])
            if low[-i] < low[-i - 1] and low[-i] < low[-i + 1] if i + 1 < lookback else True:
                recent_lows.append(low[-i])

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            if recent_highs[-1] > recent_highs[-2] and recent_lows[-1] > recent_lows[-2]:
                return {"trend": "bullish", "highs": recent_highs, "lows": recent_lows}
            elif recent_highs[-1] < recent_highs[-2] and recent_lows[-1] < recent_lows[-2]:
                return {"trend": "bearish", "highs": recent_highs, "lows": recent_lows}

        return {"trend": "neutral", "highs": recent_highs, "lows": recent_lows}

    def _find_demand_zones(self, h1: pd.DataFrame, h4: pd.DataFrame) -> list[dict]:
        zones = []
        h4_lows = self._find_swing_lows(h4, lookback=min(20, len(h4) - 1))
        h1_lows = self._find_swing_lows(h1, lookback=min(30, len(h1) - 1))

        for low in h4_lows:
            for h1_low in h1_lows:
                if abs(h1_low["price"] - low["price"]) < low["price"] * 0.001:
                    zones.append({
                        "top": h1_low["price"] + h1_low["atr"] * 0.2,
                        "bottom": h1_low["price"] - h1_low["atr"] * 0.3,
                        "strength": "strong",
                    })
        return zones[:5]

    def _find_supply_zones(self, h1: pd.DataFrame, h4: pd.DataFrame) -> list[dict]:
        zones = []
        h4_highs = self._find_swing_highs(h4, lookback=min(20, len(h4) - 1))
        h1_highs = self._find_swing_highs(h1, lookback=min(30, len(h1) - 1))

        for high in h4_highs:
            for h1_high in h1_highs:
                if abs(h1_high["price"] - high["price"]) < high["price"] * 0.001:
                    zones.append({
                        "top": h1_high["price"] + h1_high["atr"] * 0.3,
                        "bottom": h1_high["price"] - h1_high["atr"] * 0.2,
                        "strength": "strong",
                    })
        return zones[:5]

    def _find_swing_lows(self, df: pd.DataFrame, lookback: int = 20) -> list[dict]:
        lows = []
        low = df["low"].values
        atr = self._compute_atr(df)
        for i in range(2, min(lookback, len(df) - 1)):
            if low[-i] < low[-i - 1] and low[-i] < low[-i + 1]:
                lows.append({"price": low[-i], "index": len(df) - i, "atr": atr})
        return lows

    def _find_swing_highs(self, df: pd.DataFrame, lookback: int = 20) -> list[dict]:
        highs = []
        high = df["high"].values
        atr = self._compute_atr(df)
        for i in range(2, min(lookback, len(df) - 1)):
            if high[-i] > high[-i - 1] and high[-i] > high[-i + 1]:
                highs.append({"price": high[-i], "index": len(df) - i, "atr": atr})
        return highs

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.001
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = []
        for i in range(1, len(df)):
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        return sum(tr[-period:]) / period if tr else 0.001

    def _confirm_zones(
        self, m15: pd.DataFrame, demand_zones: list[dict], supply_zones: list[dict]
    ) -> Optional[dict]:
        if m15.empty:
            return None
        last_close = float(m15["close"].iloc[-1])
        last_low = float(m15["low"].iloc[-1])
        last_high = float(m15["high"].iloc[-1])
        atr = self._compute_atr(m15)
        # Proximity: inside zone or within 0.75 ATR of its edge
        tol = atr * 0.75

        for zone in demand_zones:
            if last_close <= zone["top"] + tol and last_low >= zone["bottom"] - tol:
                return {"type": "demand", "zone": zone, "direction": "bullish"}

        for zone in supply_zones:
            if last_close >= zone["bottom"] - tol and last_high <= zone["top"] + tol:
                return {"type": "supply", "zone": zone, "direction": "bearish"}

        return None

    def _find_entry(
        self, m5: pd.DataFrame, confirmation: dict
    ) -> Optional[tuple]:
        if m5.empty or len(m5) < 10:
            return None

        zone = confirmation["zone"]
        direction = confirmation["direction"]
        zone_mid = (zone["top"] + zone["bottom"]) / 2
        atr = self._compute_atr(m5, period=10)

        if atr <= 0:
            return None

        close = m5["close"].values
        reasoning = []

        if direction == "bullish":
            entry_price = zone_mid
            stop_loss = zone["bottom"] - atr * 0.5
            take_profit = entry_price + (entry_price - stop_loss) * 2.0

            recent_closes = close[-3:]
            rising = sum(
                1 for i in range(1, len(recent_closes))
                if recent_closes[i] >= recent_closes[i - 1]
            )
            if rising >= 2 or recent_closes[-1] >= recent_closes[0]:
                reasoning.append("M5 showing bullish momentum into demand zone")
            else:
                return None

            reasoning.append(f"Bullish BOS detected in demand zone [{zone['bottom']:.5f}-{zone['top']:.5f}]")
            reasoning.append("H4/H1 structure confirms bullish bias")
            reasoning.append("M15 zone confirmation received")

            return (
                SignalDirection.BUY,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (zone["bottom"], zone["top"]),
            )

        elif direction == "bearish":
            entry_price = zone_mid
            stop_loss = zone["top"] + atr * 0.5
            take_profit = entry_price - (stop_loss - entry_price) * 2.0

            recent_closes = close[-3:]
            falling = sum(
                1 for i in range(1, len(recent_closes))
                if recent_closes[i] <= recent_closes[i - 1]
            )
            if falling >= 2 or recent_closes[-1] <= recent_closes[0]:
                reasoning.append("M5 showing bearish momentum into supply zone")
            else:
                return None

            reasoning.append(f"Bearish BOS detected in supply zone [{zone['bottom']:.5f}-{zone['top']:.5f}]")
            reasoning.append("H4/H1 structure confirms bearish bias")
            reasoning.append("M15 zone confirmation received")

            return (
                SignalDirection.SELL,
                round(entry_price, 5),
                round(stop_loss, 5),
                round(take_profit, 5),
                reasoning,
                (zone["bottom"], zone["top"]),
            )

        return None
