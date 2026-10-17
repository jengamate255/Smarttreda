"""Event types and data classes for the event-driven architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class EventType(Enum):
    MARKET_DATA_UPDATED = "market_data_updated"
    MARKET_STATE_UPDATED = "market_state_updated"
    SIGNAL_GENERATED = "signal_generated"
    RISK_CHECKED = "risk_checked"
    TRADE_EXECUTED = "trade_executed"
    TRADE_CLOSED = "trade_closed"
    PERFORMANCE_UPDATED = "performance_updated"
    RESEARCH_TRIGGERED = "research_triggered"
    EXPERIMENT_CREATED = "experiment_created"
    EXPERIMENT_COMPLETED = "experiment_completed"
    STRATEGY_PROMOTED = "strategy_promoted"
    STRATEGY_REJECTED = "strategy_rejected"


@dataclass(frozen=True)
class BaseEvent:
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType = EventType.MARKET_DATA_UPDATED


@dataclass(frozen=True)
class MarketDataUpdated(BaseEvent):
    event_type: EventType = field(default=EventType.MARKET_DATA_UPDATED, init=False)
    symbol: str = ""
    timeframe: str = ""
    candle_count: int = 0


@dataclass(frozen=True)
class MarketStateUpdated(BaseEvent):
    event_type: EventType = field(default=EventType.MARKET_STATE_UPDATED, init=False)
    symbol: str = ""
    regime: str = ""
    volatility: float = 0.0
    trend_strength: float = 0.0
    session: str = ""


@dataclass(frozen=True)
class SignalGenerated(BaseEvent):
    event_type: EventType = field(default=EventType.SIGNAL_GENERATED, init=False)
    signal_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    direction: str = ""
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0
    strategy_version: int = 0
    reasoning: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskChecked(BaseEvent):
    event_type: EventType = field(default=EventType.RISK_CHECKED, init=False)
    signal_id: UUID = field(default_factory=uuid4)
    approved: bool = False
    position_size: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class TradeExecuted(BaseEvent):
    event_type: EventType = field(default=EventType.TRADE_EXECUTED, init=False)
    trade_id: UUID = field(default_factory=uuid4)
    signal_id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size: float = 0.0
    strategy_version: int = 0


@dataclass(frozen=True)
class TradeClosed(BaseEvent):
    event_type: EventType = field(default=EventType.TRADE_CLOSED, init=False)
    trade_id: UUID = field(default_factory=uuid4)
    exit_price: float = 0.0
    pnl: float = 0.0
    r_multiple: float = 0.0
    exit_reason: str = ""
    duration_seconds: int = 0
    mae: float = 0.0
    mfe: float = 0.0


@dataclass(frozen=True)
class PerformanceUpdated(BaseEvent):
    event_type: EventType = field(default=EventType.PERFORMANCE_UPDATED, init=False)
    strategy_version: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    expectancy: float = 0.0


@dataclass(frozen=True)
class ResearchTriggered(BaseEvent):
    event_type: EventType = field(default=EventType.RESEARCH_TRIGGERED, init=False)
    task_id: UUID = field(default_factory=uuid4)
    task_type: str = ""
    symbol: str = ""
    regime: str = ""
    priority: int = 0
    question: str = ""


@dataclass(frozen=True)
class ExperimentCreated(BaseEvent):
    event_type: EventType = field(default=EventType.EXPERIMENT_CREATED, init=False)
    experiment_id: UUID = field(default_factory=uuid4)
    hypothesis_id: UUID = field(default_factory=uuid4)
    parent_strategy_id: UUID = field(default_factory=uuid4)
    candidate_strategy_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class ExperimentCompleted(BaseEvent):
    event_type: EventType = field(default=EventType.EXPERIMENT_COMPLETED, init=False)
    experiment_id: UUID = field(default_factory=uuid4)
    hypothesis_id: UUID = field(default_factory=uuid4)
    decision: str = ""
    robustness_score: float = 0.0
    test_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyPromoted(BaseEvent):
    event_type: EventType = field(default=EventType.STRATEGY_PROMOTED, init=False)
    strategy_id: UUID = field(default_factory=uuid4)
    new_version: int = 0
    promoted_from: str = ""
    reason: str = ""


@dataclass(frozen=True)
class StrategyRejected(BaseEvent):
    event_type: EventType = field(default=EventType.STRATEGY_REJECTED, init=False)
    strategy_id: UUID = field(default_factory=uuid4)
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
