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

from trading_ai.memory.journal import TradeJournal, FilterCriteria

Base = declarative_base()


class Decision(str, Enum):
    PROMOTE = "promote"
    REJECT = "reject"
    CONTINUE_PAPER = "continue_paper"
    NEED_MORE_DATA = "need_more_data"


class GatekeeperRecord(Base):
    __tablename__ = "gatekeeper_evaluations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    evaluated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    experiment_id = Column(String(36), nullable=False, index=True)
    candidate_strategy = Column(String(64), nullable=False)
    parent_strategy = Column(String(64), nullable=False)
    decision = Column(String(32), nullable=False)
    reasons = Column(JSON, default=[])
    hard_constraints = Column(JSON, default={})
    soft_scores = Column(JSON, default={})
    full_metrics = Column(JSON, default={})
    promotion_version = Column(String(64), nullable=True)
    metadata_json = Column(JSON, default={})


@dataclass
class PromotionPolicy:
    min_sample_size: int = 100
    max_drawdown: float = 12.0
    min_sharpe: float = 0.5
    min_profit_factor: float = 1.2
    min_win_rate: float = 0.40
    min_expectancy_r: float = 0.1
    min_pass_rate: float = 0.6
    min_robustness_score: float = 0.6
    min_regime_stability: float = 0.5
    min_parameter_stability: float = 0.6
    max_regression_pct: float = 0.15
    require_all_hard: bool = True


@dataclass
class EvaluationResult:
    decision: Decision
    reasons: list[str]
    hard_constraints: dict[str, bool]
    soft_scores: dict[str, float]
    full_metrics: dict[str, Any]


class StrategyGatekeeper:
    def __init__(self, journal: TradeJournal, db_url: str = "sqlite:///gatekeeper.db"):
        self.journal = journal
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.policy = PromotionPolicy()

    def set_policy(self, policy: PromotionPolicy):
        self.policy = policy

    def evaluate(self, experiment_results: dict) -> EvaluationResult:
        comparison = experiment_results.get("comparison", {})
        all_runs = experiment_results.get("all_runs", [])

        hard_constraints = self._check_hard_constraints(comparison, all_runs)
        soft_scores = self._compute_soft_scores(comparison, all_runs)
        reasons = []

        hard_pass = all(hard_constraints.values()) if self.policy.require_all_hard else any(hard_constraints.values())
        if not hard_pass:
            for k, v in hard_constraints.items():
                if not v:
                    reasons.append(f"Failed hard constraint: {k}")

        soft_pass = self._check_soft_scores(soft_scores)
        if not soft_pass:
            for k, v in soft_scores.items():
                threshold = getattr(self.policy, f"min_{k}", 0)
                if v < threshold:
                    reasons.append(f"Below soft threshold for {k}: {v:.4f} < {threshold}")

        decision = self._make_decision(hard_pass, soft_pass, comparison, reasons)

        if decision == Decision.PROMOTE:
            reasons.append("Candidate meets all promotion criteria")
        elif decision == Decision.NEED_MORE_DATA:
            reasons.append("Insufficient data for definitive decision")
        elif decision == Decision.CONTINUE_PAPER:
            reasons.append("Promising but not yet robust enough for promotion")
        elif decision == Decision.REJECT:
            reasons.append("Candidate does not meet minimum standards")

        return EvaluationResult(
            decision=decision,
            reasons=reasons,
            hard_constraints=hard_constraints,
            soft_scores=soft_scores,
            full_metrics={**comparison, **soft_scores},
        )

    def record_evaluation(
        self, experiment_id: str, candidate_strategy: str,
        parent_strategy: str, evaluation: EvaluationResult,
        promotion_version: str | None = None,
    ) -> str:
        db: Session = self.SessionLocal()
        try:
            record = GatekeeperRecord(
                id=str(uuid.uuid4()),
                evaluated_at=datetime.utcnow(),
                experiment_id=experiment_id,
                candidate_strategy=candidate_strategy,
                parent_strategy=parent_strategy,
                decision=evaluation.decision.value,
                reasons=evaluation.reasons,
                hard_constraints=evaluation.hard_constraints,
                soft_scores=evaluation.soft_scores,
                full_metrics=evaluation.full_metrics,
                promotion_version=promotion_version,
            )
            db.add(record)
            db.commit()
            return record.id
        finally:
            db.close()

    def get_evaluation_history(
        self, strategy: str | None = None, decision: Decision | None = None
    ) -> list[dict]:
        db: Session = self.SessionLocal()
        try:
            query = db.query(GatekeeperRecord)
            if strategy:
                query = query.filter(
                    (GatekeeperRecord.candidate_strategy == strategy) |
                    (GatekeeperRecord.parent_strategy == strategy)
                )
            if decision:
                query = query.filter(GatekeeperRecord.decision == decision.value)
            records = query.order_by(GatekeeperRecord.evaluated_at.desc()).all()
            return [
                {
                    "id": r.id,
                    "evaluated_at": r.evaluated_at.isoformat(),
                    "experiment_id": r.experiment_id,
                    "candidate_strategy": r.candidate_strategy,
                    "parent_strategy": r.parent_strategy,
                    "decision": r.decision,
                    "reasons": r.reasons,
                    "hard_constraints": r.hard_constraints,
                    "soft_scores": r.soft_scores,
                    "promotion_version": r.promotion_version,
                }
                for r in records
            ]
        finally:
            db.close()

    def _check_hard_constraints(self, comparison: dict, all_runs: list[dict]) -> dict[str, bool]:
        total_trades = comparison.get("total_runs", 0)
        avg_expectancy = comparison.get("avg_expectancy", 0)
        avg_sharpe = comparison.get("avg_sharpe", 0)
        max_dd = comparison.get("max_drawdown_r", float("inf"))
        pass_rate = comparison.get("pass_rate", 0)

        pf_values = [r.get("metrics", {}).get("profit_factor", 0) for r in all_runs if r.get("metrics")]
        avg_pf = sum(pf_values) / len(pf_values) if pf_values else 0

        wr_values = [r.get("metrics", {}).get("win_rate", 0) for r in all_runs if r.get("metrics")]
        avg_wr = sum(wr_values) / len(wr_values) if wr_values else 0

        return {
            "min_sample_size": total_trades >= self.policy.min_sample_size,
            "max_drawdown": max_dd <= self.policy.max_drawdown,
            "min_sharpe": avg_sharpe >= self.policy.min_sharpe,
            "min_profit_factor": avg_pf >= self.policy.min_profit_factor,
            "min_win_rate": avg_wr >= self.policy.min_win_rate,
            "min_expectancy_r": avg_expectancy >= self.policy.min_expectancy_r,
            "min_pass_rate": pass_rate >= self.policy.min_pass_rate,
        }

    def _compute_soft_scores(self, comparison: dict, all_runs: list[dict]) -> dict[str, float]:
        robustness = self._compute_robustness(all_runs)
        regime_stability = self._compute_regime_stability(all_runs)
        parameter_stability = self._compute_parameter_stability(all_runs)

        return {
            "robustness_score": robustness,
            "regime_stability": regime_stability,
            "parameter_stability": parameter_stability,
        }

    def _compute_robustness(self, all_runs: list[dict]) -> float:
        if not all_runs:
            return 0.0

        run_types = {}
        for run in all_runs:
            rt = run.get("run_type", "unknown")
            passed = run.get("passed", False)
            run_types.setdefault(rt, []).append(passed)

        if not run_types:
            return 0.0

        type_scores = []
        for rt, results in run_types.items():
            pass_rate = sum(results) / len(results) if results else 0
            type_scores.append(pass_rate)

        avg_pass_rate = sum(type_scores) / len(type_scores) if type_scores else 0
        consistency = 1.0 - (max(type_scores) - min(type_scores)) if len(type_scores) > 1 else 1.0

        return round(avg_pass_rate * 0.7 + consistency * 0.3, 4)

    def _compute_regime_stability(self, all_runs: list[dict]) -> float:
        regime_runs = [r for r in all_runs if r.get("run_type") in ("rolling_window", "expanding_window")]
        if not regime_runs:
            return 1.0

        expectancies = [r.get("metrics", {}).get("expectancy", 0) for r in regime_runs]
        positive = sum(1 for e in expectancies if e > 0)
        stability = positive / len(expectancies) if expectancies else 0

        if len(expectancies) > 1:
            mean_e = sum(expectancies) / len(expectancies)
            var_e = sum((e - mean_e) ** 2 for e in expectancies) / (len(expectancies) - 1)
            std_e = var_e ** 0.5
            consistency_bonus = max(0, 1 - std_e) * 0.2
        else:
            consistency_bonus = 0.1

        return round(min(stability + consistency_bonus, 1.0), 4)

    def _compute_parameter_stability(self, all_runs: list[dict]) -> float:
        pp_runs = [r for r in all_runs if r.get("run_type") == "parameter_perturbation"]
        if not pp_runs:
            return 1.0

        stability_scores = [
            r.get("metrics", {}).get("stability_score", 0) for r in pp_runs
        ]
        avg_stability = sum(stability_scores) / len(stability_scores) if stability_scores else 0
        return round(avg_stability, 4)

    def _check_soft_scores(self, soft_scores: dict[str, float]) -> bool:
        for key, score in soft_scores.items():
            threshold = getattr(self.policy, f"min_{key}", None)
            if threshold is not None and score < threshold:
                return False
        return True

    def _make_decision(
        self, hard_pass: bool, soft_pass: bool,
        comparison: dict, reasons: list[str],
    ) -> Decision:
        total_runs = comparison.get("total_runs", 0)
        avg_expectancy = comparison.get("avg_expectancy", 0)
        pass_rate = comparison.get("pass_rate", 0)

        if hard_pass and soft_pass:
            return Decision.PROMOTE

        if not hard_pass:
            critical_fails = sum(1 for v in [
                comparison.get("max_drawdown_r", 0) > self.policy.max_drawdown,
                comparison.get("avg_sharpe", 0) < self.policy.min_sharpe * 0.5,
                comparison.get("avg_expectancy", 0) < 0,
            ] if v)
            if critical_fails >= 2:
                return Decision.REJECT

        if total_runs < self.policy.min_sample_size * 0.5:
            return Decision.NEED_MORE_DATA

        if avg_expectancy > 0 and pass_rate > self.policy.min_pass_rate * 0.8:
            return Decision.CONTINUE_PAPER

        return Decision.REJECT
