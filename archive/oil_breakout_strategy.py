"""Oil Breakout Strategy for USOIL/UKOIL.

Donchian channel breakout on M15 with H1 EMA200 trend filter,
optimized for oil's volatility characteristics.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from strategy_framework import (
    BaseStrategy,
    Signal,
    StrategyConfig,
    registry,
    get_client_instance,
    get_journal,
)
from mt5_mcp.mt5_client import MT5Error
from mt5_mcp.tools.supply_demand import detect_zones as detect_sd_zones

log = logging.getLogger("oil_breakout")


class OilBreakoutConfig(StrategyConfig):
    """Configuration for Oil Breakout strategy."""
    symbol: str = "USOIL"  # Can be USOIL or UKOIL
    risk_percent: float = 0.5
    max_spread_pips: float = 5.0
    min_rr: float = 1.5
    max_trades_per_day: int = 2
    confidence_floor: float = 60.0
    range_bars: int = 24
    atr_mult: float = 0.5
    ema_period: int = 200


class OilBreakoutStrategy(BaseStrategy):
    """Oil Breakout Strategy - Donchian breakout with trend filter."""

    def __init__(self, config: Any = None):
        config = config or OilBreakoutConfig()
        super().__init__("OilBreakout", config)
        self.client = get_client_instance()
        self.journal = get_journal()

    def get_default_config(self) -> Any:
        return OilBreakoutConfig()

    def scan(self):
        """Scan for oil breakout signals."""
        signals = []

        # Check limits
        allowed, reason = self._check_limits()
        if not allowed:
            return []

        try:
            # Check if symbol has rate data
            frame = self.client.rates(self.config.symbol, "M15", 5)
            if not frame or len(frame) < 10:
                return []
        except Exception:
            return []

        # Scan for breakout
        setup = self._find_breakout()
        if setup:
            self.signals_generated += 1
            return [setup]

        return []

    def _find_breakout(self):
        """Find Donchian breakout setup."""
        try:
            # Get M15 data for range
            frame = self.client.rates(self.config.symbol, "M15", self.config.range_bars + 50)
            if not frame or len(frame) < self.config.range_bars + 10:
                return None

            # Get H1 trend
            trend, ema200 = self._ema200_direction("H1")
            if trend == "flat":
                return None

            # Donchian range (exclude current forming bar)
            window = frame.iloc[-self.config.range_bars - 1 : -1]
            range_high = float(window["high"].max())
            range_low = float(window["low"].min())
            range_height = range_high - range_low

            # ATR for buffer
            atr = self._atr(14)
            if atr <= 0:
                return None

            # Current tick
            tick = self.client.tick(self.config.symbol)
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", bid))

            # Check breakout
            buffer = self.config.atr_mult * atr
            candidates = []

            # Long breakout
            if ask >= range_high - buffer:
                if trend != "bear":  # Only trade with trend
                    entry_buy = range_high + buffer
                    overshoot = ask - range_high
                    if overshoot <= 0.5 * atr:
                        sl_buy = range_low - 0.8 * atr
                        risk_buy = entry_buy - sl_buy
                        if risk_buy > 0:
                            tp_buy = entry_buy + 1.5 * risk_buy
                            rr = (entry_buy - sl_buy) / (entry_buy - sl_buy) * 1.5
                            # Calculate lots
                            lots = self._calculate_lots(entry_buy, sl_buy)
                            if lots > 0:
                                return self._build_signal(
                                    "BUY", entry_buy, sl_buy, entry_buy + 1.5 * (entry_buy - sl_buy),
                                    lots, "oil_breakout_buy"
                                )

            # Short breakout
            if bid <= range_low + buffer:
                if trend != "bull":
                    entry_sell = range_low - buffer
                    overshoot = range_low - bid
                    if overshoot <= 0.5 * atr:
                        sl_sell = range_high + 0.8 * atr
                        risk_sell = sl_sell - entry_sell
                        if risk_sell > 0:
                            tp_sell = entry_sell - 1.5 * risk_sell
                            lots = self._calculate_lots(entry_sell, sl_sell)
                            if lots > 0:
                                return self._build_signal(
                                    "SELL", entry_sell, entry_sell + 0.8 * atr,
                                    entry_sell - 1.5 * (entry_sell - sl_sell),
                                    lots, "oil_breakout_sell"
                                )

        except Exception as e:
            log.warning(f"Oil breakout scan error: {e}")

        return None

    def _ema200_direction(self, timeframe: str):
        """Get EMA200 trend direction."""
        try:
            # Request more bars to ensure we get enough for EMA200
            frame = self.client.rates(self.config.symbol, "H1", 500)
            if frame is None or len(frame) < 220:
                return "flat", None

            closes = frame["close"].astype(float)
            ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])
            last = float(frame["close"].iloc[-1])

            if last > ema200 * 1.0005:
                return "bull", ema200
            elif last < ema200 * 0.9995:
                return "bear", ema200
            return "flat", ema200
        except Exception:
            return "flat", None

    def _atr(self, period: int = 14) -> float:
        """Calculate ATR from M15 data."""
        try:
            frame = self.client.rates(self.config.symbol, "M15", period + 50)
            if frame is None or len(frame) < period + 10:
                return 0.0

            high = frame["high"].astype(float)
            low = frame["low"].astype(float)
            close = frame["close"].astype(float)

            tr1 = high - low
            tr2 = (high - frame["close"].shift(1)).abs()
            tr3 = (low - frame["close"].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            atr = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
            return float(atr)
        except Exception:
            return 0.0

    def _build_signal(self, side, entry, sl, tp, lots, setup_name):
        """Build a signal object."""
        from strategy_framework import Signal
        return Signal(
            symbol=self.config.symbol,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            lots=lots,
            confidence=70.0,
            rr=1.5,
            setup=f"oil_breakout_{side.lower()}",
            reason=f"Donchian breakout with H1 trend filter",
            metadata={"strategy": "OilBreakout"}
        )


# Register strategy
def register_oil_breakout():
    """Register oil breakout strategies for USOIL and UKOIL."""
    for sym in ["USOIL", "UKOIL"]:
        try:
            config = OilBreakoutConfig(symbol=sym)
            strategy = OilBreakoutStrategy(config)
            registry.register(strategy)
        except Exception as e:
            logging.getLogger("oil_breakout").warning(f"Failed to register {sym}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    register_oil_breakout()

    # Test scan
    for name, strategy in registry.strategies.items():
        if "OilBreakout" in name:
            signals = strategy.scan()
            print(f"{name}: {len(signals)} signals")