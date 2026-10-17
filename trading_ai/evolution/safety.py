"""Safety rails for the self-learning loop: kill switches, phases, deployment gates."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LoopPhase(str, Enum):
    EXPLORE = "explore"
    EXPLOIT = "exploit"


class DeploymentStage(str, Enum):
    SANDBOX = "sandbox"       # auto-promote allowed (backtest only)
    PAPER = "paper"           # awaiting paper-trade confirmation
    PAPER_APPROVED = "paper_approved"
    LIVE_PENDING = "live_pending"  # requires human approval
    LIVE = "live"


@dataclass
class PhaseConfig:
    name: LoopPhase
    min_improvement_eps: float = 1e-9
    require_oos: bool = False
    require_regimes: int = 1          # regimes that must show non-worse fitness
    promote_epsilon_pf: float = 1.01  # PF must beat parent * this
    explore_epsilon: float = 0.2      # mutator explore rate
    min_trades: int = 1


EXPLORE_PHASE = PhaseConfig(
    name=LoopPhase.EXPLORE,
    min_improvement_eps=1e-9,
    require_oos=False,
    require_regimes=1,
    promote_epsilon_pf=1.005,
    explore_epsilon=0.35,
    min_trades=5,
)

EXPLOIT_PHASE = PhaseConfig(
    name=LoopPhase.EXPLOIT,
    min_improvement_eps=1e-6,
    require_oos=True,
    require_regimes=2,          # improve / not-worse on >= 2 of 3 regimes
    promote_epsilon_pf=1.02,
    explore_epsilon=0.1,
    min_trades=10,
)


@dataclass
class SafetyLimits:
    max_stagnation: int = 15           # consecutive non-promotions before phase change / stop
    max_iterations: int | None = None
    max_daily_iterations: int = 200
    max_daily_compute_s: float = 3600.0
    circuit_breaker_drawdown_pct: float = 30.0  # stop if best PF drops this % from peak
    min_best_pf_floor: float = 0.5


@dataclass
class SafetyState:
    phase: LoopPhase = LoopPhase.EXPLORE
    stagnation: int = 0
    peak_best_pf: float = 0.0
    daily_iterations: int = 0
    daily_compute_s: float = 0.0
    day_key: str = ""
    stopped: bool = False
    stop_reason: str | None = None
    phase_switches: int = 0
    last_phase_switch_at: str | None = None

    def roll_day(self) -> None:
        key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if key != self.day_key:
            self.day_key = key
            self.daily_iterations = 0
            self.daily_compute_s = 0.0


class SafetyController:
    """Evaluates kill-switch conditions and manages explore/exploit phase transitions."""

    def __init__(self, limits: SafetyLimits | None = None):
        self.limits = limits or SafetyLimits()
        self.state = SafetyState()

    def phase_config(self) -> PhaseConfig:
        return EXPLORE_PHASE if self.state.phase == LoopPhase.EXPLORE else EXPLOIT_PHASE

    def can_run_iteration(self) -> tuple[bool, str | None]:
        self.state.roll_day()
        if self.state.stopped:
            return False, self.state.stop_reason or "stopped"
        if self.limits.max_iterations is not None and (
            # iterations_completed checked by caller
            False
        ):
            return False, "max_iterations"
        if self.state.daily_iterations >= self.limits.max_daily_iterations:
            self._stop("daily_iteration_cap")
            return False, "daily_iteration_cap"
        if self.state.daily_compute_s >= self.limits.max_daily_compute_s:
            self._stop("daily_compute_cap")
            return False, "daily_compute_cap"
        return True, None

    def note_iteration(self, duration_s: float, improved: bool, best_pf: float) -> None:
        self.state.roll_day()
        self.state.daily_iterations += 1
        self.state.daily_compute_s += duration_s
        if improved:
            self.state.stagnation = 0
            if best_pf > self.state.peak_best_pf:
                self.state.peak_best_pf = best_pf
        else:
            self.state.stagnation += 1
        # Circuit breaker: best degraded sharply from peak
        if (
            self.state.peak_best_pf > 0
            and best_pf < self.state.peak_best_pf * (1 - self.limits.circuit_breaker_drawdown_pct / 100.0)
            and best_pf < self.limits.min_best_pf_floor
        ):
            self._stop("circuit_breaker_best_pf_degraded")
        # Phase transitions on stagnation
        if self.state.stagnation >= self.limits.max_stagnation:
            self._toggle_phase()

    def _toggle_phase(self) -> None:
        if self.state.phase == LoopPhase.EXPLORE:
            self.state.phase = LoopPhase.EXPLOIT
        else:
            # Prolonged exploit stagnation → back to explore with reset pressure
            self.state.phase = LoopPhase.EXPLORE
        self.state.stagnation = 0
        self.state.phase_switches += 1
        self.state.last_phase_switch_at = datetime.now(timezone.utc).isoformat()

    def force_phase(self, phase: LoopPhase) -> None:
        self.state.phase = phase
        self.state.stagnation = 0
        self.state.phase_switches += 1
        self.state.last_phase_switch_at = datetime.now(timezone.utc).isoformat()

    def _stop(self, reason: str) -> None:
        self.state.stopped = True
        self.state.stop_reason = reason

    def reset(self) -> None:
        self.state = SafetyState()

    def to_dict(self) -> dict[str, Any]:
        s = self.state
        return {
            "phase": s.phase.value,
            "stagnation": s.stagnation,
            "peak_best_pf": round(s.peak_best_pf, 4),
            "daily_iterations": s.daily_iterations,
            "daily_compute_s": round(s.daily_compute_s, 2),
            "stopped": s.stopped,
            "stop_reason": s.stop_reason,
            "phase_switches": s.phase_switches,
            "limits": {
                "max_stagnation": self.limits.max_stagnation,
                "max_daily_iterations": self.limits.max_daily_iterations,
                "max_daily_compute_s": self.limits.max_daily_compute_s,
                "circuit_breaker_drawdown_pct": self.limits.circuit_breaker_drawdown_pct,
            },
        }


@dataclass
class DeploymentRecord:
    strategy_id: str
    stage: DeploymentStage = DeploymentStage.SANDBOX
    paper_metrics: dict[str, Any] = field(default_factory=dict)
    approved_by: str | None = None
    approved_at: str | None = None
    notes: str = ""


class DeploymentGate:
    """Human-in-the-loop: sandbox auto, paper needs results, live needs explicit approval."""

    def __init__(self):
        self._records: dict[str, DeploymentRecord] = {}

    def ensure(self, strategy_id: str) -> DeploymentRecord:
        if strategy_id not in self._records:
            self._records[strategy_id] = DeploymentRecord(strategy_id=strategy_id)
        return self._records[strategy_id]

    def on_backtest_promoted(self, strategy_id: str) -> DeploymentRecord:
        rec = self.ensure(strategy_id)
        if rec.stage == DeploymentStage.SANDBOX:
            rec.stage = DeploymentStage.PAPER
            rec.notes = "Backtest promoted; awaiting paper confirmation"
        return rec

    def submit_paper_metrics(self, strategy_id: str, metrics: dict[str, Any]) -> DeploymentRecord:
        rec = self.ensure(strategy_id)
        rec.paper_metrics = metrics
        exp = metrics.get("expectancy", 0)
        trades = metrics.get("total_trades", 0)
        if trades >= 5 and exp > 0:
            rec.stage = DeploymentStage.LIVE_PENDING
            rec.notes = "Paper confirmed; awaiting human live approval"
        else:
            rec.stage = DeploymentStage.PAPER
            rec.notes = f"Paper insufficient (trades={trades}, exp={exp})"
        return rec

    def approve_live(self, strategy_id: str, approver: str) -> DeploymentRecord:
        rec = self.ensure(strategy_id)
        if rec.stage not in (DeploymentStage.LIVE_PENDING, DeploymentStage.PAPER_APPROVED, DeploymentStage.PAPER):
            raise ValueError(f"Cannot approve live from stage {rec.stage.value}")
        rec.stage = DeploymentStage.LIVE
        rec.approved_by = approver
        rec.approved_at = datetime.now(timezone.utc).isoformat()
        rec.notes = "Human-approved for live"
        return rec

    def reject_live(self, strategy_id: str, reason: str) -> DeploymentRecord:
        rec = self.ensure(strategy_id)
        rec.stage = DeploymentStage.PAPER
        rec.notes = f"Live rejected: {reason}"
        return rec

    def get(self, strategy_id: str) -> DeploymentRecord | None:
        return self._records.get(strategy_id)

    def can_auto_deploy(self, strategy_id: str) -> bool:
        """Auto-deploy only within sandbox/paper — never to live without approval."""
        rec = self._records.get(strategy_id)
        return rec is None or rec.stage in (
            DeploymentStage.SANDBOX,
            DeploymentStage.PAPER,
            DeploymentStage.PAPER_APPROVED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            sid: {
                "stage": r.stage.value,
                "paper_metrics": r.paper_metrics,
                "approved_by": r.approved_by,
                "approved_at": r.approved_at,
                "notes": r.notes,
            }
            for sid, r in self._records.items()
        }
