"""Shared data models for backtesting, risk, and evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    PARTIAL = "PARTIAL"
    MANUAL = "MANUAL"
    KILL_SWITCH = "KILL_SWITCH"
    END_OF_DATA = "END_OF_DATA"


class OrderStatus(str, Enum):
    PENDING = auto()
    FILLED = auto()
    PARTIALLY_FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()


@dataclass
class Signal:
    timestamp: datetime
    symbol: str
    side: Side
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeRecord:
    trade_id: int
    symbol: str
    side: Side
    entry_time: datetime
    exit_time: datetime | None = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    volume: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: ExitReason = ExitReason.MANUAL
    commission: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpenPosition:
    trade_id: int
    symbol: str
    side: Side
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    trailing_stop: float | None = None
    trailing_stop_distance: float | None = None
    volume: float = 0.0
    initial_volume: float = 0.0
    bars_held: int = 0
    mae: float = 0.0
    mfe: float = 0.0
    current_price: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    average_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    recovery_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    average_bars_held: float = 0.0
    total_commission: float = 0.0
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    initial_capital: float = 0.0
    final_equity: float = 0.0
    return_pct: float = 0.0
    annualized_return: float = 0.0
    mae_stats: dict[str, float] = field(default_factory=dict)
    mfe_stats: dict[str, float] = field(default_factory=dict)
    exposure_stats: dict[str, float] = field(default_factory=dict)
    segment_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class RiskApproval:
    approved: bool = True
    position_size: float = 0.0
    risk_amount: float = 0.0
    risk_reward: float = 0.0
    margin_required: float = 0.0
    reason: str = "Approved"
    adjustments: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskRejection:
    approved: bool = False
    reason: str = ""
    violations: list[str] = field(default_factory=list)


@dataclass
class BacktestConfig:
    initial_capital: float = 10000.0
    spread_pips: float = 1.5
    spread_model: str = "fixed"
    slippage_pips: float = 0.5
    slippage_model: str = "fixed"
    commission_per_lot: float = 7.0
    commission_model: str = "per_lot"
    risk_per_trade: float = 0.005
    max_risk_per_trade: float = 0.02
    max_daily_loss: float = 0.03
    max_drawdown: float = 0.10
    max_open_positions: int = 5
    max_correlated_exposure: float = 0.06
    min_risk_reward: float = 3.0
    use_trailing_stop: bool = False
    trailing_stop_pips: float = 20.0
    pip_value: float = 0.0001
    lot_size: float = 100000.0
    margin_rate: float = 100.0
    leverage: int = 100
    enable_partial_closes: bool = False
    partial_close_pct: float = 0.5
    seed: int | None = None
