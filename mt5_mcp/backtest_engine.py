"""Backtesting engine for the Trading Intelligence Platform.

This module provides a backtesting framework that runs the strategy pipeline
on historical data and simulates trades. It's designed for strategy validation
and parameter optimization, NOT for live trading.

Key principles:
- Backtesting is for validation, not profit generation
- Past performance does not guarantee future results
- Overfitting is the primary risk to guard against
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .data_schema import NormalizedCandle
from .domain_models import TradeSetup, TradeSetupStatus
from .asset import AssetClass

logger = logging.getLogger("mt5_mcp.backtest")


# ---------------------------------------------------------------------------
# Backtest configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""
    initial_capital: float = 10000.0
    risk_per_trade: float = 0.01
    max_positions: int = 5
    commission_per_trade: float = 0.0
    slippage_pct: float = 0.0
    min_risk_reward: float = 2.0
    enable_red_team: bool = False
    enable_llm: bool = False


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Record of a single trade."""
    id: str
    symbol: str
    direction: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"
    setup_id: str = ""
    notes: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


# ---------------------------------------------------------------------------
# Backtest result
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Result of a backtest run."""
    config: BacktestConfig
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0

    def calculate_metrics(self) -> None:
        """Calculate performance metrics from trades."""
        closed_trades = [t for t in self.trades if not t.is_open]
        if not closed_trades:
            return

        self.total_trades = len(closed_trades)
        self.winning_trades = sum(1 for t in closed_trades if t.is_winner)
        self.losing_trades = self.total_trades - self.winning_trades
        self.win_rate = self.winning_trades / self.total_trades if self.total_trades > 0 else 0

        wins = [t.pnl for t in closed_trades if t.is_winner]
        losses = [t.pnl for t in closed_trades if not t.is_winner]

        self.avg_win = sum(wins) / len(wins) if wins else 0
        self.avg_loss = sum(losses) / len(losses) if losses else 0
        self.total_pnl = sum(t.pnl for t in closed_trades)
        self.total_pnl_pct = self.total_pnl / self.config.initial_capital * 100

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        self.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        self.expectancy = (
            self.win_rate * self.avg_win +
            (1 - self.win_rate) * self.avg_loss
        )

        if self.equity_curve:
            peak = self.equity_curve[0]
            max_dd = 0
            for equity in self.equity_curve:
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
            self.max_drawdown = max_dd
            self.max_drawdown_pct = (max_dd / peak * 100) if peak > 0 else 0

        if len(self.equity_curve) > 1:
            returns = []
            for i in range(1, len(self.equity_curve)):
                if self.equity_curve[i-1] > 0:
                    ret = (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1]
                    returns.append(ret)
            if returns:
                avg_return = sum(returns) / len(returns)
                std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
                self.sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0

    def summary(self) -> dict[str, Any]:
        """Get summary dict of results."""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "expectancy": round(self.expectancy, 2),
        }


# ---------------------------------------------------------------------------
# Backtest Engine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Main backtesting engine that orchestrates strategy evaluation and trade simulation."""

    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[float] = []
        self.current_capital = self.config.initial_capital

    def run(
        self,
        candles: list[NormalizedCandle],
        symbol: str,
        asset_class: AssetClass = AssetClass.FOREX,
    ) -> BacktestResult:
        """Run backtest on historical candle data."""
        from .trade_simulator import TradeSimulator

        if len(candles) < 50:
            logger.warning("Insufficient data for backtest: %d candles", len(candles))
            return BacktestResult(config=self.config)

        simulator = TradeSimulator(self.config)

        result = BacktestResult(
            config=self.config,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
        )

        self.equity_curve = [self.config.initial_capital]
        self.current_capital = self.config.initial_capital
        self.trades = []

        lookback = 50
        for i in range(lookback, len(candles)):
            window = candles[i - lookback:i + 1]
            current_candle = candles[i]

            # Check existing trades for exits
            for trade in self.trades:
                if trade.is_open:
                    simulator.check_exit(trade, current_candle)

            # Look for new setups (every 5 candles)
            open_count = sum(1 for t in self.trades if t.is_open)
            if i % 5 == 0 and open_count < self.config.max_positions:
                setups = self._find_setups(window, symbol, asset_class)
                for setup in setups:
                    if open_count >= self.config.max_positions:
                        break
                    trade = simulator.create_trade(setup, current_candle)
                    if trade:
                        self.trades.append(trade)
                        open_count += 1

            equity = self._calculate_equity(current_candle)
            self.equity_curve.append(equity)

        # Close remaining open trades
        last_candle = candles[-1]
        for trade in self.trades:
            if trade.is_open:
                simulator.close_trade(trade, last_candle.close, last_candle.timestamp)

        result.trades = self.trades
        result.equity_curve = self.equity_curve
        result.calculate_metrics()

        return result

    def _find_setups(
        self,
        candles: list[NormalizedCandle],
        symbol: str,
        asset_class: AssetClass,
    ) -> list[TradeSetup]:
        """Find trade setups using simplified strategy."""
        setups = []
        if len(candles) < 20:
            return setups

        last = candles[-1]
        prev = candles[-2]

        body = last.close - last.open
        prev_body = prev.close - prev.open
        total_range = last.high - last.low

        if total_range == 0:
            return setups

        body_pct = abs(body) / total_range

        if body > 0 and prev_body > 0 and body_pct > 0.5:
            entry = last.close
            stop_loss = last.low
            risk = entry - stop_loss
            take_profit = entry + risk * self.config.min_risk_reward

            setup = TradeSetup(
                symbol=symbol,
                asset_class=asset_class,
                direction="LONG",
                timeframe="H1",
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_percent=self.config.risk_per_trade,
            )
            setups.append(setup)

        elif body < 0 and prev_body < 0 and body_pct > 0.5:
            entry = last.close
            stop_loss = last.high
            risk = stop_loss - entry
            take_profit = entry - risk * self.config.min_risk_reward

            setup = TradeSetup(
                symbol=symbol,
                asset_class=asset_class,
                direction="SHORT",
                timeframe="H1",
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_percent=self.config.risk_per_trade,
            )
            setups.append(setup)

        return setups

    def _calculate_equity(self, candle: NormalizedCandle) -> float:
        """Calculate current equity including unrealized PnL."""
        equity = self.current_capital
        for trade in self.trades:
            if trade.is_open:
                if trade.direction == "LONG":
                    unrealized = (candle.close - trade.entry_price) * trade.position_size
                else:
                    unrealized = (trade.entry_price - candle.close) * trade.position_size
                equity += unrealized
        return equity
