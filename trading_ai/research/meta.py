from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column, String, Float, DateTime, Text, JSON, Integer,
    create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from trading_ai.memory.journal import TradeJournal, FilterCriteria, AggregationResult
from trading_ai.research.agent import ResearchAgent, Hypothesis, HypothesisStatus, WeaknessType

Base = declarative_base()


class TaskType(str, Enum):
    INVESTIGATE_WEAKNESS = "investigate_weakness"
    VALIDATE_HYPOTHESIS = "validate_hypothesis"
    COMPARE_STRATEGIES = "compare_strategies"
    RETIRE_STRATEGY = "retire_strategy"
    OPTIMIZE_PARAMETERS = "optimize_parameters"
    TEST_REGIME_FILTER = "test_regime_filter"
    TEST_SESSION_FILTER = "test_session_filter"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResearchTaskRecord(Base):
    __tablename__ = "research_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    task_type = Column(String(32), nullable=False, index=True)
    symbol = Column(String(16), nullable=True, index=True)
    regime = Column(String(32), nullable=True, index=True)
    strategy = Column(String(64), nullable=False, index=True)
    priority = Column(String(16), nullable=False, index=True)
    question = Column(Text, nullable=False)
    hypothesis_id = Column(String(36), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    assigned_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    result_summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, default={})


@dataclass
class ResearchTask:
    id: str
    task_type: TaskType
    symbol: str | None
    regime: str | None
    strategy: str
    priority: TaskPriority
    question: str
    hypothesis_id: str | None = None
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    assigned_at: datetime | None = None
    completed_at: datetime | None = None
    result_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MetaResearchAgent:
    def __init__(
        self,
        journal: TradeJournal,
        research_agent: ResearchAgent,
        db_url: str = "sqlite:///meta_research.db",
    ):
        self.journal = journal
        self.research_agent = research_agent
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def identify_strategies_needing_research(
        self, min_trades: int = 50
    ) -> list[dict[str, Any]]:
        criteria = FilterCriteria()
        strategy_agg = self.journal.group_by("strategy_version", criteria)

        needing_research = []
        for strat, agg in strategy_agg.items():
            reasons = []
            if agg.total_trades < min_trades:
                reasons.append(f"Insufficient data: {agg.total_trades}/{min_trades} trades")
            if agg.expectancy < 0:
                reasons.append(f"Negative expectancy: {agg.expectancy:.4f}R")
            if agg.profit_factor < 1.0:
                reasons.append(f"Profit factor below 1.0: {agg.profit_factor:.4f}")
            if agg.max_drawdown_r > 8:
                reasons.append(f"Excessive drawdown: {agg.max_drawdown_r:.2f}R")
            if agg.sharpe_approx < 0.5 and agg.total_trades >= min_trades:
                reasons.append(f"Low risk-adjusted returns: Sharpe {agg.sharpe_approx:.4f}")

            if reasons:
                needing_research.append({
                    "strategy": strat,
                    "total_trades": agg.total_trades,
                    "expectancy": agg.expectancy,
                    "reasons": reasons,
                    "urgency": self._calculate_urgency(agg),
                })

        needing_research.sort(key=lambda x: x["urgency"], reverse=True)
        return needing_research

    def prioritize_weaknesses(
        self, weaknesses: list, min_investigation_value: float = 0.3
    ) -> list[dict[str, Any]]:
        prioritized = []
        for w in weaknesses:
            score = self._score_weakness(w)
            if score >= min_investigation_value:
                prioritized.append({
                    "weakness": w,
                    "priority_score": score,
                    "estimated_value": self._estimate_investigation_value(w),
                    "recommended_action": self._recommend_action(w),
                })

        prioritized.sort(key=lambda x: x["priority_score"], reverse=True)
        return prioritized

    def decide_experiments(
        self, hypotheses: list[Hypothesis], max_concurrent: int = 3
    ) -> list[ResearchTask]:
        tasks = []
        active = self._get_active_tasks()
        slots_available = max_concurrent - len(active)

        if slots_available <= 0:
            return tasks

        scored = [(h, self._score_hypothesis(h)) for h in hypotheses]
        scored.sort(key=lambda x: x[1], reverse=True)

        for hypothesis, score in scored[:slots_available]:
            task = ResearchTask(
                id=str(uuid.uuid4()),
                task_type=TaskType.VALIDATE_HYPOTHESIS,
                symbol=hypothesis.affected_conditions.get("symbol"),
                regime=hypothesis.affected_conditions.get("regime"),
                strategy=hypothesis.parent_strategy,
                priority=self._score_to_priority(score),
                question=f"Validate hypothesis: {hypothesis.description}",
                hypothesis_id=hypothesis.id,
                metadata={
                    "proposed_change": hypothesis.proposed_change,
                    "required_test": hypothesis.required_test,
                    "priority_score": score,
                },
            )
            tasks.append(task)
            self._save_task(task)

        return tasks

    def decide_retirements(
        self, strategy_versions: list[str] | None = None, lookback_days: int = 90
    ) -> list[ResearchTask]:
        tasks = []
        criteria = FilterCriteria()

        if strategy_versions:
            criteria.strategies = strategy_versions

        strategy_agg = self.journal.group_by("strategy_version", criteria)

        for strat, agg in strategy_agg.items():
            if self._should_retire(strat, agg, lookback_days):
                task = ResearchTask(
                    id=str(uuid.uuid4()),
                    task_type=TaskType.RETIRE_STRATEGY,
                    symbol=None,
                    regime=None,
                    strategy=strat,
                    priority=TaskPriority.HIGH,
                    question=f"Should strategy {strat} be retired? "
                        f"Expectancy: {agg.expectancy:.4f}R, "
                        f"PF: {agg.profit_factor:.4f}, "
                        f"DD: {agg.max_drawdown_r:.2f}R, "
                        f"Trades: {agg.total_trades}",
                    metadata={
                        "recommendation": "retire",
                        "reason": self._retirement_reason(agg),
                        "metrics": {
                            "expectancy": agg.expectancy,
                            "profit_factor": agg.profit_factor,
                            "max_drawdown": agg.max_drawdown_r,
                            "total_trades": agg.total_trades,
                        },
                    },
                )
                tasks.append(task)
                self._save_task(task)

        return tasks

    def _calculate_urgency(self, agg: AggregationResult) -> float:
        urgency = 0.0
        if agg.expectancy < -0.5:
            urgency += 3.0
        elif agg.expectancy < 0:
            urgency += 1.5

        if agg.profit_factor < 0.8:
            urgency += 2.0
        elif agg.profit_factor < 1.0:
            urgency += 1.0

        if agg.max_drawdown_r > 15:
            urgency += 3.0
        elif agg.max_drawdown_r > 10:
            urgency += 1.5

        if agg.total_trades < 30:
            urgency += 1.0

        return urgency

    def _score_weakness(self, weakness) -> float:
        base_score = weakness.severity_score if hasattr(weakness, "severity_score") else 0.5
        sample_size = weakness.sample_size if hasattr(weakness, "sample_size") else 0

        if sample_size < 20:
            confidence_penalty = 0.5
        elif sample_size < 50:
            confidence_penalty = 0.8
        else:
            confidence_penalty = 1.0

        return min(base_score * confidence_penalty, 1.0)

    def _estimate_investigation_value(self, weakness) -> float:
        if hasattr(weakness, "supporting_evidence"):
            metrics = weakness.supporting_evidence
            if isinstance(metrics, dict):
                expectancy = abs(metrics.get("expectancy", 0))
                return min(expectancy * 2, 1.0)
        return 0.5

    def _recommend_action(self, weakness) -> str:
        if hasattr(weakness, "weakness_type"):
            wt = weakness.weakness_type
            if wt == WeaknessType.NEGATIVE_EXPECTANCY or wt == "negative_expectancy":
                return "Add regime filter or disable strategy"
            elif wt == WeaknessType.LOW_WIN_RATE or wt == "low_win_rate":
                return "Tighten entry criteria"
            elif wt == WeaknessType.SESSION_FAILURE or wt == "session_failure":
                return "Add session time filter"
        return "Investigate and modify"

    def _score_hypothesis(self, hypothesis: Hypothesis) -> float:
        score = hypothesis.priority_score
        if hypothesis.weakness_type in (WeaknessType.NEGATIVE_EXPECTANCY, WeaknessType.HIGH_DRAWDOWN):
            score *= 1.5
        if hypothesis.affected_conditions.get("regime"):
            score *= 1.2
        return min(score, 10.0)

    def _score_to_priority(self, score: float) -> TaskPriority:
        if score >= 8.0:
            return TaskPriority.CRITICAL
        elif score >= 5.0:
            return TaskPriority.HIGH
        elif score >= 2.0:
            return TaskPriority.MEDIUM
        return TaskPriority.LOW

    def _should_retire(self, strategy: str, agg: AggregationResult, lookback_days: int) -> bool:
        if agg.total_trades < 20:
            return False
        if agg.expectancy < -0.3 and agg.profit_factor < 0.9:
            return True
        if agg.expectancy < -0.5:
            return True
        if agg.max_drawdown_r > 15 and agg.expectancy < 0:
            return True
        return False

    def _retirement_reason(self, agg: AggregationResult) -> str:
        reasons = []
        if agg.expectancy < -0.3:
            reasons.append(f"negative expectancy ({agg.expectancy:.4f}R)")
        if agg.profit_factor < 0.9:
            reasons.append(f"low profit factor ({agg.profit_factor:.4f})")
        if agg.max_drawdown_r > 15:
            reasons.append(f"excessive drawdown ({agg.max_drawdown_r:.2f}R)")
        return "; ".join(reasons) if reasons else "underperformance"

    def _get_active_tasks(self) -> list[ResearchTask]:
        db: Session = self.SessionLocal()
        try:
            records = db.query(ResearchTaskRecord).filter(
                ResearchTaskRecord.status.in_(["pending", "in_progress"])
            ).all()
            return [self._record_to_task(r) for r in records]
        finally:
            db.close()

    def _save_task(self, task: ResearchTask):
        db: Session = self.SessionLocal()
        try:
            record = ResearchTaskRecord(
                id=task.id,
                created_at=task.created_at,
                task_type=task.task_type.value,
                symbol=task.symbol,
                regime=task.regime,
                strategy=task.strategy,
                priority=task.priority.value,
                question=task.question,
                hypothesis_id=task.hypothesis_id,
                status=task.status,
                assigned_at=task.assigned_at,
                completed_at=task.completed_at,
                result_summary=task.result_summary,
                metadata_json=task.metadata,
            )
            db.add(record)
            db.commit()
        finally:
            db.close()

    def get_tasks(
        self, status: str | None = None, priority: TaskPriority | None = None,
        strategy: str | None = None,
    ) -> list[ResearchTask]:
        db: Session = self.SessionLocal()
        try:
            query = db.query(ResearchTaskRecord)
            if status:
                query = query.filter(ResearchTaskRecord.status == status)
            if priority:
                query = query.filter(ResearchTaskRecord.priority == priority.value)
            if strategy:
                query = query.filter(ResearchTaskRecord.strategy == strategy)
            records = query.order_by(ResearchTaskRecord.created_at.desc()).all()
            return [self._record_to_task(r) for r in records]
        finally:
            db.close()

    def complete_task(self, task_id: str, result_summary: str, status: str = "completed"):
        db: Session = self.SessionLocal()
        try:
            record = db.query(ResearchTaskRecord).filter(ResearchTaskRecord.id == task_id).first()
            if record:
                record.status = status
                record.completed_at = datetime.utcnow()
                record.result_summary = result_summary
                db.commit()
        finally:
            db.close()

    def _record_to_task(self, record: ResearchTaskRecord) -> ResearchTask:
        return ResearchTask(
            id=record.id,
            task_type=TaskType(record.task_type),
            symbol=record.symbol,
            regime=record.regime,
            strategy=record.strategy,
            priority=TaskPriority(record.priority),
            question=record.question,
            hypothesis_id=record.hypothesis_id,
            status=record.status,
            created_at=record.created_at,
            assigned_at=record.assigned_at,
            completed_at=record.completed_at,
            result_summary=record.result_summary,
            metadata=record.metadata_json or {},
        )
