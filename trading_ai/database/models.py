"""SQLAlchemy ORM models for the trading platform."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


class ExperimentStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle"),
        Index("ix_candle_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    tick_volume: Mapped[int] = mapped_column(Integer, default=0)
    spread: Mapped[int] = mapped_column(Integer, default=0)


class TradeRecord(Base):
    __tablename__ = "trade_records"
    __table_args__ = (
        Index("ix_trade_strategy", "strategy_version"),
        Index("ix_trade_symbol_tf", "symbol", "timeframe"),
        Index("ix_trade_entry_time", "entry_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    session: Mapped[str] = mapped_column(String(16), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)

    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    spread_at_entry: Mapped[int] = mapped_column(Integer, default=0)
    volatility_at_entry: Mapped[float] = mapped_column(Float, default=0.0)

    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mae: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mfe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)

    features_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reasoning_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class StrategyGenome(Base):
    __tablename__ = "strategy_genomes"
    __table_args__ = (
        Index("ix_strategy_active", "is_active"),
        Index("ix_strategy_version", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    strategy_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    market_json: Mapped[str] = mapped_column(Text, default="[]")
    timeframes_json: Mapped[str] = mapped_column(Text, default="[]")
    entry_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    exit_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    filters_json: Mapped[str] = mapped_column(Text, default="{}")

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    git_commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        "Hypothesis", back_populates="parent_strategy", lazy="selectin"
    )


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    __table_args__ = (
        Index("ix_hypothesis_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parent_strategy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("strategy_genomes.id"), nullable=False
    )
    proposed_change_json: Mapped[str] = mapped_column(Text, default="{}")
    rationale: Mapped[str] = mapped_column(Text, default="")
    required_test: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(16), default=ExperimentStatus.PROPOSED.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    parent_strategy: Mapped["StrategyGenome"] = relationship(
        "StrategyGenome", back_populates="hypotheses"
    )
    experiment: Mapped[Optional["Experiment"]] = relationship(
        "Experiment", back_populates="hypothesis", uselist=False, lazy="selectin"
    )


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiment_hypothesis", "hypothesis_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    hypothesis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hypotheses.id"), nullable=False
    )
    parent_strategy_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_strategy_id: Mapped[str] = mapped_column(String(36), nullable=False)

    config_json: Mapped[str] = mapped_column(Text, default="{}")
    train_metrics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_metrics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_metrics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    monte_carlo_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    robustness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gatekeeper_decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    git_commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    hypothesis: Mapped["Hypothesis"] = relationship("Hypothesis", back_populates="experiment")


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        Index("ix_research_status_priority", "status", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyLineage(Base):
    __tablename__ = "strategy_lineage"
    __table_args__ = (
        Index("ix_lineage_ancestor", "ancestor_id"),
        Index("ix_lineage_descendant", "descendant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ancestor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    descendant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    mutation_description: Mapped[str] = mapped_column(Text, default="")
