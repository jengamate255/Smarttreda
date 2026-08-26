"""Generic Strategy Framework for MT5 Trading System.

Provides base classes and utilities for implementing and testing
trading strategies with proper risk management, logging, and
performance tracking.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.client import get_client, reset_client
from mt5_mcp.config import get_settings, RiskConfig
from mt5_mcp.tools.supply_demand import detect_zones as detect_sd_zones
from mt5_mcp.tools.trades import open_market, pending_order
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.visual_notify import popup
from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.tools.risk import risk_report
from mt5_mcp.mt5_client import MT5Error

log = logging.getLogger("strategy_framework")

# --------------------------------------------------------------------------
# Data Classes
# --------------------------------------------------------------------------

@dataclass
class Signal:
    """Trading signal with all necessary execution parameters."""
    symbol: str
    side: str  # "BUY" or "SELL"
    entry: float
    sl: float
    tp: float
    lots: float
    confidence: float
    rr: float
    setup: str
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

@dataclass
class TradeResult:
    """Result of a trade execution."""
    success: bool
    ticket: int | None = None
    error: str | None = None
    order_type: str | None = None

@dataclass
class StrategyConfig:
    """Configuration for a strategy instance."""
    symbol: str
    risk_percent: float = 1.0
    max_spread_pips: float = 3.0
    min_rr: float = 1.5
    max_trades_per_day: int = 3
    confidence_floor: float = 65.0
    lookback: int = 200
    enabled: bool = True


# --------------------------------------------------------------------------
# Base Strategy Class
# --------------------------------------------------------------------------

class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(
        self,
        name: str,
        config: StrategyConfig,
        client=None,
        journal=None,
    ):
        self.name = name
        self.config = config
        self.client = client or get_client()
        self.journal = journal or get_journal()
        self.settings = get_settings()
        self.risk_cfg = RiskConfig()

        # Runtime state
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.signals_generated = 0
        self.trades_executed = 0

    @abstractmethod
    def scan(self) -> list[Signal]:
        """Scan for trading signals. Return list of signals."""
        pass

    @abstractmethod
    def get_default_config(self) -> StrategyConfig:
        """Return default configuration for this strategy."""
        pass

    def _reset_daily_counters(self):
        """Reset daily counters if new day."""
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            self.trades_today = 0
            self.daily_pnl = 0.0
            self.last_reset_date = today

    def _check_limits(self) -> tuple[bool, str]:
        """Check if strategy can trade. Returns (allowed, reason)."""
        self._reset_daily_counters()

        if not self.config.enabled:
            return False, "Strategy disabled"

        if self.trades_today >= self.config.max_trades_per_day:
            return False, f"Max trades/day reached ({self.config.max_trades_per_day})"

        # Check risk limits via risk_report
        report = risk_report()
        if not report.get("trading_allowed", True):
            return False, f"Risk limit: {report.get('warnings', [''])[0]}"

        # Check spread
        # (would need symbol info check here)

        return True, "OK"

    def _calculate_lots(self, symbol: str, entry: float, sl: float) -> float:
        """Calculate position size based on risk percent."""
        try:
            info = self.client.symbol_info(self.config.symbol)
            if not info:
                return 0.01

            equity = self.client.account_info().get("equity", 0)
            if equity <= 0:
                return 0.01

            risk_amount = equity * self.config.risk_percent / 100.0
            sl_distance = abs(entry - self.config.sl) if hasattr(self.config, 'sl') else 0

            # Get pip value
            pip_value = info.get("trade_tick_value", 1.0)
            tick_size = info.get("trade_tick_size", 0.00001)
            sl_pips = sl_distance / tick_size if tick_size > 0 else 0

            if sl_pips <= 0:
                return 0.01

            lots = risk_amount / (sl_pips * pip_value)

            # Round to step
            step = info.get("volume_step", 0.01)
            lots = round(lots / step) * step

            # Clamp
            min_lot = info.get("volume_min", 0.01)
            max_lot = info.get("volume_max", 100.0)
            lots = max(min_lot, min(lots, max_lot))

            return lots
        except Exception as e:
            log.warning(f"Lot calculation failed: {e}")
            return 0.01

    def execute_signal(self, signal: Signal) -> TradeResult:
        """Execute a trading signal."""
        allowed, reason = self._check_limits()
        if not allowed:
            return TradeResult(success=False, error=reason)

        try:
            if signal.side == "BUY":
                result = open_market(
                    signal.symbol,
                    "BUY",
                    signal.lots,
                    sl=signal.sl,
                    tp=signal.tp,
                    comment=f"{self.name}:{signal.setup}"
                )
            else:
                result = open_market(
                    signal.symbol,
                    "SELL",
                    signal.lots,
                    sl=signal.sl,
                    tp=signal.tp,
                    comment=f"{self.name}:{signal.setup}"
                )

            if result and result.get("ticket"):
                ticket = result["ticket"]
                self._record_trade(signal, ticket)
                self.trades_today += 1
                self.trades_executed += 1

                # Send telegram alert
                msg = (
                    f"🎯 {self.name} Trade Executed\n"
                    f"{signal.side} {signal.symbol} {signal.lots} lots\n"
                    f"Entry: {signal.entry:.5f} | SL: {signal.sl:.5f} | TP: {signal.tp:.5f}\n"
                    f"RR: {signal.rr:.2f} | Conf: {signal.confidence:.1f}%\n"
                    f"Setup: {signal.setup}"
                )
                send_telegram(msg)
                popup(msg, title=f"{self.name} Trade")

                return TradeResult(success=True, ticket=signal.ticket)
            else:
                return TradeResult(success=False, error="No ticket returned")

        except Exception as e:
            log.exception(f"Trade execution failed: {e}")
            return TradeResult(success=False, error=str(e))

    def _record_trade(self, signal: Signal, ticket: int):
        """Record trade to journal."""
        try:
            self.journal.record_trade(
                ticket=ticket,
                symbol=signal.symbol,
                side=signal.side,
                entry=signal.entry,
                sl=signal.sl,
                tp=signal.tp,
                lots=signal.lots,
                open_time=signal.timestamp.isoformat(),
                setup=signal.setup,
                confidence=signal.confidence,
                rr=signal.rr,
                reason=signal.reason,
            )
        except Exception as e:
            log.warning(f"Journal record failed: {e}")

    def run_cycle(self) -> list[TradeResult]:
        """Run one scan/execute cycle. Returns list of trade results."""
        self.signals_generated = 0
        results = []

        signals = self.scan()
        self.signals_generated = len(signals)

        for signal in signals:
            log.info(f"{self.name} signal: {signal.side} {signal.symbol} @ {signal.entry:.5f} (conf={signal.confidence:.1f}%)")
            result = self.execute_signal(signal)
            results.append(result)

        return results


# --------------------------------------------------------------------------
# Strategy Registry
# --------------------------------------------------------------------------

class StrategyRegistry:
    """Registry for managing and running multiple strategies."""

    def __init__(self):
        self.strategies: dict[str, BaseStrategy] = {}
        self.running = False

    def register(self, strategy: BaseStrategy):
        """Register a strategy."""
        self.strategies[strategy.name] = strategy
        log.info(f"Registered strategy: {strategy.name}")

    def unregister(self, name: str):
        """Unregister a strategy."""
        if name in self.strategies:
            del self.strategies[name]

    def run_all(self) -> dict[str, list]:
        """Run all registered strategies. Returns dict of results."""
        results = {}
        for name, strategy in self.strategies.items():
            try:
                results[name] = strategy.run_cycle()
            except Exception as e:
                log.exception(f"Strategy {name} failed: {e}")
                results[name] = []
        return results

    def get_status(self) -> dict:
        """Get status of all strategies."""
        return {
            name: {
                "enabled": s.config.enabled,
                "trades_today": s.trades_today,
                "signals_today": s.signals_generated,
                "executed": s.trades_executed,
            }
            for name, s in self.strategies.items()
        }


# Global registry instance
registry = StrategyRegistry()


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------

def get_client_instance():
    """Get or create MT5 client."""
    reset_client()
    client = get_client()
    client.ensure_connected()
    return client


def setup_logging(name: str, level=logging.INFO):
    """Setup logging for a strategy."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger