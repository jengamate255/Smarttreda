"""GER40 London Open Breakout Strategy.

Trades the London session open range breakout on GER40 (DAX).
Optimized for the 07:00-08:00 GMT volatility spike.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
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

log = logging.getLogger("ger40_london_open")


class GER40LondonOpenConfig(StrategyConfig):
    """Configuration for GER40 London Open Breakout strategy."""
    symbol: str = "GER40"
    risk_percent: float = 0.75
    max_spread_pips: float = 8.0
    min_rr: float = 1.5
    max_trades_per_day: int = 1
    confidence_floor: float = 65.0
    # London open session times (GMT)
    london_open_hour: int = 7
    london_open_minute: int = 0
    range_duration_minutes: int = 60
    breakout_buffer_pips: float = 5.0
    min_range_pips: float = 15.0
    max_range_pips: float = 150.0


class GER40LondonOpenStrategy(BaseStrategy):
    """GER40 London Open Range Breakout Strategy."""

    def __init__(self, config: Any = None):
        config = config or GER40LondonOpenConfig()
        super().__init__("GER40LondonOpen", config)
        self.client = get_client_instance()
        self.journal = get_journal()
        self._today_range = None

    def get_default_config(self) -> Any:
        return GER40LondonOpenConfig()

    def _is_london_open_window(self) -> bool:
        """Check if we're in the London open range-building window."""
        now = datetime.now(timezone.utc)
        open_time = now.replace(
            hour=self.config.london_open_hour,
            minute=self.config.london_open_minute,
            second=0,
            microsecond=0
        )
        end_time = open_time + timedelta(minutes=self.config.range_duration_minutes)
        return open_time <= now < end_time

    def _is_post_range_window(self) -> bool:
        """Check if we're in the post-range breakout window."""
        now = datetime.now(timezone.utc)
        open_time = datetime.now(timezone.utc).replace(
            hour=self.config.london_open_hour,
            minute=self.config.london_open_minute,
            second=0,
            microsecond=0
        )
        end_time = open_time + timedelta(minutes=self.config.range_duration_minutes)
        # Trade for 2 hours after range
        trade_end = end_time + timedelta(hours=2)
        return end_time <= now < trade_end

    def _build_range(self) -> dict | None:
        """Build the London open range."""
        try:
            # Get M5 data for the range period
            frame = self.client.rates(self.config.symbol, "M5", 200)
            if frame is None or len(frame) < 20:
                return None

            # Filter to London open window (7:00-8:00 GMT)
            # Convert time to UTC and filter
            frame = frame.copy()
            frame['hour'] = frame['time'].dt.hour
            frame['minute'] = frame['time'].dt.minute

            london_open = 7  # 7:00 GMT
            range_end = 8    # 8:00 GMT

            range_frame = frame[
                (frame['hour'] == london_open) |
                ((frame['hour'] == range_end) & (frame['minute'] == 0))
            ]

            if len(range_frame) < 10:
                return None

            range_high = float(range_frame["high"].max())
            range_low = float(range_frame["low"].min())
            range_open = float(range_frame.iloc[0]["open"])
            range_close = float(range_frame.iloc[-1]["close"])

            range_pips = (range_high - range_low) * 10000  # Convert to pips

            if range_pips < self.config.min_range_pips or range_pips > self.config.max_range_pips:
                return None

            return {
                "high": range_high,
                "low": range_low,
                "open": range_open,
                "close": range_close,
                "pips": range_pips,
                "high_time": range_frame.loc[range_frame["high"].idxmax(), "time"],
                "low_time": range_frame.loc[range_frame["low"].idxmin(), "time"],
            }
        except Exception as e:
            log.warning(f"Range build error: {e}")
            return None

    def scan(self):
        """Scan for London open breakout signals."""
        signals = []

        # Check limits
        allowed, reason = self._check_limits()
        if not allowed:
            return []

        # Only trade during post-range window
        if not self._is_post_range_window():
            return []

        # Check if we have today's range
        if self._today_range is None:
            self._today_range = self._build_range()
            if self._today_range is None:
                return []

        # Check if range is valid
        if self._today_range is None:
            return []

        # Check for breakout
        setup = self._check_breakout()
        if setup:
            self.signals_generated += 1
            return [setup]

        return []

    def _check_breakout(self):
        """Check for breakout of London open range."""
        try:
            range_data = self._today_range
            if not range_data:
                return None

            # Current price
            tick = self.client.tick(self.config.symbol)
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", 0.0))

            range_high = range_data["high"]
            range_low = range_data["low"]
            buffer = self.config.breakout_buffer_pips / 10000.0  # Convert pips to price

            # Long breakout
            if ask >= range_high + buffer:
                entry = range_high + self.config.breakout_buffer_pips / 10000.0
                sl = range_low
                tp = entry + 2.0 * (entry - range_low)
                lots = self._calculate_lots(entry, range_low)

                if lots > 0:
                    return Signal(
                        symbol=self.config.symbol,
                        side="BUY",
                        entry=entry,
                        sl=range_low,
                        tp=entry + 2.0 * (entry - range_low),
                        lots=lots,
                        confidence=75.0,
                        rr=2.0,
                        setup="ger40_london_open_buy",
                        reason="London open range breakout long",
                    )

            # Short breakout
            if bid <= range_low - buffer:
                entry = range_low - buffer
                lots = self._calculate_lots(range_high, entry)

                if lots > 0:
                    return Signal(
                        symbol=self.config.symbol,
                        side="SELL",
                        entry=entry,
                        sl=range_high,
                        tp=entry - 2.0 * (range_high - entry),
                        lots=lots,
                        confidence=75.0,
                        rr=2.0,
                        setup="ger40_london_open_sell",
                        reason="London open range breakout short",
                    )

        except Exception as e:
            log.warning(f"Breakout check error: {e}")

        return None


def register_ger40_london_open():
    """Register GER40 London Open strategy."""
    try:
        config = GER40LondonOpenConfig(symbol="GER40")
        strategy = GER40LondonOpenStrategy(config)
        registry.register(strategy)
        log.info("Registered GER40 London Open strategy")
    except Exception as e:
        logging.getLogger("ger40_london_open").warning(f"Failed to register: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    register_ger40_london_open()

    # Test
    for name, strategy in registry.strategies.items():
        if "GER40" in name:
            signals = strategy.scan()
            print(f"{name}: {len(signals)} signals")