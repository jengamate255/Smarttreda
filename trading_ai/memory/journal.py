from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text, JSON, Boolean,
    create_engine, Index, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"


class SessionType(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"
    LONDON_NY_OVERLAP = "london_ny_overlap"
    OFF_HOURS = "off_hours"


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    MANUAL = "manual"
    SIGNAL_REVERSAL = "signal_reversal"
    MARGIN_CALL = "margin_call"


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"


class TradeRecord(Base):
    __tablename__ = "trade_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, nullable=False, index=True)
    strategy_version = Column(String(64), nullable=False, index=True)
    market_regime = Column(String(32), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    session = Column(String(32), nullable=False, index=True)
    direction = Column(String(4), nullable=False)

    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    spread_at_entry = Column(Float, nullable=False)
    volatility_at_entry = Column(Float, nullable=False)

    entry_reason = Column(Text, nullable=False)
    features_snapshot = Column(JSON, nullable=False, default={})

    outcome = Column(String(16), nullable=False, index=True)
    exit_price = Column(Float, nullable=False)
    exit_time = Column(DateTime, nullable=False)
    exit_reason = Column(String(32), nullable=False)

    r_multiple = Column(Float, nullable=False)
    mae = Column(Float, nullable=False)
    mfe = Column(Float, nullable=False)
    duration_minutes = Column(Float, nullable=False)
    pnl_pips = Column(Float, nullable=False)
    pnl_currency = Column(Float, nullable=False)

    __table_args__ = (
        Index("ix_trade_composite", "symbol", "strategy_version", "market_regime", "session"),
        Index("ix_trade_date_range", "timestamp", "symbol"),
    )


@dataclass
class TradeEntry:
    timestamp: datetime
    strategy_version: str
    market_regime: MarketRegime
    symbol: str
    timeframe: str
    session: SessionType
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    spread_at_entry: float
    volatility_at_entry: float
    entry_reason: str
    features_snapshot: dict[str, Any] = field(default_factory=dict)
    outcome: TradeOutcome = TradeOutcome.WIN
    exit_price: float = 0.0
    exit_time: datetime | None = None
    exit_reason: ExitReason = ExitReason.TAKE_PROFIT
    r_multiple: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0
    duration_minutes: float = 0.0
    pnl_pips: float = 0.0
    pnl_currency: float = 0.0


class FilterCriteria:
    def __init__(self):
        self.symbols: list[str] | None = None
        self.strategies: list[str] | None = None
        self.regimes: list[MarketRegime] | None = None
        self.sessions: list[SessionType] | None = None
        self.timeframes: list[str] | None = None
        self.date_from: datetime | None = None
        self.date_to: datetime | None = None
        self.outcomes: list[TradeOutcome] | None = None
        self.min_r: float | None = None
        self.max_r: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class AggregationResult:
    total_trades: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: float
    avg_r: float
    avg_r_winners: float
    avg_r_losers: float
    expectancy: float
    profit_factor: float
    avg_duration_minutes: float
    max_drawdown_r: float
    sharpe_approx: float


class TradeJournal:
    def __init__(self, db_url: str = "sqlite:///trade_journal.db"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def record_trade(self, trade: TradeEntry) -> str:
        rec = TradeRecord(
            id=str(uuid.uuid4()),
            timestamp=trade.timestamp,
            strategy_version=trade.strategy_version,
            market_regime=trade.market_regime.value,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            session=trade.session.value,
            direction=trade.direction,
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            spread_at_entry=trade.spread_at_entry,
            volatility_at_entry=trade.volatility_at_entry,
            entry_reason=trade.entry_reason,
            features_snapshot=trade.features_snapshot,
            outcome=trade.outcome.value,
            exit_price=trade.exit_price,
            exit_time=trade.exit_time or trade.timestamp,
            exit_reason=trade.exit_reason.value,
            r_multiple=trade.r_multiple,
            mae=trade.mae,
            mfe=trade.mfe,
            duration_minutes=trade.duration_minutes,
            pnl_pips=trade.pnl_pips,
            pnl_currency=trade.pnl_currency,
        )
        db: Session = self.SessionLocal()
        try:
            db.add(rec)
            db.commit()
            return rec.id
        finally:
            db.close()

    def _apply_filters(self, query, criteria: FilterCriteria):
        if criteria.symbols:
            query = query.filter(TradeRecord.symbol.in_(criteria.symbols))
        if criteria.strategies:
            query = query.filter(TradeRecord.strategy_version.in_(criteria.strategies))
        if criteria.regimes:
            regime_vals = [r.value for r in criteria.regimes]
            query = query.filter(TradeRecord.market_regime.in_(regime_vals))
        if criteria.sessions:
            session_vals = [s.value for s in criteria.sessions]
            query = query.filter(TradeRecord.session.in_(session_vals))
        if criteria.timeframes:
            query = query.filter(TradeRecord.timeframe.in_(criteria.timeframes))
        if criteria.date_from:
            query = query.filter(TradeRecord.timestamp >= criteria.date_from)
        if criteria.date_to:
            query = query.filter(TradeRecord.timestamp <= criteria.date_to)
        if criteria.outcomes:
            outcome_vals = [o.value for o in criteria.outcomes]
            query = query.filter(TradeRecord.outcome.in_(outcome_vals))
        if criteria.min_r is not None:
            query = query.filter(TradeRecord.r_multiple >= criteria.min_r)
        if criteria.max_r is not None:
            query = query.filter(TradeRecord.r_multiple <= criteria.max_r)
        return query

    def query_trades(self, criteria: FilterCriteria) -> list[dict]:
        db: Session = self.SessionLocal()
        try:
            query = db.query(TradeRecord)
            query = self._apply_filters(query, criteria)
            query = query.order_by(TradeRecord.timestamp)
            return [self._record_to_dict(r) for r in query.all()]
        finally:
            db.close()

    def aggregate(self, criteria: FilterCriteria) -> AggregationResult:
        trades = self.query_trades(criteria)
        if not trades:
            return AggregationResult(
                total_trades=0, win_count=0, loss_count=0, breakeven_count=0,
                win_rate=0.0, avg_r=0.0, avg_r_winners=0.0, avg_r_losers=0.0,
                expectancy=0.0, profit_factor=0.0, avg_duration_minutes=0.0,
                max_drawdown_r=0.0, sharpe_approx=0.0,
            )

        wins = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]
        breakevens = [t for t in trades if t["outcome"] == "breakeven"]

        total = len(trades)
        win_rate = len(wins) / total if total > 0 else 0.0
        avg_r = sum(t["r_multiple"] for t in trades) / total
        avg_r_winners = sum(t["r_multiple"] for t in wins) / len(wins) if wins else 0.0
        avg_r_losers = sum(t["r_multiple"] for t in losses) / len(losses) if losses else 0.0

        gross_profit = sum(t["r_multiple"] for t in wins)
        gross_loss = abs(sum(t["r_multiple"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        expectancy = avg_r

        r_values = [t["r_multiple"] for t in trades]
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in r_values:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        if len(r_values) > 1:
            mean_r = sum(r_values) / len(r_values)
            variance = sum((r - mean_r) ** 2 for r in r_values) / (len(r_values) - 1)
            std_r = variance ** 0.5
            sharpe_approx = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe_approx = 0.0

        avg_duration = sum(t["duration_minutes"] for t in trades) / total

        return AggregationResult(
            total_trades=total,
            win_count=len(wins),
            loss_count=len(losses),
            breakeven_count=len(breakevens),
            win_rate=round(win_rate, 4),
            avg_r=round(avg_r, 4),
            avg_r_winners=round(avg_r_winners, 4),
            avg_r_losers=round(avg_r_losers, 4),
            expectancy=round(expectancy, 4),
            profit_factor=round(profit_factor, 4),
            avg_duration_minutes=round(avg_duration, 2),
            max_drawdown_r=round(max_dd, 4),
            sharpe_approx=round(sharpe_approx, 4),
        )

    def group_by(
        self, group_field: str, criteria: FilterCriteria
    ) -> dict[str, AggregationResult]:
        valid_fields = {
            "symbol", "strategy_version", "market_regime", "session", "timeframe", "outcome"
        }
        if group_field not in valid_fields:
            raise ValueError(f"group_field must be one of {valid_fields}")

        db: Session = self.SessionLocal()
        try:
            query = db.query(TradeRecord)
            query = self._apply_filters(query, criteria)
            all_records = [self._record_to_dict(r) for r in query.all()]
        finally:
            db.close()

        groups: dict[str, list[dict]] = {}
        for rec in all_records:
            key = rec.get(group_field, "unknown")
            groups.setdefault(key, []).append(rec)

        results = {}
        for key, group_trades in groups.items():
            group_criteria = FilterCriteria()
            group_criteria.symbols = [t["symbol"] for t in group_trades]
            group_criteria.date_from = criteria.date_from
            group_criteria.date_to = criteria.date_to
            results[key] = self._aggregate_raw(group_trades)
        return results

    def _aggregate_raw(self, trades: list[dict]) -> AggregationResult:
        if not trades:
            return AggregationResult(
                total_trades=0, win_count=0, loss_count=0, breakeven_count=0,
                win_rate=0.0, avg_r=0.0, avg_r_winners=0.0, avg_r_losers=0.0,
                expectancy=0.0, profit_factor=0.0, avg_duration_minutes=0.0,
                max_drawdown_r=0.0, sharpe_approx=0.0,
            )

        wins = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]
        breakevens = [t for t in trades if t["outcome"] == "breakeven"]
        total = len(trades)
        win_rate = len(wins) / total if total > 0 else 0.0
        avg_r = sum(t["r_multiple"] for t in trades) / total
        avg_r_winners = sum(t["r_multiple"] for t in wins) / len(wins) if wins else 0.0
        avg_r_losers = sum(t["r_multiple"] for t in losses) / len(losses) if losses else 0.0
        gross_profit = sum(t["r_multiple"] for t in wins)
        gross_loss = abs(sum(t["r_multiple"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy = avg_r

        r_values = [t["r_multiple"] for t in trades]
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in r_values:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        if len(r_values) > 1:
            mean_r = sum(r_values) / len(r_values)
            variance = sum((r - mean_r) ** 2 for r in r_values) / (len(r_values) - 1)
            std_r = variance ** 0.5
            sharpe_approx = mean_r / std_r if std_r > 0 else 0.0
        else:
            sharpe_approx = 0.0

        avg_duration = sum(t["duration_minutes"] for t in trades) / total

        return AggregationResult(
            total_trades=total,
            win_count=len(wins),
            loss_count=len(losses),
            breakeven_count=len(breakevens),
            win_rate=round(win_rate, 4),
            avg_r=round(avg_r, 4),
            avg_r_winners=round(avg_r_winners, 4),
            avg_r_losers=round(avg_r_losers, 4),
            expectancy=round(expectancy, 4),
            profit_factor=round(profit_factor, 4),
            avg_duration_minutes=round(avg_duration, 2),
            max_drawdown_r=round(max_dd, 4),
            sharpe_approx=round(sharpe_approx, 4),
        )

    def natural_language_query(self, query: str) -> list[dict]:
        criteria = self._parse_natural_query(query)
        return self.query_trades(criteria)

    def _parse_natural_query(self, query: str) -> FilterCriteria:
        criteria = FilterCriteria()
        q = query.lower()

        symbol_map = {
            "eurusd": "EURUSD", "gbpusd": "GBPUSD", "usdjpy": "USDJPY",
            "audusd": "AUDUSD", "usdcad": "USDCAD", "nzdusd": "NZDUSD",
            "usdchf": "USDCHF", "eurjpy": "EURJPY", "gbpjpy": "GBPJPY",
        }
        found_symbols = []
        for key, val in symbol_map.items():
            if key in q:
                found_symbols.append(val)
        if found_symbols:
            criteria.symbols = found_symbols

        regime_map = {
            "trending up": MarketRegime.TRENDING_UP,
            "trending down": MarketRegime.TRENDING_DOWN,
            "ranging": MarketRegime.RANGING,
            "high volatility": MarketRegime.HIGH_VOLATILITY,
            "low volatility": MarketRegime.LOW_VOLATILITY,
            "breakout": MarketRegime.BREAKOUT,
        }
        for key, val in regime_map.items():
            if key in q:
                criteria.regimes = [val]
                break

        session_map = {
            "asian": SessionType.ASIAN,
            "london": SessionType.LONDON,
            "new york": SessionType.NEW_YORK,
            "overlap": SessionType.LONDON_NY_OVERLAP,
            "off hours": SessionType.OFF_HOURS,
        }
        for key, val in session_map.items():
            if key in q:
                criteria.sessions = [val]
                break

        return criteria

    def _record_to_dict(self, rec: TradeRecord) -> dict:
        return {
            "id": rec.id,
            "timestamp": rec.timestamp.isoformat(),
            "strategy_version": rec.strategy_version,
            "market_regime": rec.market_regime,
            "symbol": rec.symbol,
            "timeframe": rec.timeframe,
            "session": rec.session,
            "direction": rec.direction,
            "entry_price": rec.entry_price,
            "stop_loss": rec.stop_loss,
            "take_profit": rec.take_profit,
            "spread_at_entry": rec.spread_at_entry,
            "volatility_at_entry": rec.volatility_at_entry,
            "entry_reason": rec.entry_reason,
            "features_snapshot": rec.features_snapshot,
            "outcome": rec.outcome,
            "exit_price": rec.exit_price,
            "exit_time": rec.exit_time.isoformat(),
            "exit_reason": rec.exit_reason,
            "r_multiple": rec.r_multiple,
            "mae": rec.mae,
            "mfe": rec.mfe,
            "duration_minutes": rec.duration_minutes,
            "pnl_pips": rec.pnl_pips,
            "pnl_currency": rec.pnl_currency,
        }

    def export_trades(self, criteria: FilterCriteria, filepath: str) -> int:
        trades = self.query_trades(criteria)
        with open(filepath, "w") as f:
            json.dump(trades, f, indent=2)
        return len(trades)
