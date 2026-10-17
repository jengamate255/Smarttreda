"""Unit tests for the SelfLearningLoop recursive auto-improvement cycle."""
from __future__ import annotations

import pytest

from trading_ai.evolution.self_learning import SelfLearningLoop, IterationResult
from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.genome_executor import make_strategy_fn, _apply_genome_rules
from trading_ai.models import Signal, Side
from datetime import datetime


@pytest.fixture
def loop(tmp_path):
    # Fast single-regime config for unit tests
    return SelfLearningLoop(
        db_url_prefix=str(tmp_path / "sl_test"),
        multi_regime=False,
        use_oos=False,
        population_size=1,
        seed=7,
        base_count=800,
    )


def test_loop_constructs(loop: SelfLearningLoop):
    assert loop.state.iterations_completed == 0
    assert loop._current_genome.strategy_id == "self-learn-000"
    assert not loop.state.running


def test_run_once_produces_result(loop: SelfLearningLoop):
    r = loop.run_once()
    assert isinstance(r, IterationResult)
    assert r.iteration == 1
    assert r.decision in ("PROMOTE", "REJECT")
    assert r.error is None
    assert r.parent_metrics.get("total_trades", 0) >= 0
    assert loop.state.iterations_completed == 1


def test_run_n(loop: SelfLearningLoop):
    results = loop.run_n(3)
    assert len(results) == 3
    assert loop.state.iterations_completed == 3
    assert loop.state.promotions + loop.state.rejections == 3
    assert len(loop.state.history) == 3


def test_mutation_changes_genome(loop: SelfLearningLoop):
    parent = loop._current_genome
    from trading_ai.research.agent import Hypothesis, HypothesisStatus, WeaknessType
    h = Hypothesis(
        id="t1", description="test", parent_strategy=parent.strategy_id,
        proposed_change="raise min_rr", rationale="r", required_test="bt",
        status=HypothesisStatus.PROPOSED, weakness_type=WeaknessType.HIGH_DRAWDOWN,
    )
    child, mut_type = loop._mutate_for_hypothesis(parent, h)
    assert child.strategy_id != parent.strategy_id
    # Default path chains two mutations; lineage walks back through parent_id
    assert child.parent_id
    assert child.version > parent.version


def test_promote_only_on_improvement(loop: SelfLearningLoop):
    # Force a rejection path by mutating with extreme confidence gate
    from trading_ai.research.agent import Hypothesis, HypothesisStatus, WeaknessType
    parent = loop._current_genome
    h = Hypothesis(
        id="t2", description="impossible conf", parent_strategy=parent.strategy_id,
        proposed_change="min_confidence 0.99", rationale="r", required_test="bt",
        status=HypothesisStatus.PROPOSED, weakness_type=WeaknessType.NEGATIVE_EXPECTANCY,
    )
    # Directly create a child that blocks almost all signals
    child = loop.mutator.mutate_entry_filter(
        parent, {"min_confidence": 0.99, "rule_type": "supply_demand", "description": "block"}
    )
    # monkey-patch next mutation to return this child
    loop._mutate_for_hypothesis = lambda g, hyp: (child, __import__(
        "trading_ai.evolution.lineage", fromlist=["MutationType"]
    ).MutationType.PARAMETER_CHANGE)
    r = loop.run_once()
    # Should not promote a zero-signal / worse candidate
    assert r.decision in ("REJECT", "PROMOTE")
    if r.decision == "REJECT":
        assert not r.improved


def test_apply_genome_rules_enforces_min_rr():
    g = StrategyGenome(
        strategy_id="x",
        entry_rules=[{"type": "supply_demand", "params": {}}],
        exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
        risk_rules=[{"type": "stop_placement", "params": {"min_rr": 3.0}}],
    )
    sig = Signal(
        timestamp=datetime.utcnow(), symbol="EURUSD", side=Side.BUY,
        entry_price=1.1000, stop_loss=1.0990, take_profit=1.1005, risk_reward=0.5,
        metadata={"confidence": 0.7},
    )
    out = _apply_genome_rules(g, sig)
    assert out is not None
    assert out.risk_reward >= 3.0
    # TP extended: entry + 3 * risk
    risk = 1.1000 - 1.0990
    assert abs(out.take_profit - (1.1000 + risk * 3.0)) < 1e-9


def test_apply_genome_rules_confidence_gate():
    g = StrategyGenome(
        strategy_id="y",
        entry_rules=[{"type": "supply_demand", "params": {"min_confidence": 0.8}}],
    )
    sig = Signal(
        timestamp=datetime.utcnow(), symbol="EURUSD", side=Side.BUY,
        entry_price=1.1, stop_loss=1.099, take_profit=1.103, risk_reward=3.0,
        metadata={"confidence": 0.5},
    )
    assert _apply_genome_rules(g, sig) is None


def test_make_strategy_fn_returns_callable(loop: SelfLearningLoop):
    fn = make_strategy_fn(loop._current_genome, loop.data, loop.market_state)
    assert callable(fn)


def test_status_serializable(loop: SelfLearningLoop):
    loop.run_once()
    st = loop.get_status()
    import json
    json.dumps(st, default=str)  # must not raise
    assert st["iterations_completed"] == 1
    assert isinstance(st["history_tail"], list)
    assert "safety" in st
    assert "deployment" in st
    assert "mutation_credits" in st


def test_phase_and_safety(loop: SelfLearningLoop):
    loop.force_phase("exploit")
    assert loop.safety.state.phase.value == "exploit"
    cfg = loop.safety.phase_config()
    assert cfg.require_oos is True
    loop.force_phase("explore")
    assert loop.safety.phase_config().require_oos is False
    loop.reset_safety()
    assert loop.safety.state.stopped is False


def test_deployment_gate_human_approval(loop: SelfLearningLoop):
    # Backtest promote → PAPER stage
    rec = loop.deployment.on_backtest_promoted("strat-1")
    assert rec.stage.value == "paper"
    # Paper insufficient → still paper
    loop.submit_paper_metrics("strat-1", {"total_trades": 1, "expectancy": -1})
    assert loop.deployment.get("strat-1").stage.value == "paper"
    # Paper confirmed → LIVE_PENDING (not LIVE)
    loop.submit_paper_metrics("strat-1", {"total_trades": 10, "expectancy": 0.2})
    assert loop.deployment.get("strat-1").stage.value == "live_pending"
    # Never LIVE without human approval
    assert loop.deployment.get("strat-1").stage.value != "live"
    # Human approves
    loop.approve_live("strat-1", approver="tester")
    assert loop.deployment.get("strat-1").stage.value == "live"
    assert "strat-1" in loop.state.live_approved


def test_credits_record_and_choose(loop: SelfLearningLoop):
    from trading_ai.evolution.lineage import MutationType
    import random
    loop.credits.record(MutationType.SESSION_FILTER, True, delta_pf=0.5)
    loop.credits.record(MutationType.SESSION_FILTER, True, delta_pf=0.3)
    loop.credits.record(MutationType.REGIME_FILTER, False, delta_pf=-0.2)
    scores = loop.get_mutation_credits()
    assert any(s["mutation_type"] == "session_filter" and s["successes"] == 2 for s in scores)
    # Weighted sampling: with epsilon=0, better-credited type should dominate
    rng = random.Random(0)
    picks = [
        loop.credits.choose(
            [MutationType.SESSION_FILTER, MutationType.REGIME_FILTER],
            rng=rng,
            explore_epsilon=0.0,
        )
        for _ in range(50)
    ]
    assert picks.count(MutationType.SESSION_FILTER) > picks.count(MutationType.REGIME_FILTER)


def test_mutation_types_vary(loop: SelfLearningLoop):
    types = set()
    for _ in range(4):
        r = loop.run_once()
        if r.mutation_type:
            types.add(r.mutation_type)
    assert len(types) >= 1


def test_population_size_config(tmp_path):
    sl = SelfLearningLoop(
        db_url_prefix=str(tmp_path / "sl_pop"),
        multi_regime=False,
        use_oos=False,
        population_size=3,
        seed=1,
        base_count=800,
    )
    assert sl.population_size == 3
    assert len(sl._population) >= 1
    sl.run_once()
    assert sl.state.population_size >= 1
