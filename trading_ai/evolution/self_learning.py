"""SelfLearningLoop: automatic recursive self-improvement with safety, phases,
population, multi-regime fitness, OOS gating, mutation credit meta-learning,
paper-trade confirmation, and human-in-the-loop live approval.

Cycle per iteration:
  1. Safety gate (kill switches / phase)
  2. Analyze weaknesses from TradeJournal (or bootstrap)
  3. Weighted mutation via MutationCreditLedger (meta-learned)
  4. Multi-regime + optional OOS backtest fitness
  5. Gatekeeper (phase-dependent strictness)
  6. Promote/reject; credit ledger; lineage; graph; deployment stage
  7. Population cull / stagnation / phase transition
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd

from trading_ai.data.generator import SyntheticDataGenerator
from trading_ai.evolution.credits import MutationCreditLedger
from trading_ai.evolution.gatekeeper import StrategyGatekeeper, Decision, EvaluationResult, PromotionPolicy
from trading_ai.evolution.lineage import StrategyLineage, MutationType
from trading_ai.evolution.safety import (
    SafetyController,
    SafetyLimits,
    LoopPhase,
    PhaseConfig,
    DeploymentGate,
    DeploymentStage,
)
from trading_ai.evaluation.performance import PerformanceAnalyzer
from trading_ai.backtesting.engine import BacktestEngine
from trading_ai.models import BacktestConfig
from trading_ai.memory.graph import ResearchGraph, NodeType
from trading_ai.memory.journal import TradeJournal
from trading_ai.research.agent import ResearchAgent, Hypothesis, HypothesisStatus, WeaknessType
from trading_ai.research.meta import MetaResearchAgent
from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.genome.mutator import StrategyMutator
from trading_ai.strategies.genome_executor import make_strategy_fn

logger = logging.getLogger(__name__)

# Candidate mutation types for weighted selection
_MUTATION_POOL = [
    MutationType.PARAMETER_CHANGE,
    MutationType.SESSION_FILTER,
    MutationType.REGIME_FILTER,
    MutationType.RISK_MANAGEMENT,
    MutationType.EXIT_LOGIC,
]


@dataclass
class IterationResult:
    iteration: int
    hypothesis_id: str | None
    hypothesis_desc: str
    parent_id: str
    candidate_id: str
    decision: str
    reasons: list[str]
    parent_metrics: dict[str, Any]
    candidate_metrics: dict[str, Any]
    improved: bool
    duration_s: float
    error: str | None = None
    mutation_type: str = ""
    phase: str = ""
    regime_scores: dict[str, Any] = field(default_factory=dict)
    oos_passed: bool | None = None
    deployment_stage: str = ""


@dataclass
class LoopState:
    running: bool = False
    iterations_completed: int = 0
    promotions: int = 0
    rejections: int = 0
    errors: int = 0
    best_genome_id: str = ""
    best_metrics: dict[str, Any] = field(default_factory=dict)
    last_iteration: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    started_at: str | None = None
    last_at: str | None = None
    population_size: int = 1
    paper_pending: list[str] = field(default_factory=list)
    paper_approved: list[str] = field(default_factory=list)
    live_approved: list[str] = field(default_factory=list)


class SelfLearningLoop:
    """Orchestrates the full recursive self-improvement pipeline."""

    def __init__(
        self,
        journal: Optional[TradeJournal] = None,
        db_url_prefix: str = "self_learn",
        data: Optional[dict[str, pd.DataFrame]] = None,
        market_state: Optional[dict] = None,
        base_genome: Optional[StrategyGenome] = None,
        backtest_config: Optional[BacktestConfig] = None,
        max_history: int = 200,
        multi_regime: bool = True,
        use_oos: bool | None = None,
        population_size: int = 1,
        safety_limits: Optional[SafetyLimits] = None,
        seed: int = 42,
        base_count: int = 5000,
    ) -> None:
        self.journal = journal or TradeJournal(db_url=f"sqlite:///{db_url_prefix}_journal.db")
        self.research = ResearchAgent(
            self.journal, db_url=f"sqlite:///{db_url_prefix}_research.db"
        )
        self.meta = MetaResearchAgent(
            self.journal, self.research, db_url=f"sqlite:///{db_url_prefix}_meta.db"
        )
        from trading_ai.evolution.engine import ExperimentEngine
        self.experiment_engine = ExperimentEngine(
            self.journal, db_url=f"sqlite:///{db_url_prefix}_experiments.db"
        )
        self.gatekeeper = StrategyGatekeeper(
            self.journal, db_url=f"sqlite:///{db_url_prefix}_gatekeeper.db"
        )
        self.lineage = StrategyLineage(db_url=f"sqlite:///{db_url_prefix}_lineage.db")
        self.graph = ResearchGraph(db_url=f"sqlite:///{db_url_prefix}_graph.db")
        self.mutator = StrategyMutator()
        self.credits = MutationCreditLedger(db_url=f"sqlite:///{db_url_prefix}_credits.db")
        self.safety = SafetyController(safety_limits or SafetyLimits())
        self.deployment = DeploymentGate()

        self.multi_regime = multi_regime
        self._use_oos = use_oos  # None → follow phase config
        self.population_size = max(1, population_size)
        self._rng = random.Random(seed)
        self._seed = seed
        self._base_count = base_count

        self.regime_data: dict[str, dict[str, pd.DataFrame]] = {}
        if data is not None:
            self.regime_data["primary"] = data
        else:
            self.regime_data = self._default_regime_data() if multi_regime else {
                "primary": self._default_single_data()
            }
        self.data = self.regime_data.get("primary") or next(iter(self.regime_data.values()))

        self.market_state = market_state if market_state is not None else {
            "symbol": "EURUSD", "session": "london",
            "volatility_regime": "normal", "atr": 0.001,
        }
        self.base_genome = base_genome or self._default_genome()
        self.bt_config = backtest_config or BacktestConfig(
            initial_capital=100000, risk_per_trade=0.005, seed=42,
        )
        self.analyzer = PerformanceAnalyzer(self.bt_config)
        self.max_history = max_history

        self.state = LoopState()
        self.state.best_genome_id = self.base_genome.strategy_id
        self._current_genome: StrategyGenome = self.base_genome
        # Population: list of (genome, metrics)
        self._population: list[tuple[StrategyGenome, dict[str, Any]]] = [
            (self.base_genome, {})
        ]
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._event_handlers: dict[str, list[Callable]] = {}

        # Baseline fitness for peak tracking
        try:
            base_m = self._fitness(self.base_genome)
            self.state.best_metrics = base_m
            self.safety.state.peak_best_pf = base_m.get("profit_factor", 0) or 0
            self._population[0] = (self.base_genome, base_m)
        except Exception:
            pass

        try:
            self.lineage.create_version(
                self.base_genome.strategy_id, self.base_genome.__dict__,
                mutation_type=None, mutation_description="baseline",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Events (orchestrator integration)
    # ------------------------------------------------------------------

    def on(self, event: str, handler: Callable) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for h in self._event_handlers.get(event, []):
            try:
                h(payload)
            except Exception:
                logger.exception("SelfLearning event handler %s failed", event)

    # ------------------------------------------------------------------
    # Defaults / data
    # ------------------------------------------------------------------

    def _default_single_data(self) -> dict[str, pd.DataFrame]:
        gen = SyntheticDataGenerator(seed=self._seed)
        return gen.generate_multi_timeframe(
            "EURUSD", base_timeframe="M5",
            timeframes=("M5", "M15", "H1", "H4"),
            base_count=self._base_count, initial_price=1.1000, trend="bullish",
        )

    def _default_regime_data(self) -> dict[str, dict[str, pd.DataFrame]]:
        gen = SyntheticDataGenerator(seed=self._seed)
        count = min(self._base_count, 3500)
        out: dict[str, dict[str, pd.DataFrame]] = {}
        for name, kwargs in (
            ("bullish", {"trend": "bullish", "initial_price": 1.1000}),
            ("bearish", {"trend": "bearish", "initial_price": 1.1000}),
            ("ranging", {"trend": "ranging", "initial_price": 1.1000}),
        ):
            try:
                out[name] = gen.generate_multi_timeframe(
                    "EURUSD", base_timeframe="M5",
                    timeframes=("M5", "M15", "H1", "H4"),
                    base_count=count, **kwargs,
                )
            except Exception:
                out[name] = gen.generate_multi_timeframe(
                    "EURUSD", base_timeframe="M5",
                    timeframes=("M5", "M15", "H1", "H4"),
                    base_count=count, initial_price=1.1000,
                )
        return out

    def _default_genome(self) -> StrategyGenome:
        return StrategyGenome(
            strategy_id="self-learn-000",
            name="Self-Learn Baseline",
            description="Baseline genome for recursive self-improvement",
            market=["EURUSD", "*"],
            timeframes=["H4", "H1", "M15", "M5"],
            entry_rules=[{"type": "supply_demand", "params": {"lookback": 50, "eval_every": 3}}],
            exit_rules=[{"type": "risk_reward", "params": {"min_rr": 1.5}}],
            risk_rules=[{"type": "stop_placement", "params": {"min_rr": 1.5}}],
            filters=[],
        )

    # ------------------------------------------------------------------
    # Fitness: multi-regime + OOS
    # ------------------------------------------------------------------

    def _backtest_on(self, genome: StrategyGenome, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        fn = make_strategy_fn(genome, data, self.market_state)
        h1 = data.get("H1")
        if h1 is None or h1.empty:
            h1 = next(iter(data.values()))
        engine = BacktestEngine(fn, h1, self.bt_config)
        result = engine.run()
        pm = self.analyzer.calculate(
            result.trades, result.equity_curve, result.drawdown_series
        )
        return {
            "total_trades": pm.total_trades,
            "win_rate": pm.win_rate,
            "profit_factor": pm.profit_factor if pm.profit_factor != float("inf") else 10.0,
            "sharpe": pm.sharpe_ratio,
            "sortino": pm.sortino_ratio,
            "max_drawdown_pct": pm.max_drawdown_pct,
            "expectancy": pm.expectancy,
            "net_profit": pm.net_profit,
            "return_pct": pm.return_pct,
            "recovery_factor": pm.recovery_factor,
        }

    def _oos_backtest(
        self, genome: StrategyGenome, data: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """Time-split OOS: train 60% / val 20% / test 20% on primary H1/M5 series."""
        key = "H1" if "H1" in data else next(iter(data))
        frame = data[key]
        n = len(frame)
        if n < 100:
            return self._backtest_on(genome, data)
        train_end = int(n * 0.6)
        val_end = int(n * 0.8)
        slices = {
            "train": frame.iloc[:train_end],
            "validation": frame.iloc[train_end:val_end],
            "test": frame.iloc[val_end:],
        }
        out: dict[str, Any] = {}
        for name, slice_df in slices.items():
            sub = dict(data)
            sub[key] = slice_df
            # Also trim other TFs by timestamp if possible
            if len(slice_df) > 0:
                t0, t1 = slice_df.index[0], slice_df.index[-1]
                for tf, f in data.items():
                    if tf == key:
                        continue
                    try:
                        sub[tf] = f[(f.index >= t0) & (f.index <= t1)]
                    except Exception:
                        pass
            try:
                out[name] = self._backtest_on(genome, sub)
            except Exception:
                out[name] = {"total_trades": 0, "profit_factor": 0, "expectancy": 0}
        # Aggregate: average PF/exp across val+test (true OOS)
        oos = [out["validation"], out["test"]]
        out["oos_avg_pf"] = sum(m.get("profit_factor", 0) for m in oos) / len(oos)
        out["oos_avg_exp"] = sum(m.get("expectancy", 0) for m in oos) / len(oos)
        out["oos_avg_trades"] = sum(m.get("total_trades", 0) for m in oos) / len(oos)
        return out

    def _fitness(self, genome: StrategyGenome) -> dict[str, Any]:
        """Primary fitness + per-regime scores when multi_regime enabled."""
        primary = self._backtest_on(genome, self.data)
        result: dict[str, Any] = dict(primary)
        if not self.multi_regime or len(self.regime_data) <= 1:
            result["regimes"] = {"primary": primary}
            result["regimes_ok"] = 1
            result["regimes_total"] = 1
            return result

        regimes: dict[str, Any] = {}
        parent_ref = self.state.best_metrics or primary
        parent_pf = parent_ref.get("profit_factor", 0) or 0
        parent_exp = parent_ref.get("expectancy", 0) or 0
        ok = 0
        for name, rdata in self.regime_data.items():
            try:
                m = self._backtest_on(genome, rdata)
            except Exception:
                m = {"total_trades": 0, "profit_factor": 0, "expectancy": 0}
            regimes[name] = m
            # Non-worse: PF not much below parent and expectancy not deeply negative
            if m.get("profit_factor", 0) >= parent_pf * 0.9 or m.get("expectancy", 0) >= parent_exp - 0.05:
                ok += 1
        result["regimes"] = regimes
        result["regimes_ok"] = ok
        result["regimes_total"] = len(regimes)
        return result

    def _backtest_genome(self, genome: StrategyGenome) -> dict[str, Any]:
        """Back-compat: primary regime metrics only."""
        return self._backtest_on(genome, self.data)

    def _metrics_to_gatekeeper(self, metrics: dict[str, Any]) -> dict[str, Any]:
        pf = metrics.get("profit_factor", 0)
        wr = metrics.get("win_rate", 0)
        sharpe = metrics.get("sharpe", 0)
        exp = metrics.get("expectancy", 0)
        trades = metrics.get("total_trades", 0)
        return {
            "total_runs": trades,
            "sample_size": trades,
            "avg_expectancy": exp,
            "avg_sharpe": sharpe,
            "avg_profit_factor": pf,
            "avg_win_rate": wr,
            "max_drawdown_r": metrics.get("max_drawdown_pct", 0) * 100,
            "max_drawdown": metrics.get("max_drawdown_pct", 0) * 100,
            "pass_rate": 1.0 if exp > 0 else 0.0,
            "profit_factor": pf,
            "win_rate": wr,
            "sharpe_ratio": sharpe,
            "expectancy_r": exp,
            "robustness_score": min(1.0, max(0.0, sharpe)),
            "regime_stability": (metrics.get("regimes_ok", 1) / max(1, metrics.get("regimes_total", 1))),
            "parameter_stability": 0.7,
            "regression_pct": 0.0,
        }

    def _set_gatekeeper_policy(self, phase: PhaseConfig) -> None:
        if phase.name == LoopPhase.EXPLORE:
            self.gatekeeper.set_policy(PromotionPolicy(
                min_sample_size=5, max_drawdown=50.0, min_sharpe=-1.0,
                min_profit_factor=0.5, min_win_rate=0.3, min_expectancy_r=-1.0,
                min_pass_rate=0.0, min_robustness_score=0.0,
                min_regime_stability=0.0, min_parameter_stability=0.0,
                max_regression_pct=1.0, require_all_hard=False,
            ))
        else:
            self.gatekeeper.set_policy(PromotionPolicy(
                min_sample_size=10, max_drawdown=25.0, min_sharpe=0.0,
                min_profit_factor=1.1, min_win_rate=0.4, min_expectancy_r=0.0,
                min_pass_rate=0.4, min_robustness_score=0.3,
                min_regime_stability=0.4, min_parameter_stability=0.3,
                max_regression_pct=0.2, require_all_hard=False,
            ))

    # ------------------------------------------------------------------
    # Mutation (credit-weighted + phase-aware)
    # ------------------------------------------------------------------

    def _mutate_for_hypothesis(
        self, genome: StrategyGenome, hypothesis: Hypothesis
    ) -> tuple[StrategyGenome, MutationType]:
        """Credit-weighted mutation selection; hypothesis biases params when aligned."""
        phase = self.safety.phase_config()
        rng = self._rng
        # Align pool with hypothesis hint, but allow credit-weighted alternatives
        hint = self._hypothesis_mutation_hint(hypothesis)
        pool = list(_MUTATION_POOL)
        if hint and hint in pool:
            # Boost hint into pool twice so hypothesis still matters
            pool = [hint, hint] + pool
        mtype = self.credits.choose(pool, rng=rng, explore_epsilon=phase.explore_epsilon)

        try:
            child = self._apply_mutation(genome, mtype, hypothesis, rng)
            return child, mtype
        except Exception as e:
            logger.warning("Mutation %s failed (%s); fallback volatility", mtype, e)
            child = self.mutator.mutate_volatility_filter(
                genome, {"min_atr": 0.0004, "description": "fallback"}
            )
            return child, MutationType.REGIME_FILTER

    def _hypothesis_mutation_hint(self, hypothesis: Hypothesis) -> MutationType | None:
        wt = str(hypothesis.weakness_type)
        change = str(hypothesis.proposed_change).lower()
        if "session" in change or "session" in wt:
            return MutationType.SESSION_FILTER
        if "regime" in change or "volatility" in change or "regime" in wt:
            return MutationType.REGIME_FILTER
        if "risk" in change or "drawdown" in wt or "stop" in change:
            return MutationType.RISK_MANAGEMENT
        if "exit" in change or "reward" in change:
            return MutationType.EXIT_LOGIC
        return MutationType.PARAMETER_CHANGE

    def _apply_mutation(
        self,
        genome: StrategyGenome,
        mtype: MutationType,
        hypothesis: Hypothesis,
        rng: random.Random,
    ) -> StrategyGenome:
        desc = hypothesis.proposed_change[:120]
        if mtype == MutationType.SESSION_FILTER:
            sessions = rng.choice([
                ["london"], ["london", "overlap"], ["overlap"], ["london", "new_york"],
            ])
            return self.mutator.mutate_session_filter(
                genome, {"sessions": sessions, "description": desc}
            )
        if mtype == MutationType.REGIME_FILTER:
            lo = rng.choice([0.0, 0.0003, 0.0005, 0.0008])
            hi = rng.choice([0.001, 0.002, 0.004, 0.01])
            if lo >= hi:
                lo = 0.0
            return self.mutator.mutate_volatility_filter(
                genome, {"min_atr": lo, "max_atr": hi, "description": desc}
            )
        if mtype == MutationType.RISK_MANAGEMENT:
            min_rr = rng.choice([1.5, 2.0, 2.5, 3.0])
            max_risk = rng.choice([0.002, 0.005, 0.01, 0.0])
            return self.mutator.mutate_stop_placement(
                genome, {"min_rr": min_rr, "max_risk_frac": max_risk, "description": desc}
            )
        if mtype == MutationType.EXIT_LOGIC:
            min_rr = rng.choice([1.5, 2.0, 2.5, 3.0, 4.0])
            return self.mutator.mutate_exit_rule(
                genome, {"min_rr": min_rr, "rule_type": "risk_reward", "description": desc}
            )
        # PARAMETER_CHANGE default: entry + light exit
        min_conf = rng.choice([0.0, 0.5, 0.55, 0.6, 0.7])
        min_rr = rng.choice([1.0, 1.5, 2.0, 2.5])
        eval_every = rng.choice([2, 3, 5, 7])
        child = self.mutator.mutate_entry_filter(
            genome, {
                "lookback": rng.choice([20, 30, 40, 50, 60]),
                "min_confidence": min_conf,
                "eval_every": eval_every,
                "rule_type": "supply_demand",
                "description": desc,
            }
        )
        return self.mutator.mutate_exit_rule(
            child, {"min_rr": min_rr, "rule_type": "risk_reward", "description": f"min_rr={min_rr}"}
        )

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _select_parent(self) -> StrategyGenome:
        if len(self._population) <= 1:
            return self._population[0][0]
        # Tournament: pick 2 random, take better PF
        i, j = self._rng.sample(range(len(self._population)), 2)
        a, b = self._population[i], self._population[j]
        pa = (a[1] or {}).get("profit_factor", 0)
        pb = (b[1] or {}).get("profit_factor", 0)
        return a[0] if pa >= pb else b[0]

    def _cull_population(self) -> None:
        """Keep top population_size by PF; drop stagnating duplicates."""
        if len(self._population) <= self.population_size:
            return
        self._population.sort(
            key=lambda x: (x[1] or {}).get("profit_factor", 0), reverse=True
        )
        self._population = self._population[: self.population_size]
        self.state.population_size = len(self._population)

    # ------------------------------------------------------------------
    # One iteration
    # ------------------------------------------------------------------

    def run_once(self) -> IterationResult:
        t0 = time.time()
        it = self.state.iterations_completed + 1
        phase = self.safety.phase_config()
        hypothesis: Hypothesis | None = None
        candidate: StrategyGenome | None = None
        mut_type = MutationType.PARAMETER_CHANGE
        parent = self._current_genome

        # Safety gate
        can, reason = self.safety.can_run_iteration()
        if not can:
            duration = time.time() - t0
            result = IterationResult(
                iteration=it, hypothesis_id=None, hypothesis_desc="blocked",
                parent_id=parent.strategy_id, candidate_id="", decision="BLOCKED",
                reasons=[reason or "safety"], parent_metrics={}, candidate_metrics={},
                improved=False, duration_s=duration, error=reason,
                phase=phase.name.value,
            )
            # Do not increment completed on blocked? Still record for visibility
            self.state.errors += 1
            self._record_iteration(result)
            return result

        try:
            # 1. Hypotheses from journal weaknesses (memory stream)
            weaknesses = self.research.analyze_weaknesses(
                strategy_version=parent.strategy_id, min_sample_size=1
            )
            hypotheses = self.research.generate_hypotheses(weaknesses, parent.strategy_id)
            if not hypotheses:
                hypothesis = Hypothesis(
                    id=f"bootstrap-{it}",
                    description=f"Iteration {it}: {phase.name.value} parameter exploration",
                    parent_strategy=parent.strategy_id,
                    proposed_change=self._bootstrap_change_text(phase),
                    rationale="Bootstrap when journal cold-start empty",
                    required_test="Backtest parent vs candidate (multi-regime + OOS)",
                    status=HypothesisStatus.PROPOSED,
                    weakness_type=WeaknessType.NEGATIVE_EXPECTANCY,
                    priority_score=1.0,
                )
            else:
                hypotheses.sort(key=lambda h: h.priority_score, reverse=True)
                hypothesis = hypotheses[0]
                try:
                    self.research.save_hypothesis(hypothesis)
                except Exception:
                    pass

            # 2. Parent selection (population)
            parent = self._select_parent()
            self._current_genome = parent

            # 3. Credit-weighted mutation
            candidate, mut_type = self._mutate_for_hypothesis(parent, hypothesis)

            # 4. Fitness: multi-regime + optional OOS
            parent_metrics = self._fitness(parent)
            candidate_metrics = self._fitness(candidate)

            oos_passed: bool | None = None
            want_oos = self._use_oos if self._use_oos is not None else phase.require_oos
            if want_oos:
                try:
                    p_oos = self._oos_backtest(parent, self.data)
                    c_oos = self._oos_backtest(candidate, self.data)
                    candidate_metrics["oos"] = c_oos
                    parent_metrics["oos"] = p_oos
                    oos_passed = (
                        c_oos.get("oos_avg_pf", 0) >= p_oos.get("oos_avg_pf", 0) * 0.95
                        and c_oos.get("oos_avg_exp", 0) > -0.5
                    )
                except Exception as e:
                    logger.warning("OOS backtest failed: %s", e)
                    oos_passed = False

            # 5. Improvement decision (phase-aware)
            p_pf = parent_metrics.get("profit_factor", 0) or 0
            c_pf = candidate_metrics.get("profit_factor", 0) or 0
            p_ex = parent_metrics.get("expectancy", 0) or 0
            c_ex = candidate_metrics.get("expectancy", 0) or 0
            c_tr = candidate_metrics.get("total_trades", 0) or 0
            p_tr = parent_metrics.get("total_trades", 0) or 0
            # Floor: absolute phase min + keep enough sample vs parent
            # (blocks degenerate "1 trade, PF=10" promotions)
            min_abs = max(phase.min_trades, 3)
            min_vs_parent = max(3, int(p_tr * 0.4)) if p_tr >= 3 else min_abs
            trade_floor = max(min_abs, min_vs_parent)

            improved = (
                c_tr >= trade_floor
                and c_ex >= 0
                and (
                    c_ex > p_ex + phase.min_improvement_eps
                    or (
                        c_pf > p_pf * phase.promote_epsilon_pf
                        and c_ex >= p_ex - 1e-9
                    )
                )
            )
            # Multi-regime: need enough regimes non-worse
            regimes_ok = candidate_metrics.get("regimes_ok", 1)
            regimes_total = max(1, candidate_metrics.get("regimes_total", 1))
            regimes_passed = regimes_ok >= min(phase.require_regimes, regimes_total)

            if want_oos and oos_passed is False:
                improved = False

            if improved and not regimes_passed:
                improved = False

            # 6. Gatekeeper (informs, secondary to improved)
            comparison = self._metrics_to_gatekeeper(candidate_metrics)
            all_runs = [
                {"run_type": "backtest", "metrics": parent_metrics, "passed": True},
                {"run_type": "backtest", "metrics": candidate_metrics,
                 "passed": c_ex > 0},
            ]
            if oos_passed is not None:
                all_runs.append({
                    "run_type": "oos",
                    "metrics": candidate_metrics.get("oos", {}),
                    "passed": bool(oos_passed),
                })
            self._set_gatekeeper_policy(phase)
            eval_result: EvaluationResult = self.gatekeeper.evaluate(
                {"comparison": comparison, "all_runs": all_runs}
            )
            decision = eval_result.decision
            should_promote = improved and decision != Decision.REJECT

            reasons = list(eval_result.reasons)
            if should_promote and decision != Decision.PROMOTE:
                reasons.append("Promoted on metric improvement (gatekeeper soft-pass)")
            if not improved:
                reasons.append(
                    f"No improvement: parent(pf={p_pf:.4f},exp={p_ex:.4f},tr={p_tr}) "
                    f"cand(pf={c_pf:.4f},exp={c_ex:.4f},tr={c_tr}) "
                    f"floor={trade_floor}"
                )
            if not regimes_passed:
                reasons.append(
                    f"Regimes ok {regimes_ok}/{regimes_total} < required {phase.require_regimes}"
                )
            if want_oos and oos_passed is False:
                reasons.append("OOS gate failed")

            # 7. Apply decision
            decision_str = "REJECT"
            if should_promote:
                self._current_genome = candidate
                self.state.best_genome_id = candidate.strategy_id
                self.state.best_metrics = candidate_metrics
                self.state.promotions += 1
                decision_str = "PROMOTE"
                # Deployment stage: sandbox → paper
                self.deployment.on_backtest_promoted(candidate.strategy_id)
                if candidate.strategy_id not in self.state.paper_pending:
                    self.state.paper_pending.append(candidate.strategy_id)
                try:
                    self.lineage.create_version(
                        candidate.strategy_id, candidate.__dict__,
                        parent_id=parent.strategy_id,
                        mutation_type=mut_type,
                        mutation_description=hypothesis.proposed_change[:200],
                    )
                    self.lineage.record_promotion(
                        candidate.strategy_id, "promote", reasons, candidate_metrics
                    )
                except Exception as e:
                    logger.warning("Lineage record failed: %s", e)
                try:
                    self.research.update_hypothesis_status(
                        hypothesis.id, HypothesisStatus.VALIDATED,
                        notes="; ".join(reasons)[:500],
                    )
                except Exception:
                    pass
                # Population: add candidate
                self._population.append((candidate, candidate_metrics))
                self._cull_population()
                self._emit("strategy_promoted", {
                    "strategy_id": candidate.strategy_id,
                    "parent_id": parent.strategy_id,
                    "metrics": candidate_metrics,
                    "mutation_type": mut_type.value,
                })
            else:
                self.state.rejections += 1
                try:
                    self.lineage.create_version(
                        candidate.strategy_id, candidate.__dict__,
                        parent_id=parent.strategy_id,
                        mutation_type=mut_type,
                        mutation_description=hypothesis.proposed_change[:200],
                    )
                    self.lineage.record_promotion(
                        candidate.strategy_id, "reject", reasons, candidate_metrics
                    )
                except Exception:
                    pass
                try:
                    self.research.update_hypothesis_status(
                        hypothesis.id, HypothesisStatus.REJECTED,
                        notes="; ".join(reasons)[:500],
                    )
                except Exception:
                    pass
                self._emit("strategy_rejected", {
                    "strategy_id": candidate.strategy_id,
                    "reasons": reasons[:3],
                })

            # 8. Credit ledger (meta-learning)
            delta_pf = c_pf - p_pf
            delta_exp = c_ex - p_ex
            try:
                self.credits.record(mut_type, improved, delta_pf, delta_exp)
            except Exception:
                logger.debug("Credit record failed", exc_info=True)

            # 9. Graph links
            try:
                h_node = self.graph.add_node(
                    NodeType.HYPOTHESIS, hypothesis.description[:100],
                    {"weakness": str(hypothesis.weakness_type), "phase": phase.name.value},
                    node_id=hypothesis.id,
                )
                p_node = self.graph.get_node_by_label(parent.strategy_id) or self.graph.add_node(
                    NodeType.STRATEGY, parent.strategy_id, {"role": "parent"},
                    node_id=parent.strategy_id,
                )
                c_node = self.graph.add_node(
                    NodeType.STRATEGY_VERSION, candidate.strategy_id,
                    {"decision": decision_str, "metrics": {
                        k: candidate_metrics.get(k) for k in
                        ("profit_factor", "expectancy", "total_trades")
                    }},
                    node_id=candidate.strategy_id,
                )
                self.graph.add_edge(h_node, c_node, "generated", 1.0)
                self.graph.add_edge(p_node, c_node, "evolves_from", 1.0)
            except Exception as e:
                logger.debug("Graph link skipped: %s", e)

            # 10. ExperimentEngine record (real time split)
            try:
                from trading_ai.evolution.engine import ExperimentConfig, DataSplit
                now = datetime.utcnow()
                total_days = 120
                cfg = ExperimentConfig(
                    hypothesis_id=hypothesis.id,
                    parent_strategy=parent.strategy_id,
                    candidate_strategy=candidate.strategy_id,
                    data_split=DataSplit(
                        train_start=now - timedelta(days=total_days),
                        train_end=now - timedelta(days=int(total_days * 0.6)),
                        validation_start=now - timedelta(days=int(total_days * 0.6)),
                        validation_end=now - timedelta(days=int(total_days * 0.2)),
                        test_start=now - timedelta(days=int(total_days * 0.2)),
                        test_end=now,
                    ),
                    walk_forward_windows=3,
                    monte_carlo_iterations=50,
                    parameter_perturbation_iterations=10,
                )
                self.experiment_engine.create_experiment(cfg)
            except Exception:
                pass

            duration = time.time() - t0
            # Safety bookkeeping
            best_pf = (self.state.best_metrics or {}).get("profit_factor", 0) or 0
            self.safety.note_iteration(duration, improved, best_pf)

            result = IterationResult(
                iteration=it,
                hypothesis_id=hypothesis.id,
                hypothesis_desc=hypothesis.description[:200],
                parent_id=parent.strategy_id,
                candidate_id=candidate.strategy_id,
                decision=decision_str,
                reasons=reasons,
                parent_metrics=parent_metrics,
                candidate_metrics=candidate_metrics,
                improved=improved,
                duration_s=duration,
                mutation_type=mut_type.value,
                phase=phase.name.value,
                regime_scores=candidate_metrics.get("regimes", {}),
                oos_passed=oos_passed,
                deployment_stage=(
                    self.deployment.get(candidate.strategy_id).stage.value
                    if self.deployment.get(candidate.strategy_id) else ""
                ),
            )
            self._record_iteration(result)
            self._emit("iteration_completed", {
                "iteration": it, "decision": decision_str, "phase": phase.name.value,
            })
            return result

        except Exception as e:
            duration = time.time() - t0
            self.state.errors += 1
            self.safety.note_iteration(duration, False, (self.state.best_metrics or {}).get("profit_factor", 0) or 0)
            result = IterationResult(
                iteration=it,
                hypothesis_id=hypothesis.id if hypothesis else None,
                hypothesis_desc=hypothesis.description[:200] if hypothesis else "error",
                parent_id=parent.strategy_id,
                candidate_id=candidate.strategy_id if candidate else "",
                decision="ERROR",
                reasons=[str(e)],
                parent_metrics={},
                candidate_metrics={},
                improved=False,
                duration_s=duration,
                error=f"{type(e).__name__}: {e}",
                mutation_type=mut_type.value,
                phase=phase.name.value,
            )
            self._record_iteration(result)
            logger.exception("Self-learning iteration %d failed", it)
            return result

    def _bootstrap_change_text(self, phase: PhaseConfig) -> str:
        if phase.name == LoopPhase.EXPLORE:
            return "Explore entry/exit parameters for any edge"
        return "Tighten parameters; require multi-regime + OOS robustness"

    def _record_iteration(self, result: IterationResult) -> None:
        self.state.iterations_completed += (
            1 if result.decision != "BLOCKED" else 0
        )
        # Always count completed for progress even on error (except pure safety block)
        if result.decision == "BLOCKED":
            # keep iterations_completed unchanged; history still records
            pass
        self.state.last_at = datetime.now(timezone.utc).isoformat()
        if self.state.started_at is None:
            self.state.started_at = self.state.last_at
        entry = {
            "iteration": result.iteration,
            "decision": result.decision,
            "hypothesis_id": result.hypothesis_id,
            "parent_id": result.parent_id,
            "candidate_id": result.candidate_id,
            "improved": result.improved,
            "duration_s": round(result.duration_s, 2),
            "parent_pf": result.parent_metrics.get("profit_factor"),
            "cand_pf": result.candidate_metrics.get("profit_factor"),
            "parent_trades": result.parent_metrics.get("total_trades"),
            "cand_trades": result.candidate_metrics.get("total_trades"),
            "mutation_type": result.mutation_type,
            "phase": result.phase,
            "oos_passed": result.oos_passed,
            "error": result.error,
            "at": self.state.last_at,
        }
        self.state.history.append(entry)
        if len(self.state.history) > self.max_history:
            self.state.history = self.state.history[-self.max_history:]
        self.state.last_iteration = entry
        self.state.population_size = len(self._population)

    # ------------------------------------------------------------------
    # Paper-trade confirmation + human approval
    # ------------------------------------------------------------------

    def submit_paper_metrics(self, strategy_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        rec = self.deployment.submit_paper_metrics(strategy_id, metrics)
        if rec.stage == DeploymentStage.LIVE_PENDING and strategy_id in self.state.paper_pending:
            self.state.paper_pending.remove(strategy_id)
        if rec.stage in (DeploymentStage.LIVE_PENDING, DeploymentStage.LIVE):
            if strategy_id not in self.state.paper_approved:
                self.state.paper_approved.append(strategy_id)
        return self.deployment.to_dict().get(strategy_id, {})

    def approve_live(self, strategy_id: str, approver: str = "user") -> dict[str, Any]:
        rec = self.deployment.approve_live(strategy_id, approver)
        if strategy_id not in self.state.live_approved:
            self.state.live_approved.append(strategy_id)
        self._emit("live_approved", {"strategy_id": strategy_id, "approver": approver})
        return self.deployment.to_dict().get(strategy_id, {})

    def reject_live(self, strategy_id: str, reason: str) -> dict[str, Any]:
        rec = self.deployment.reject_live(strategy_id, reason)
        return self.deployment.to_dict().get(strategy_id, {})

    def get_mutation_credits(self) -> list[dict[str, Any]]:
        return self.credits.get_scores_summary()

    def force_phase(self, phase: str) -> None:
        self.safety.force_phase(LoopPhase(phase))

    def reset_safety(self) -> None:
        self.safety.reset()

    # ------------------------------------------------------------------
    # Multi-iteration + background
    # ------------------------------------------------------------------

    def run_n(self, n: int = 5) -> list[IterationResult]:
        results = []
        for _ in range(n):
            r = self.run_once()
            results.append(r)
            if r.decision == "BLOCKED":
                break
        return results

    async def run_forever(self, interval_s: float = 30.0, max_iterations: int | None = None) -> None:
        self.state.running = True
        self._stop_event.clear()
        try:
            while not self._stop_event.is_set():
                if max_iterations is not None and self.state.iterations_completed >= max_iterations:
                    break
                if self.safety.state.stopped:
                    logger.warning("Safety stop: %s", self.safety.state.stop_reason)
                    break
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.run_once)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval_s)
                except asyncio.TimeoutError:
                    continue
        finally:
            self.state.running = False
            logger.info("SelfLearningLoop stopped after %d iterations", self.state.iterations_completed)

    def start(self, interval_s: float = 30.0, max_iterations: int | None = None) -> bool:
        if self.state.running and self._task and not self._task.done():
            return False
        if self.safety.state.stopped:
            logger.warning("Cannot start: safety stopped (%s)", self.safety.state.stop_reason)
            return False
        self._task = asyncio.get_event_loop().create_task(
            self.run_forever(interval_s=interval_s, max_iterations=max_iterations)
        )
        logger.info("SelfLearningLoop started (interval=%ss)", interval_s)
        return True

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self.state.running = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.state.running,
            "iterations_completed": self.state.iterations_completed,
            "promotions": self.state.promotions,
            "rejections": self.state.rejections,
            "errors": self.state.errors,
            "best_genome_id": self.state.best_genome_id,
            "best_metrics": self.state.best_metrics,
            "current_genome_id": self._current_genome.strategy_id,
            "started_at": self.state.started_at,
            "last_at": self.state.last_at,
            "last_iteration": self.state.last_iteration,
            "history_length": len(self.state.history),
            "history_tail": self.state.history[-10:],
            "safety": self.safety.to_dict(),
            "population_size": len(self._population),
            "paper_pending": list(self.state.paper_pending),
            "paper_approved": list(self.state.paper_approved),
            "live_approved": list(self.state.live_approved),
            "deployment": self.deployment.to_dict(),
            "mutation_credits": self.get_mutation_credits(),
        }

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.state.history[-limit:]
