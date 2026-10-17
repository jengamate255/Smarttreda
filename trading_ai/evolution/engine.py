from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from sqlalchemy import (
    Column, String, Float, DateTime, Text, JSON, Integer, Boolean,
    create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from trading_ai.memory.journal import TradeJournal, FilterCriteria, TradeEntry, AggregationResult

Base = declarative_base()


class ExperimentStatus(str, Enum):
    CREATED = "created"
    DATA_SPLIT = "data_split"
    WALK_FORWARD = "walk_forward"
    ROLLING_WINDOW = "rolling_window"
    EXPANDING_WINDOW = "expanding_window"
    MONTE_CARLO = "monte_carlo"
    PARAMETER_PERTURBATION = "parameter_perturbation"
    SPREAD_SENSITIVITY = "spread_sensitivity"
    SLIPPAGE_SENSITIVITY = "slippage_sensitivity"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentRecord(Base):
    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    hypothesis_id = Column(String(36), nullable=False, index=True)
    parent_strategy = Column(String(64), nullable=False, index=True)
    candidate_strategy = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default=ExperimentStatus.CREATED.value)

    train_start = Column(DateTime, nullable=True)
    train_end = Column(DateTime, nullable=True)
    validation_start = Column(DateTime, nullable=True)
    validation_end = Column(DateTime, nullable=True)
    test_start = Column(DateTime, nullable=True)
    test_end = Column(DateTime, nullable=True)

    config = Column(JSON, default={})
    results = Column(JSON, default={})
    all_runs = Column(JSON, default=[])
    comparison = Column(JSON, default={})

    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


@dataclass
class DataSplit:
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    def to_dict(self) -> dict:
        return {
            "train": {"start": self.train_start.isoformat(), "end": self.train_end.isoformat()},
            "validation": {"start": self.validation_start.isoformat(), "end": self.validation_end.isoformat()},
            "test": {"start": self.test_start.isoformat(), "end": self.test_end.isoformat()},
        }


@dataclass
class ExperimentConfig:
    hypothesis_id: str
    parent_strategy: str
    candidate_strategy: str
    data_split: DataSplit | None = None
    walk_forward_windows: int = 5
    walk_forward_train_ratio: float = 0.7
    rolling_window_days: int = 60
    rolling_window_step_days: int = 10
    expanding_window_min_trades: int = 100
    monte_carlo_iterations: int = 1000
    parameter_perturbation_pct: float = 0.1
    parameter_perturbation_iterations: int = 50
    spread_multipliers: list[float] = field(default_factory=lambda: [0.5, 1.0, 1.5, 2.0, 3.0])
    slippage_pips: list[float] = field(default_factory=lambda: [0.0, 0.5, 1.0, 2.0, 5.0])


@dataclass
class ExperimentResult:
    run_id: str
    run_type: str
    metrics: dict[str, Any]
    trades: list[dict]
    passed: bool
    error: str | None = None


class ExperimentEngine:
    def __init__(self, journal: TradeJournal, db_url: str = "sqlite:///experiments.db"):
        self.journal = journal
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def create_experiment(self, config: ExperimentConfig) -> str:
        exp_id = str(uuid.uuid4())
        if not config.data_split:
            config.data_split = self._auto_split(config.parent_strategy)

        db: Session = self.SessionLocal()
        try:
            record = ExperimentRecord(
                id=exp_id,
                created_at=datetime.utcnow(),
                hypothesis_id=config.hypothesis_id,
                parent_strategy=config.parent_strategy,
                candidate_strategy=config.candidate_strategy,
                status=ExperimentStatus.CREATED.value,
                config=self._config_to_dict(config),
            )
            db.add(record)
            db.commit()
            return exp_id
        finally:
            db.close()

    def run_experiment(self, experiment_id: str, strategy_fn: Callable) -> dict:
        config = self._load_config(experiment_id)
        if not config:
            return {"error": "Experiment not found"}

        self._update_status(experiment_id, ExperimentStatus.DATA_SPLIT)

        results: list[ExperimentResult] = []

        try:
            walk_forward = self._run_walk_forward(config, strategy_fn)
            results.extend(walk_forward)
            self._update_status(experiment_id, ExperimentStatus.WALK_FORWARD)

            rolling = self._run_rolling_window(config, strategy_fn)
            results.extend(rolling)
            self._update_status(experiment_id, ExperimentStatus.ROLLING_WINDOW)

            expanding = self._run_expanding_window(config, strategy_fn)
            results.extend(expanding)
            self._update_status(experiment_id, ExperimentStatus.EXPANDING_WINDOW)

            monte_carlo = self._run_monte_carlo(config, results)
            results.extend(monte_carlo)
            self._update_status(experiment_id, ExperimentStatus.MONTE_CARLO)

            param_perturb = self._run_parameter_perturbation(config, strategy_fn)
            results.extend(param_perturb)
            self._update_status(experiment_id, ExperimentStatus.PARAMETER_PERTURBATION)

            spread = self._run_spread_sensitivity(config, strategy_fn)
            results.extend(spread)
            self._update_status(experiment_id, ExperimentStatus.SPREAD_SENSITIVITY)

            slippage = self._run_slippage_sensitivity(config, strategy_fn)
            results.extend(slippage)
            self._update_status(experiment_id, ExperimentStatus.SLIPPAGE_SENSITIVITY)

            comparison = self._compare_strategies(config, results)

            self._save_results(experiment_id, results, comparison)
            self._update_status(experiment_id, ExperimentStatus.COMPLETED)

            return {
                "experiment_id": experiment_id,
                "status": "completed",
                "total_runs": len(results),
                "passed_runs": sum(1 for r in results if r.passed),
                "comparison": comparison,
            }

        except Exception as e:
            self._update_status(experiment_id, ExperimentStatus.FAILED, str(e))
            return {"experiment_id": experiment_id, "status": "failed", "error": str(e)}

    def _auto_split(self, strategy: str) -> DataSplit:
        criteria = FilterCriteria()
        criteria.strategies = [strategy]
        trades = self.journal.query_trades(criteria)

        if not trades:
            now = datetime.utcnow()
            return DataSplit(
                train_start=now, train_end=now,
                validation_start=now, validation_end=now,
                test_start=now, test_end=now,
            )

        timestamps = [datetime.fromisoformat(t["timestamp"]) for t in trades]
        timestamps.sort()
        total_days = (timestamps[-1] - timestamps[0]).days

        train_end_idx = int(len(timestamps) * 0.6)
        val_end_idx = int(len(timestamps) * 0.8)

        return DataSplit(
            train_start=timestamps[0],
            train_end=timestamps[train_end_idx],
            validation_start=timestamps[train_end_idx],
            validation_end=timestamps[val_end_idx],
            test_start=timestamps[val_end_idx],
            test_end=timestamps[-1],
        )

    def _run_walk_forward(self, config: ExperimentConfig, strategy_fn: Callable) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        total_days = (split.test_end - split.train_start).days
        window_size = total_days // config.walk_forward_windows
        train_days = int(window_size * config.walk_forward_train_ratio)
        test_days = window_size - train_days

        for i in range(config.walk_forward_windows):
            window_start = split.train_start
            train_start = window_start + __import__("datetime").timedelta(days=i * window_size)
            train_end = train_start + __import__("datetime").timedelta(days=train_days)
            test_start = train_end
            test_end = test_start + __import__("datetime").timedelta(days=test_days)

            if test_end > split.test_end:
                break

            try:
                trades = self.journal.query_trades(self._time_range_criteria(
                    config.parent_strategy, train_start, test_end
                ))
                train_trades = [t for t in trades if datetime.fromisoformat(t["timestamp"]) <= train_end]
                test_trades = [t for t in trades if datetime.fromisoformat(t["timestamp"]) > train_end]

                metrics = self._compute_metrics(test_trades)
                results.append(ExperimentResult(
                    run_id=f"wf_{i}",
                    run_type="walk_forward",
                    metrics=metrics,
                    trades=test_trades,
                    passed=metrics.get("expectancy", 0) > 0,
                ))
            except Exception as e:
                results.append(ExperimentResult(
                    run_id=f"wf_{i}",
                    run_type="walk_forward",
                    metrics={},
                    trades=[],
                    passed=False,
                    error=str(e),
                ))

        return results

    def _run_rolling_window(self, config: ExperimentConfig, strategy_fn: Callable) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        window = __import__("datetime").timedelta(days=config.rolling_window_days)
        step = __import__("datetime").timedelta(days=config.rolling_window_step_days)
        current_start = split.train_start

        idx = 0
        while current_start + window <= split.test_end:
            window_end = current_start + window
            test_window = window - __import__("datetime").timedelta(days=7)

            try:
                trades = self.journal.query_trades(self._time_range_criteria(
                    config.parent_strategy, current_start, window_end
                ))
                test_trades = [t for t in trades
                               if datetime.fromisoformat(t["timestamp"]) >= current_start + test_window]

                metrics = self._compute_metrics(test_trades)
                results.append(ExperimentResult(
                    run_id=f"rw_{idx}",
                    run_type="rolling_window",
                    metrics=metrics,
                    trades=test_trades,
                    passed=metrics.get("expectancy", 0) > 0,
                ))
            except Exception as e:
                results.append(ExperimentResult(
                    run_id=f"rw_{idx}",
                    run_type="rolling_window",
                    metrics={},
                    trades=[],
                    passed=False,
                    error=str(e),
                ))

            current_start += step
            idx += 1

        return results

    def _run_expanding_window(self, config: ExperimentConfig, strategy_fn: Callable) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        min_trades = config.expanding_window_min_trades
        current_end = split.train_start + __import__("datetime").timedelta(days=30)
        idx = 0

        while current_end <= split.test_end:
            try:
                trades = self.journal.query_trades(self._time_range_criteria(
                    config.parent_strategy, split.train_start, current_end
                ))

                if len(trades) >= min_trades:
                    metrics = self._compute_metrics(trades)
                    results.append(ExperimentResult(
                        run_id=f"ew_{idx}",
                        run_type="expanding_window",
                        metrics=metrics,
                        trades=trades,
                        passed=metrics.get("expectancy", 0) > 0,
                    ))
            except Exception as e:
                results.append(ExperimentResult(
                    run_id=f"ew_{idx}",
                    run_type="expanding_window",
                    metrics={},
                    trades=[],
                    passed=False,
                    error=str(e),
                ))

            current_end += __import__("datetime").timedelta(days=30)
            idx += 1

        return results

    def _run_monte_carlo(
        self, config: ExperimentConfig, existing_results: list[ExperimentResult]
    ) -> list[ExperimentResult]:
        results = []
        all_r_multiples = []
        for r in existing_results:
            for trade in r.trades:
                if "r_multiple" in trade:
                    all_r_multiples.append(trade["r_multiple"])

        if not all_r_multiples:
            return results

        import random

        for i in range(config.monte_carlo_iterations):
            shuffled = all_r_multiples.copy()
            random.seed(i)
            random.shuffle(shuffled)

            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            for r in shuffled:
                cumulative += r
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            mean_r = sum(shuffled) / len(shuffled)
            metrics = {
                "expectancy": mean_r,
                "max_drawdown_r": max_dd,
                "total_return_r": cumulative,
                "sample_size": len(shuffled),
                "win_rate": sum(1 for r in shuffled if r > 0) / len(shuffled),
            }
            results.append(ExperimentResult(
                run_id=f"mc_{i}",
                run_type="monte_carlo",
                metrics=metrics,
                trades=[],
                passed=metrics["expectancy"] > 0 and metrics["max_drawdown_r"] < 15,
            ))

        return results

    def _run_parameter_perturbation(
        self, config: ExperimentConfig, strategy_fn: Callable
    ) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        base_trades = self.journal.query_trades(self._time_range_criteria(
            config.parent_strategy, split.train_start, split.test_end
        ))

        if not base_trades:
            return results

        base_metrics = self._compute_metrics(base_trades)

        import random
        for i in range(config.parameter_perturbation_iterations):
            random.seed(i + 1000)
            perturbed_trades = []
            for t in base_trades:
                perturbed = t.copy()
                perturbed["entry_price"] *= (1 + random.uniform(
                    -config.parameter_perturbation_pct, config.parameter_perturbation_pct
                ))
                perturbed["stop_loss"] *= (1 + random.uniform(
                    -config.parameter_perturbation_pct, config.parameter_perturbation_pct
                ))
                perturbed["take_profit"] *= (1 + random.uniform(
                    -config.parameter_perturbation_pct, config.parameter_perturbation_pct
                ))
                perturbed_trades.append(perturbed)

            metrics = self._compute_metrics(perturbed_trades)
            stability = self._compute_stability(base_metrics, metrics)
            results.append(ExperimentResult(
                run_id=f"pp_{i}",
                run_type="parameter_perturbation",
                metrics={**metrics, "stability_score": stability},
                trades=perturbed_trades,
                passed=stability > 0.7 and metrics.get("expectancy", 0) > 0,
            ))

        return results

    def _run_spread_sensitivity(
        self, config: ExperimentConfig, strategy_fn: Callable
    ) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        base_trades = self.journal.query_trades(self._time_range_criteria(
            config.parent_strategy, split.train_start, split.test_end
        ))

        if not base_trades:
            return results

        for mult in config.spread_multipliers:
            adjusted_trades = []
            for t in base_trades:
                adjusted = t.copy()
                adjusted["spread_at_entry"] *= mult
                adjusted["pnl_pips"] -= t["spread_at_entry"] * (mult - 1)
                adjusted_trades.append(adjusted)

            metrics = self._compute_metrics(adjusted_trades)
            results.append(ExperimentResult(
                run_id=f"ss_{mult}",
                run_type="spread_sensitivity",
                metrics={**metrics, "spread_multiplier": mult},
                trades=adjusted_trades,
                passed=metrics.get("expectancy", 0) > 0,
            ))

        return results

    def _run_slippage_sensitivity(
        self, config: ExperimentConfig, strategy_fn: Callable
    ) -> list[ExperimentResult]:
        results = []
        split = config.data_split
        if not split:
            return results

        base_trades = self.journal.query_trades(self._time_range_criteria(
            config.parent_strategy, split.train_start, split.test_end
        ))

        if not base_trades:
            return results

        for slip in config.slippage_pips:
            adjusted_trades = []
            for t in base_trades:
                adjusted = t.copy()
                adjusted["pnl_pips"] -= slip
                adjusted_trades.append(adjusted)

            metrics = self._compute_metrics(adjusted_trades)
            results.append(ExperimentResult(
                run_id=f"sl_{slip}",
                run_type="slippage_sensitivity",
                metrics={**metrics, "slippage_pips": slip},
                trades=adjusted_trades,
                passed=metrics.get("expectancy", 0) > 0,
            ))

        return results

    def _compare_strategies(
        self, config: ExperimentConfig, results: list[ExperimentResult]
    ) -> dict:
        candidate_metrics = [r.metrics for r in results if r.passed and r.metrics]
        all_metrics = [r.metrics for r in results if r.metrics]

        if not all_metrics:
            return {"verdict": "insufficient_data", "reason": "No valid runs completed"}

        avg_expectancy = sum(m.get("expectancy", 0) for m in all_metrics) / len(all_metrics)
        avg_sharpe = sum(m.get("sharpe_approx", 0) for m in all_metrics) / len(all_metrics)
        max_dd = max(m.get("max_drawdown_r", 0) for m in all_metrics)
        pass_rate = len(candidate_metrics) / len(all_metrics) if all_metrics else 0
        stability_scores = [m.get("stability_score", 1.0) for m in all_metrics if "stability_score" in m]
        avg_stability = sum(stability_scores) / len(stability_scores) if stability_scores else 1.0

        return {
            "avg_expectancy": round(avg_expectancy, 4),
            "avg_sharpe": round(avg_sharpe, 4),
            "max_drawdown_r": round(max_dd, 4),
            "pass_rate": round(pass_rate, 4),
            "avg_stability": round(avg_stability, 4),
            "total_runs": len(all_metrics),
            "passed_runs": len(candidate_metrics),
            "verdict": "promote_candidate" if pass_rate > 0.7 and avg_expectancy > 0 else "needs_more_data",
        }

    def _compute_metrics(self, trades: list[dict]) -> dict:
        if not trades:
            return {"expectancy": 0, "total_trades": 0}

        r_values = [t.get("r_multiple", 0) for t in trades]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r < 0]

        total = len(r_values)
        win_rate = len(wins) / total if total > 0 else 0
        avg_r = sum(r_values) / total if total > 0 else 0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

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
            sharpe = mean_r / std_r if std_r > 0 else 0
        else:
            sharpe = 0

        return {
            "expectancy": round(avg_r, 4),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "max_drawdown_r": round(max_dd, 4),
            "sharpe_approx": round(sharpe, 4),
            "total_trades": total,
            "total_return_r": round(cumulative, 4),
        }

    def _compute_stability(self, base: dict, perturbed: dict) -> float:
        if not base or not perturbed:
            return 0.0

        base_exp = base.get("expectancy", 0)
        pert_exp = perturbed.get("expectancy", 0)

        if base_exp == 0:
            return 1.0 if pert_exp == 0 else 0.0

        ratio = pert_exp / base_exp if base_exp != 0 else 0
        stability = max(0, 1 - abs(1 - ratio))
        return round(stability, 4)

    def _time_range_criteria(self, strategy: str, start: datetime, end: datetime) -> FilterCriteria:
        criteria = FilterCriteria()
        criteria.strategies = [strategy]
        criteria.date_from = start
        criteria.date_to = end
        return criteria

    def _config_to_dict(self, config: ExperimentConfig) -> dict:
        return {
            "hypothesis_id": config.hypothesis_id,
            "parent_strategy": config.parent_strategy,
            "candidate_strategy": config.candidate_strategy,
            "data_split": config.data_split.to_dict() if config.data_split else None,
            "walk_forward_windows": config.walk_forward_windows,
            "rolling_window_days": config.rolling_window_days,
            "expanding_window_min_trades": config.expanding_window_min_trades,
            "monte_carlo_iterations": config.monte_carlo_iterations,
            "parameter_perturbation_pct": config.parameter_perturbation_pct,
            "spread_multipliers": config.spread_multipliers,
            "slippage_pips": config.slippage_pips,
        }

    def _load_config(self, experiment_id: str) -> ExperimentConfig | None:
        db: Session = self.SessionLocal()
        try:
            record = db.query(ExperimentRecord).filter(ExperimentRecord.id == experiment_id).first()
            if not record:
                return None
            cfg = record.config
            split_data = cfg.get("data_split")
            split = None
            if split_data:
                split = DataSplit(
                    train_start=datetime.fromisoformat(split_data["train"]["start"]),
                    train_end=datetime.fromisoformat(split_data["train"]["end"]),
                    validation_start=datetime.fromisoformat(split_data["validation"]["start"]),
                    validation_end=datetime.fromisoformat(split_data["validation"]["end"]),
                    test_start=datetime.fromisoformat(split_data["test"]["start"]),
                    test_end=datetime.fromisoformat(split_data["test"]["end"]),
                )
            return ExperimentConfig(
                hypothesis_id=cfg["hypothesis_id"],
                parent_strategy=cfg["parent_strategy"],
                candidate_strategy=cfg["candidate_strategy"],
                data_split=split,
                walk_forward_windows=cfg.get("walk_forward_windows", 5),
                rolling_window_days=cfg.get("rolling_window_days", 60),
                expanding_window_min_trades=cfg.get("expanding_window_min_trades", 100),
                monte_carlo_iterations=cfg.get("monte_carlo_iterations", 1000),
                parameter_perturbation_pct=cfg.get("parameter_perturbation_pct", 0.1),
                spread_multipliers=cfg.get("spread_multipliers", [0.5, 1.0, 1.5, 2.0, 3.0]),
                slippage_pips=cfg.get("slippage_pips", [0.0, 0.5, 1.0, 2.0, 5.0]),
            )
        finally:
            db.close()

    def _update_status(self, experiment_id: str, status: ExperimentStatus, error: str | None = None):
        db: Session = self.SessionLocal()
        try:
            record = db.query(ExperimentRecord).filter(ExperimentRecord.id == experiment_id).first()
            if record:
                record.status = status.value
                if error:
                    record.error = error
                if status == ExperimentStatus.COMPLETED:
                    record.completed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()

    def _save_results(self, experiment_id: str, results: list[ExperimentResult], comparison: dict):
        db: Session = self.SessionLocal()
        try:
            record = db.query(ExperimentRecord).filter(ExperimentRecord.id == experiment_id).first()
            if record:
                record.results = comparison
                record.all_runs = [
                    {
                        "run_id": r.run_id,
                        "run_type": r.run_type,
                        "metrics": r.metrics,
                        "passed": r.passed,
                        "error": r.error,
                    }
                    for r in results
                ]
                record.comparison = comparison
                db.commit()
        finally:
            db.close()

    def get_experiment(self, experiment_id: str) -> dict | None:
        db: Session = self.SessionLocal()
        try:
            record = db.query(ExperimentRecord).filter(ExperimentRecord.id == experiment_id).first()
            if not record:
                return None
            return {
                "id": record.id,
                "created_at": record.created_at.isoformat(),
                "hypothesis_id": record.hypothesis_id,
                "parent_strategy": record.parent_strategy,
                "candidate_strategy": record.candidate_strategy,
                "status": record.status,
                "config": record.config,
                "results": record.results,
                "all_runs": record.all_runs,
                "comparison": record.comparison,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
                "error": record.error,
            }
        finally:
            db.close()

    def list_experiments(
        self, status: str | None = None, strategy: str | None = None
    ) -> list[dict]:
        db: Session = self.SessionLocal()
        try:
            query = db.query(ExperimentRecord)
            if status:
                query = query.filter(ExperimentRecord.status == status)
            if strategy:
                query = query.filter(ExperimentRecord.parent_strategy == strategy)
            records = query.order_by(ExperimentRecord.created_at.desc()).all()
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat(),
                    "parent_strategy": r.parent_strategy,
                    "status": r.status,
                    "comparison": r.comparison,
                }
                for r in records
            ]
        finally:
            db.close()
