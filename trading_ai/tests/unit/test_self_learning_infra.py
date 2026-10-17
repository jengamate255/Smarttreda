"""Tests for mutation credits, safety controller, and paper→journal bridge."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_ai.evolution.credits import MutationCreditLedger
from trading_ai.evolution.lineage import MutationType
from trading_ai.evolution.safety import (
    SafetyController,
    SafetyLimits,
    LoopPhase,
    DeploymentGate,
    DeploymentStage,
)


@pytest.fixture
def ledger(tmp_path):
    return MutationCreditLedger(db_url=f"sqlite:///{tmp_path}/credits.db")


def test_credit_ledger_records(ledger: MutationCreditLedger):
    ledger.record(MutationType.PARAMETER_CHANGE, True, delta_pf=0.2)
    ledger.record(MutationType.PARAMETER_CHANGE, False, delta_pf=-0.1)
    ledger.record(MutationType.SESSION_FILTER, True, delta_pf=0.5)
    scores = {s.mutation_type: s for s in ledger.scores()}
    assert scores[MutationType.PARAMETER_CHANGE].attempts == 2
    assert scores[MutationType.PARAMETER_CHANGE].successes == 1
    assert scores[MutationType.SESSION_FILTER].success_rate == 1.0


def test_credit_choose_prefers_better(ledger: MutationCreditLedger):
    for _ in range(5):
        ledger.record(MutationType.SESSION_FILTER, True, delta_pf=0.4)
    for _ in range(5):
        ledger.record(MutationType.RISK_MANAGEMENT, False, delta_pf=-0.3)
    import random
    rng = random.Random(0)
    picks = [
        ledger.choose(
            [MutationType.SESSION_FILTER, MutationType.RISK_MANAGEMENT],
            rng=rng, explore_epsilon=0.0,
        )
        for _ in range(20)
    ]
    assert picks.count(MutationType.SESSION_FILTER) > picks.count(MutationType.RISK_MANAGEMENT)


def test_safety_daily_cap():
    sc = SafetyController(SafetyLimits(max_daily_iterations=2, max_daily_compute_s=9999))
    assert sc.can_run_iteration()[0] is True
    sc.note_iteration(1.0, improved=False, best_pf=1.0)
    sc.note_iteration(1.0, improved=False, best_pf=1.0)
    ok, reason = sc.can_run_iteration()
    assert ok is False
    assert reason == "daily_iteration_cap"
    assert sc.state.stopped


def test_safety_phase_transition_on_stagnation():
    sc = SafetyController(SafetyLimits(max_stagnation=3))
    assert sc.state.phase == LoopPhase.EXPLORE
    for _ in range(3):
        sc.note_iteration(0.1, improved=False, best_pf=1.0)
    assert sc.state.phase == LoopPhase.EXPLOIT
    assert sc.state.phase_switches == 1


def test_safety_circuit_breaker():
    sc = SafetyController(SafetyLimits(
        max_stagnation=100,
        circuit_breaker_drawdown_pct=20.0,
        min_best_pf_floor=1.0,
    ))
    sc.note_iteration(0.1, improved=True, best_pf=2.0)  # peak=2.0
    # Drop below floor and >20% from peak → stop
    sc.note_iteration(0.1, improved=False, best_pf=0.5)
    assert sc.state.stopped
    assert sc.state.stop_reason == "circuit_breaker_best_pf_degraded"


def test_deployment_gate_flow():
    g = DeploymentGate()
    g.on_backtest_promoted("s1")
    assert g.get("s1").stage == DeploymentStage.PAPER
    g.submit_paper_metrics("s1", {"total_trades": 10, "expectancy": 0.15})
    assert g.get("s1").stage == DeploymentStage.LIVE_PENDING
    # Not live yet
    assert g.get("s1").stage != DeploymentStage.LIVE
    g.approve_live("s1", approver="alice")
    assert g.get("s1").stage == DeploymentStage.LIVE
    assert g.get("s1").approved_by == "alice"


def test_paper_journal_bridge(tmp_path):
    from trading_ai.execution.paper import PaperBroker, Position, PositionSide
    from trading_ai.execution.paper_journal import PaperJournalBridge, position_to_trade_entry
    from trading_ai.memory.journal import TradeJournal
    from uuid import uuid4

    journal = TradeJournal(db_url=f"sqlite:///{tmp_path}/j.db")
    broker = PaperBroker({"initial_balance": 10000.0})
    bridge = PaperJournalBridge(broker, journal, strategy_id="test-strat")
    assert broker.on_position_closed is not None

    pos = Position(
        position_id=uuid4(),
        symbol="EURUSD",
        side=PositionSide.LONG,
        volume=0.1,
        entry_price=1.1000,
        stop_loss=1.0990,
        take_profit=1.1030,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        last_update=datetime.now(timezone.utc).replace(tzinfo=None),
        metadata={"strategy_id": "test-strat", "session": "london", "regime": "trending_up"},
        realized_pnl=12.5,
    )
    # Fire hook as broker would
    broker.on_position_closed(pos, exit_price=1.1025)
    assert bridge.recorded == 1

    entry = position_to_trade_entry(pos, 1.1025, "test-strat")
    assert entry.strategy_version == "test-strat"
    assert entry.symbol == "EURUSD"
    assert entry.outcome.value == "win"
    assert entry.pnl_currency == 12.5
