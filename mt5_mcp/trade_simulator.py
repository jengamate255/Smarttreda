"""Trade simulator for backtesting.

Manages individual trade lifecycle: entry, exit, SL/TP handling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .data_schema import NormalizedCandle
from .domain_models import TradeSetup
from .backtest_engine import TradeRecord, BacktestConfig

logger = logging.getLogger("mt5_mcp.backtest")


class TradeSimulator:
    """Simulates individual trade execution and management.

    Handles:
    - Entry with slippage
    - Stop loss and take profit monitoring
    - Position sizing based on risk
    - Trade exit and PnL calculation
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self._trade_counter = 0

    def create_trade(self, setup: TradeSetup, candle: NormalizedCandle) -> TradeRecord | None:
        """Create a new trade from a setup.

        Args:
            setup: The trade setup to execute
            candle: Current candle for entry price

        Returns:
            TradeRecord if trade was opened, None if rejected
        """
        if not self._validate_setup(setup):
            return None

        # Check max positions
        # This is checked externally before calling create_trade

        # Calculate entry price with slippage
        entry_price = self._apply_slippage(
            candle.close, setup.direction
        )

        # Calculate position size
        position_size = self._calculate_position_size(
            entry_price, setup.stop_loss
        )

        if position_size <= 0:
            return None

        # Calculate commission
        commission = self.config.commission_per_trade

        self._trade_counter += 1
        trade = TradeRecord(
            id=f"trade_{self._trade_counter}",
            symbol=setup.symbol,
            direction=setup.direction,
            entry_time=candle.timestamp,
            entry_price=entry_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            position_size=position_size,
            setup_id=setup.id,
        )

        logger.debug(
            "Opened %s %s at %.5f, SL=%.5f, TP=%.5f, size=%.2f",
            trade.direction, trade.symbol, trade.entry_price,
            trade.stop_loss or 0, trade.take_profit or 0, trade.position_size,
        )

        return trade

    def check_exit(self, trade: TradeRecord, candle: NormalizedCandle) -> bool:
        """Check if trade should be exited.

        Args:
            trade: The trade to check
            candle: Current candle with OHLC data

        Returns:
            True if trade was closed, False if still open
        """
        if not trade.is_open:
            return False

        # Check stop loss (hit if low <= SL for long, or high >= SL for short)
        if trade.stop_loss:
            if trade.direction == "LONG" and candle.low <= trade.stop_loss:
                self._close_trade(trade, trade.stop_loss, candle.timestamp, "CLOSED_SL")
                return True
            elif trade.direction == "SHORT" and candle.high >= trade.stop_loss:
                self._close_trade(trade, trade.stop_loss, candle.timestamp, "CLOSED_SL")
                return True

        # Check take profit (hit if high >= TP for long, or low <= TP for short)
        if trade.take_profit:
            if trade.direction == "LONG" and candle.high >= trade.take_profit:
                self._close_trade(trade, trade.take_profit, candle.timestamp, "CLOSED_TP")
                return True
            elif trade.direction == "SHORT" and candle.low <= trade.take_profit:
                self._close_trade(trade, trade.take_profit, candle.timestamp, "CLOSED_TP")
                return True

        return False

    def close_trade(self, trade: TradeRecord, price: float, time: datetime) -> None:
        """Manually close a trade."""
        self._close_trade(trade, price, time, "CLOSED_MANUAL")

    def _validate_setup(self, setup: TradeSetup) -> bool:
        """Validate a trade setup before execution."""
        if not setup.entry and not setup.stop_loss:
            return False

        if setup.direction not in ("LONG", "SHORT"):
            return False

        if setup.stop_loss is None:
            return False

        return True

    def _apply_slippage(self, price: float, direction: str) -> float:
        """Apply slippage to entry price."""
        if self.config.slippage_pct <= 0:
            return price

        slippage = price * self.config.slippage_pct / 100

        if direction == "LONG":
            return price + slippage  # Slippage worse for long
        else:
            return price - slippage  # Slippage worse for short

    def _calculate_position_size(self, entry: float, stop_loss: float | None) -> float:
        """Calculate position size based on risk."""
        if stop_loss is None or stop_loss <= 0:
            return 0.0

        risk_amount = self.config.initial_capital * self.config.risk_per_trade
        risk_per_unit = abs(entry - stop_loss)

        if risk_per_unit <= 0:
            return 0.0

        position_size = risk_amount / risk_per_unit

        # Apply commission
        if self.config.commission_per_trade > 0:
            commission_per_unit = self.config.commission_per_trade / position_size if position_size > 0 else 0
            # Reduce position size slightly to account for commission
            position_size = risk_amount / (risk_per_unit + commission_per_unit)

        return max(0, position_size)

    def _close_trade(
        self,
        trade: TradeRecord,
        price: float,
        time: datetime,
        status: str,
    ) -> None:
        """Close a trade and calculate PnL."""
        trade.exit_price = price
        trade.exit_time = time
        trade.status = status

        # Calculate PnL
        if trade.direction == "LONG":
            trade.pnl = (price - trade.entry_price) * trade.position_size
        else:
            trade.pnl = (trade.entry_price - price) * trade.position_size

        # Subtract commission
        trade.pnl -= self.config.commission_per_trade

        # Calculate PnL percentage
        trade.pnl_pct = (trade.pnl / self.config.initial_capital) * 100

        logger.debug(
            "Closed %s %s at %.5f, PnL=%.2f (%.2f%%)",
            trade.direction, trade.symbol, price, trade.pnl, trade.pnl_pct,
        )
