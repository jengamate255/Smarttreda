from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column, String, Float, DateTime, Text, JSON, Integer, Boolean,
    create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from trading_ai.memory.journal import TradeJournal, FilterCriteria, MarketRegime, SessionType

Base = declarative_base()


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class WeaknessType(str, Enum):
    NEGATIVE_EXPECTANCY = "negative_expectancy"
    LOW_WIN_RATE = "low_win_rate"
    POOR_RISK_REWARD = "poor_risk_reward"
    REGIME_FAILURE = "regime_failure"
    SESSION_FAILURE = "session_failure"
    HIGH_DRAWDOWN = "high_drawdown"
    OVERTRADE = "overtrade"
    TIMING_FAILURE = "timing_failure"
    SPREAD_SENSITIVITY = "spread_sensitivity"
    CLUSTER_LOSS = "cluster_loss"


class HypothesisRecord(Base):
    __tablename__ = "hypotheses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    description = Column(Text, nullable=False)
    parent_strategy = Column(String(64), nullable=False, index=True)
    proposed_change = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False)
    required_test = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default=HypothesisStatus.PROPOSED.value)
    weakness_type = Column(String(32), nullable=False)
    affected_conditions = Column(JSON, default={})
    supporting_evidence = Column(JSON, default={})
    priority_score = Column(Float, default=0.0)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, nullable=True)


@dataclass
class Hypothesis:
    id: str
    description: str
    parent_strategy: str
    proposed_change: str
    rationale: str
    required_test: str
    status: HypothesisStatus
    weakness_type: WeaknessType
    affected_conditions: dict[str, Any] = field(default_factory=dict)
    supporting_evidence: dict[str, Any] = field(default_factory=dict)
    priority_score: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    resolution_notes: str | None = None


@dataclass
class WeaknessReport:
    weakness_type: WeaknessType
    description: str
    affected_conditions: dict[str, Any]
    severity_score: float
    sample_size: int
    supporting_metrics: dict[str, Any]
    suggested_research: str


class ResearchAgent:
    def __init__(self, journal: TradeJournal, db_url: str = "sqlite:///research_agent.db"):
        self.journal = journal
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def analyze_weaknesses(
        self,
        strategy_version: str | None = None,
        min_sample_size: int = 30,
    ) -> list[WeaknessReport]:
        weaknesses: list[WeaknessReport] = []

        criteria = FilterCriteria()
        if strategy_version:
            criteria.strategies = [strategy_version]

        strategy_groups = self._group_by_strategy(criteria)
        trades: list[dict] = []
        for strat, strat_trades in strategy_groups.items():
            trades.extend(strat_trades)
            weaknesses.extend(self._analyze_strategy_weaknesses(strat, strat_trades, min_sample_size))

        regime_groups = self._group_by_regime(criteria, trades)
        for regime, regime_trades in regime_groups.items():
            weaknesses.extend(self._analyze_regime_weaknesses(regime, regime_trades, min_sample_size))

        session_groups = self._group_by_session(criteria, trades)
        for session, session_trades in session_groups.items():
            weaknesses.extend(self._analyze_session_weaknesses(session, session_trades, min_sample_size))

        symbol_groups = self._group_by_symbol(criteria, trades)
        for symbol, symbol_trades in symbol_groups.items():
            weaknesses.extend(self._analyze_symbol_weaknesses(symbol, symbol_trades, min_sample_size))

        weaknesses.sort(key=lambda w: w.severity_score, reverse=True)
        return weaknesses

    def generate_hypotheses(
        self, weaknesses: list[WeaknessReport], parent_strategy: str
    ) -> list[Hypothesis]:
        hypotheses = []
        for weakness in weaknesses:
            hypothesis = self._weakness_to_hypothesis(weakness, parent_strategy)
            if hypothesis:
                hypotheses.append(hypothesis)
        return hypotheses

    def _group_by_strategy(self, base_criteria: FilterCriteria) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        db: Session = self.journal.SessionLocal()
        try:
            from trading_ai.memory.journal import TradeRecord
            query = db.query(TradeRecord)
            query = self.journal._apply_filters(query, base_criteria)
            trades = [self.journal._record_to_dict(r) for r in query.all()]
            for t in trades:
                strat = t["strategy_version"]
                result.setdefault(strat, []).append(t)
        finally:
            db.close()
        return result

    def _group_by_regime(self, base_criteria: FilterCriteria, trades: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for t in trades:
            regime = t["market_regime"]
            result.setdefault(regime, []).append(t)
        return result

    def _group_by_session(self, base_criteria: FilterCriteria, trades: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for t in trades:
            session = t["session"]
            result.setdefault(session, []).append(t)
        return result

    def _group_by_symbol(self, base_criteria: FilterCriteria, trades: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for t in trades:
            symbol = t["symbol"]
            result.setdefault(symbol, []).append(t)
        return result

    def _analyze_strategy_weaknesses(self, strategy: str, trades: list[dict], min_sample: int) -> list[WeaknessReport]:
        weaknesses = []
        if len(trades) < min_sample:
            return weaknesses

        agg = self.journal._aggregate_raw(trades)
        if agg.expectancy < 0:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.NEGATIVE_EXPECTANCY,
                description=f"Strategy {strategy} has negative expectancy ({agg.expectancy:.4f}R)",
                affected_conditions={"strategy": strategy},
                severity_score=abs(agg.expectancy) * (agg.total_trades / 100),
                sample_size=agg.total_trades,
                supporting_metrics={
                    "expectancy": agg.expectancy,
                    "win_rate": agg.win_rate,
                    "profit_factor": agg.profit_factor,
                    "sharpe": agg.sharpe_approx,
                },
                suggested_research=f"Investigate root causes of negative expectancy in {strategy}. "
                    f"Consider regime filtering, entry timing, or exit optimization.",
            ))

        if agg.win_rate < 0.35 and agg.total_trades >= min_sample:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.LOW_WIN_RATE,
                description=f"Strategy {strategy} has unacceptably low win rate ({agg.win_rate:.2%})",
                affected_conditions={"strategy": strategy},
                severity_score=(0.4 - agg.win_rate) * 10,
                sample_size=agg.total_trades,
                supporting_metrics={"win_rate": agg.win_rate, "avg_r": agg.avg_r},
                suggested_research="Evaluate entry quality and signal reliability.",
            ))

        if agg.profit_factor < 1.0 and agg.total_trades >= min_sample:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.POOR_RISK_REWARD,
                description=f"Strategy {strategy} has profit factor below 1.0 ({agg.profit_factor:.4f})",
                affected_conditions={"strategy": strategy},
                severity_score=(1.0 - agg.profit_factor) * 5,
                sample_size=agg.total_trades,
                supporting_metrics={"profit_factor": agg.profit_factor, "avg_r_winners": agg.avg_r_winners, "avg_r_losers": agg.avg_r_losers},
                suggested_research="Review risk-reward ratio and exit management.",
            ))

        if agg.max_drawdown_r > 10:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.HIGH_DRAWDOWN,
                description=f"Strategy {strategy} max drawdown of {agg.max_drawdown_r:.2f}R is excessive",
                affected_conditions={"strategy": strategy},
                severity_score=agg.max_drawdown_r / 5,
                sample_size=agg.total_trades,
                supporting_metrics={"max_drawdown_r": agg.max_drawdown_r},
                suggested_research="Investigate position sizing and risk management.",
            ))

        return weaknesses

    def _analyze_regime_weaknesses(self, regime: str, trades: list[dict], min_sample: int) -> list[WeaknessReport]:
        weaknesses = []
        if len(trades) < min_sample:
            return weaknesses

        agg = self.journal._aggregate_raw(trades)
        if agg.expectancy < 0:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.REGIME_FAILURE,
                description=f"Negative expectancy ({agg.expectancy:.4f}R) in {regime} regime",
                affected_conditions={"regime": regime},
                severity_score=abs(agg.expectancy) * 8,
                sample_size=agg.total_trades,
                supporting_metrics={"expectancy": agg.expectancy, "win_rate": agg.win_rate},
                suggested_research=f"Consider avoiding {regime} regime or adapting entry criteria.",
            ))
        return weaknesses

    def _analyze_session_weaknesses(self, session: str, trades: list[dict], min_sample: int) -> list[WeaknessReport]:
        weaknesses = []
        if len(trades) < min_sample:
            return weaknesses

        agg = self.journal._aggregate_raw(trades)
        if agg.expectancy < 0:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.SESSION_FAILURE,
                description=f"Negative expectancy ({agg.expectancy:.4f}R) during {session} session",
                affected_conditions={"session": session},
                severity_score=abs(agg.expectancy) * 6,
                sample_size=agg.total_trades,
                supporting_metrics={"expectancy": agg.expectancy, "avg_duration": agg.avg_duration_minutes},
                suggested_research=f"Consider reducing exposure or tightening filters during {session}.",
            ))
        return weaknesses

    def _analyze_symbol_weaknesses(self, symbol: str, trades: list[dict], min_sample: int) -> list[WeaknessReport]:
        weaknesses = []
        if len(trades) < min_sample:
            return weaknesses

        agg = self.journal._aggregate_raw(trades)
        if agg.expectancy < 0:
            weaknesses.append(WeaknessReport(
                weakness_type=WeaknessType.TIMING_FAILURE,
                description=f"Negative expectancy ({agg.expectancy:.4f}R) on {symbol}",
                affected_conditions={"symbol": symbol},
                severity_score=abs(agg.expectancy) * 7,
                sample_size=agg.total_trades,
                supporting_metrics={"expectancy": agg.expectancy, "win_rate": agg.win_rate},
                suggested_research=f"Investigate {symbol}-specific characteristics: spread, volatility patterns, session behavior.",
            ))
        return weaknesses

    def _weakness_to_hypothesis(self, weakness: WeaknessReport, parent_strategy: str) -> Hypothesis | None:
        change_map = {
            WeaknessType.NEGATIVE_EXPECTANCY: "Add regime filter to avoid trading in {regime} conditions",
            WeaknessType.LOW_WIN_RATE: "Tighten entry criteria by requiring additional confirmation",
            WeaknessType.POOR_RISK_REWARD: "Adjust TP/SL ratio to minimum 1.5R",
            WeaknessType.REGIME_FAILURE: "Add regime detection filter to disable strategy in {regime}",
            WeaknessType.SESSION_FAILURE: "Restrict trading to sessions with positive expectancy",
            WeaknessType.HIGH_DRAWDOWN: "Reduce position size or add drawdown circuit breaker",
            WeaknessType.TIMING_FAILURE: "Add time-based filter for {symbol} during weak sessions",
        }

        template = change_map.get(weakness.weakness_type, "Investigate and modify strategy parameters")
        proposed_change = template.format(**weakness.affected_conditions)

        return Hypothesis(
            id=str(uuid.uuid4()),
            description=weakness.description,
            parent_strategy=parent_strategy,
            proposed_change=proposed_change,
            rationale=weakness.suggested_research,
            required_test=f"Backtest with {weakness.sample_size}+ trades across multiple regimes and sessions",
            status=HypothesisStatus.PROPOSED,
            weakness_type=weakness.weakness_type,
            affected_conditions=weakness.affected_conditions,
            supporting_evidence=weakness.supporting_metrics,
            priority_score=weakness.severity_score,
        )

    def save_hypothesis(self, hypothesis: Hypothesis) -> str:
        db: Session = self.SessionLocal()
        try:
            record = HypothesisRecord(
                id=hypothesis.id,
                created_at=hypothesis.created_at,
                description=hypothesis.description,
                parent_strategy=hypothesis.parent_strategy,
                proposed_change=hypothesis.proposed_change,
                rationale=hypothesis.rationale,
                required_test=hypothesis.required_test,
                status=hypothesis.status.value,
                weakness_type=hypothesis.weakness_type.value,
                affected_conditions=hypothesis.affected_conditions,
                supporting_evidence=hypothesis.supporting_evidence,
                priority_score=hypothesis.priority_score,
                resolved_at=hypothesis.resolved_at,
                resolution_notes=hypothesis.resolution_notes,
            )
            db.add(record)
            db.commit()
            return record.id
        finally:
            db.close()

    def get_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]:
        db: Session = self.SessionLocal()
        try:
            query = db.query(HypothesisRecord)
            if status:
                query = query.filter(HypothesisRecord.status == status.value)
            records = query.order_by(HypothesisRecord.priority_score.desc()).all()
            return [self._record_to_hypothesis(r) for r in records]
        finally:
            db.close()

    def update_hypothesis_status(
        self, hypothesis_id: str, status: HypothesisStatus, notes: str | None = None
    ):
        db: Session = self.SessionLocal()
        try:
            record = db.query(HypothesisRecord).filter(HypothesisRecord.id == hypothesis_id).first()
            if record:
                record.status = status.value
                record.resolution_notes = notes
                if status in (HypothesisStatus.VALIDATED, HypothesisStatus.REJECTED, HypothesisStatus.ARCHIVED):
                    record.resolved_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()

    def _record_to_hypothesis(self, record: HypothesisRecord) -> Hypothesis:
        return Hypothesis(
            id=record.id,
            description=record.description,
            parent_strategy=record.parent_strategy,
            proposed_change=record.proposed_change,
            rationale=record.rationale,
            required_test=record.required_test,
            status=HypothesisStatus(record.status),
            weakness_type=WeaknessType(record.weakness_type),
            affected_conditions=record.affected_conditions or {},
            supporting_evidence=record.supporting_evidence or {},
            priority_score=record.priority_score,
            created_at=record.created_at,
            resolved_at=record.resolved_at,
            resolution_notes=record.resolution_notes,
        )
