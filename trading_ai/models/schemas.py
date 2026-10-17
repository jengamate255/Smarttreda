"""Pydantic schemas for data transfer and validation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class SignalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"


class Signal(BaseModel):
    signal_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    strategy_id: str = ""
    symbol: str
    direction: str = "BUY"
    order_type: str = "LIMIT"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    risk_reward: float = Field(default=0.0, ge=0)
    risk_reward_ratio: float = Field(default=0.0, ge=0)
    lot_size: float = Field(default=0.0, ge=0)
    market_regime: str = "UNKNOWN"
    timeframe: str = "M15"
    strategy_version: int = 0
    reasoning: list[str] = Field(default_factory=list)
    pips_risk: float = Field(default=0.0, ge=0)
    pips_reward: float = Field(default=0.0, ge=0)
    entry_zone_low: float = Field(default=0.0)
    entry_zone_high: float = Field(default=0.0)
    status: str = "PROPOSED"
    timestamp: Optional[datetime] = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: Any) -> None:
        if self.risk_reward_ratio == 0.0 and self.risk_reward > 0:
            self.risk_reward_ratio = self.risk_reward
        elif self.risk_reward == 0.0 and self.risk_reward_ratio > 0:
            self.risk_reward = self.risk_reward_ratio
        if self.risk_reward == 0.0:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.take_profit - self.entry_price)
            if risk > 0:
                rr = reward / risk
                self.risk_reward = rr
                self.risk_reward_ratio = rr


class TradeResult(BaseModel):
    trade_id: UUID = Field(default_factory=uuid4)
    signal_id: Optional[str] = None
    symbol: str
    timeframe: str
    session: str
    market_regime: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: Optional[float] = None
    entry_time: datetime
    exit_time: Optional[datetime] = None
    spread_at_entry: int = 0
    volatility_at_entry: float = 0.0
    r_multiple: Optional[float] = None
    mae: Optional[float] = None
    mfe: Optional[float] = None
    duration_seconds: Optional[int] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    commission: float = 0.0
    slippage: float = 0.0
    strategy_version: int = 0
    features: dict[str, Any] = Field(default_factory=dict)
    reasoning: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class PerformanceMetricsSchema(BaseModel):
    strategy_version: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    average_r_multiple: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0
    average_win_r: float = 0.0
    average_loss_r: float = 0.0
    payoff_ratio: float = 0.0
    total_pnl: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0
    profit_per_day: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    average_trade_duration_seconds: float = 0.0
    monte_carlo_confidence_95: Optional[float] = None
    monte_carlo_confidence_99: Optional[float] = None

    model_config = {"from_attributes": True}


class HypothesisSchema(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    description: str
    parent_strategy_id: str = ""
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    required_test: str = ""
    status: str = "PROPOSED"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    experiment_id: Optional[str] = None

    model_config = {"from_attributes": True}


class ExperimentSchema(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    hypothesis_id: str = ""
    parent_strategy_id: str = ""
    candidate_strategy_id: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    train_metrics: Optional[dict[str, Any]] = None
    validation_metrics: Optional[dict[str, Any]] = None
    test_metrics: Optional[dict[str, Any]] = None
    monte_carlo_results: Optional[dict[str, Any]] = None
    robustness_score: Optional[float] = None
    gatekeeper_decision: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    git_commit: Optional[str] = None
    dataset_version: Optional[str] = None

    model_config = {"from_attributes": True}


class StrategyGenomeSchema(BaseModel):
    strategy_id: str
    parent_id: Optional[str] = None
    version: int = 1
    name: str = ""
    description: str = ""
    market: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)
    risk_rules: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = False
    is_live: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    git_commit: Optional[str] = None
    dataset_version: Optional[str] = None

    model_config = {"from_attributes": True}
