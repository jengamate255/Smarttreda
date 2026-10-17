"""Test market state, strategies, genome, backtest, risk, and subsystems."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
from trading_ai.data.generator import SyntheticDataGenerator
from trading_ai.market_state import MarketStateAgent, classify_session, classify_volatility, classify_market_regime, Session

def main():
    results = []

    # --- Market State ---
    print("=" * 60)
    print("[MARKET STATE]")
    gen = SyntheticDataGenerator(seed=42)
    df = gen.generate("EURUSD", "H1", initial_price=1.1000)

    agent = MarketStateAgent()
    agent.configure({})
    state = agent.process(df)
    for k in ["trend_structure", "volatility_regime", "market_regime"]:
        print(f"  {k}: {state[k]}")
    for k in ["swing_highs", "swing_lows", "bos_events", "choch_events", "supply_zones", "demand_zones", "support_resistance"]:
        print(f"  {k}: {len(state[k])} items")
    results.append(("market_state", True))

    # Session classification
    print()
    print("  Session classification:")
    session_tests = [
        (pd.Timestamp("2024-01-01 03:00", tz="UTC"), Session.ASIAN),
        (pd.Timestamp("2024-01-01 10:00", tz="UTC"), Session.LONDON),
        (pd.Timestamp("2024-01-01 15:00", tz="UTC"), Session.OVERLAP),
        (pd.Timestamp("2024-01-01 19:00", tz="UTC"), Session.NEW_YORK),
        (pd.Timestamp("2024-01-01 23:00", tz="UTC"), Session.OFF),
    ]
    all_pass = True
    for ts, expected in session_tests:
        actual = classify_session(ts)
        ok = actual == expected
        if not ok:
            all_pass = False
        print(f"    {ts.hour:02d}:00 -> {actual.value:8s} {'PASS' if ok else f'FAIL (expected {expected.value})'}")
    results.append(("sessions", all_pass))

    print(f"  Volatility: {classify_volatility(df).value}")
    print(f"  Regime: {classify_market_regime(df).value}")

    # --- Strategies ---
    print()
    print("=" * 60)
    print("[STRATEGIES]")
    from trading_ai.strategies.genome.schema import StrategyGenome
    from trading_ai.strategies.genome.mutator import StrategyMutator
    from trading_ai.strategies.supply_demand import SupplyDemandStrategy
    from trading_ai.strategies.trend import TrendAgent
    from trading_ai.strategies.breakout import BreakoutAgent
    from trading_ai.strategies.momentum import MomentumAgent
    from trading_ai.strategies.mean_reversion import MeanReversionAgent
    from trading_ai.strategies.volatility import VolatilityAgent

    data = {tf: gen.generate("EURUSD", tf, initial_price=1.1000) for tf in ["H4", "H1", "M15", "M5"]}
    genome = StrategyGenome(
        strategy_id="test-001", market=["EURUSD", "*"],
        timeframes=["H4", "H1", "M15", "M5"],
        entry_rules=[{"type": "supply_demand", "params": {"lookback": 50}}],
        exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
        filters=[],
    )
    ms = {"symbol": "EURUSD", "session": "london", "volatility_regime": "normal", "atr": 0.001}

    strat_ok = True
    for name, cls in [("SupplyDemand", SupplyDemandStrategy), ("Trend", TrendAgent),
                       ("Breakout", BreakoutAgent), ("Momentum", MomentumAgent),
                       ("MeanRev", MeanReversionAgent), ("Volatility", VolatilityAgent)]:
        try:
            sig = cls(genome).analyze(data, ms)
            if sig:
                d = sig.direction.value if hasattr(sig.direction, "value") else sig.direction
                print(f"  {name:14s}: SIGNAL {d} entry={sig.entry_price:.5f} RR={sig.risk_reward_ratio:.2f}")
            else:
                print(f"  {name:14s}: no signal (ran without error)")
        except Exception as e:
            strat_ok = False
            print(f"  {name:14s}: ERROR {type(e).__name__}: {e}")
    results.append(("strategies", strat_ok))

    # Genome mutation
    print()
    print("  Genome mutation:")
    m = StrategyMutator()
    child = m.mutate_volatility_filter(genome, {"min_atr": 0.0005, "max_atr": 0.005})
    parent_ok = len(genome.filters) == 0
    child_ok = len(child.filters) == 1
    print(f"    Parent filters={len(genome.filters)} (expect 0): {'PASS' if parent_ok else 'FAIL'}")
    print(f"    Child filters={len(child.filters)} (expect 1): {'PASS' if child_ok else 'FAIL'}")
    try:
        m.mutate_volatility_filter(genome, {"max_risk_per_trade": 0.99})
        prot_ok = False
        print("    Protected key: FAIL (should have raised)")
    except Exception:
        prot_ok = True
        print("    Protected key rejected: PASS")
    results.append(("genome_mutation", parent_ok and child_ok and prot_ok))

    # --- Backtest + Risk + Performance ---
    print()
    print("=" * 60)
    print("[BACKTEST + RISK + PERFORMANCE]")
    from trading_ai.backtesting.engine import BacktestEngine
    from trading_ai.models import BacktestConfig, Signal, Side
    from trading_ai.evaluation.performance import PerformanceAnalyzer
    from trading_ai.risk.engine import RiskAgent
    from datetime import datetime

    dfh1 = data["H1"]

    def simple_strategy(d, s):
        if len(d) < 10 or len(d) % 50 != 0:
            return None
        price = float(d["close"].iloc[-1])
        return Signal(timestamp=datetime.now(), symbol="EURUSD", side=Side.BUY,
                      entry_price=price, stop_loss=price * 0.995,
                      take_profit=price * 1.015, risk_reward=3.0)

    config = BacktestConfig(initial_capital=100000, risk_per_trade=0.005, seed=42)
    t0 = time.time()
    engine = BacktestEngine(simple_strategy, dfh1, config)
    result = engine.run()
    t_bt = time.time() - t0

    print(f"  Backtest: {len(result.trades)} trades in {t_bt:.2f}s")
    print(f"    equity={result.equity_curve.iloc[-1]:,.2f} dd={result.metrics.max_drawdown:.2%} "
          f"wr={result.metrics.win_rate:.1%} pf={result.metrics.profit_factor:.2f} sharpe={result.metrics.sharpe_ratio:.2f}")
    bt_ok = len(result.trades) > 0
    results.append(("backtest", bt_ok))

    # Risk engine
    risk = RiskAgent(config)
    a = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.0945, 1.1165, 100000)
    r = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.0990, 1.1010, 100000)
    r2 = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.1000, 1.1100, 100000)
    print(f"  Risk: valid_approved={a.approved} low_rr_rejected={not r.approved} zero_stop_rejected={not r2.approved}")
    if a.approved:
        print(f"    size={a.position_size:.4f} risk={a.risk_amount:.2f} rr={a.risk_reward:.2f}")
    risk_ok = a.approved and not r.approved and not r2.approved
    results.append(("risk_engine", risk_ok))

    # Performance
    pm = PerformanceAnalyzer(config).calculate(result.trades, result.equity_curve, result.drawdown_series)
    print(f"  Performance: trades={pm.total_trades} wr={pm.win_rate:.1%} pf={pm.profit_factor:.2f} "
          f"sharpe={pm.sharpe_ratio:.2f} sortino={pm.sortino_ratio:.2f} dd={pm.max_drawdown:.2%}")
    results.append(("performance", pm.total_trades > 0))

    # --- Subsystems ---
    print()
    print("=" * 60)
    print("[SUBSYSTEMS]")
    try:
        from trading_ai.evolution.gatekeeper import StrategyGatekeeper
        gk = StrategyGatekeeper()
        print(f"  Gatekeeper: OK (min_sharpe={gk.policy.min_sharpe}, min_pf={gk.policy.min_profit_factor})")
        results.append(("gatekeeper", True))
    except Exception as e:
        print(f"  Gatekeeper: FAIL {e}")
        results.append(("gatekeeper", False))

    try:
        from trading_ai.evolution.lineage import StrategyLineage
        sl = StrategyLineage()
        v1 = sl.create_version("SD-001", "Base", None, {})
        v2 = sl.create_version("SD-014", "Vol filter", "SD-001", {"mutation": "vol"})
        print(f"  Lineage: OK ({v1.strategy_id} -> {v2.strategy_id})")
        results.append(("lineage", True))
    except Exception as e:
        print(f"  Lineage: FAIL {e}")
        results.append(("lineage", False))

    try:
        from trading_ai.memory.journal import TradeJournal, TradeEntry
        from datetime import timezone
        tj = TradeJournal()
        te = TradeEntry(
            timestamp=datetime.now(timezone.utc), strategy_version="SD-001",
            market_regime="TREND", symbol="EURUSD", timeframe="M15", session="london",
            direction="BUY", entry_price=1.1000, stop_loss=1.0950, take_profit=1.1150,
            exit_price=1.1150, r_multiple=3.0, pnl=150.0, exit_reason="TAKE_PROFIT",
        )
        tid = tj.record_trade(te)
        trades = tj.query_trades(symbol="EURUSD")
        print(f"  Journal: OK (recorded trade {tid}, query returned {len(trades)})")
        results.append(("journal", len(trades) >= 1))
    except Exception as e:
        print(f"  Journal: FAIL {e}")
        results.append(("journal", False))

    try:
        from trading_ai.memory.graph import ResearchGraph, NodeType
        rg = ResearchGraph()
        n1 = rg.add_node(NodeType.STRATEGY, "SD-001", {})
        n2 = rg.add_node(NodeType.HYPOTHESIS, "H-001", {})
        print(f"  Research Graph: OK (nodes {n1}, {n2})")
        results.append(("graph", True))
    except Exception as e:
        print(f"  Research Graph: FAIL {e}")
        results.append(("graph", False))

    try:
        from trading_ai.execution.paper import PaperBroker
        pb = PaperBroker(initial_balance=100000.0)
        print(f"  Paper Broker: OK (balance={pb.balance})")
        results.append(("paper_broker", True))
    except Exception as e:
        print(f"  Paper Broker: FAIL {e}")
        results.append(("paper_broker", False))

    try:
        from trading_ai.monitoring.health import HealthMonitor
        hm = HealthMonitor()
        print(f"  Health Monitor: OK")
        results.append(("health", True))
    except Exception as e:
        print(f"  Health Monitor: FAIL {e}")
        results.append(("health", False))

    try:
        from trading_ai.agents.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        print(f"  Orchestrator: OK")
        results.append(("orchestrator", True))
    except Exception as e:
        print(f"  Orchestrator: FAIL {e}")
        results.append(("orchestrator", False))

    try:
        from trading_ai.research.agent import ResearchAgent
        ra = ResearchAgent()
        print(f"  Research Agent: OK")
        results.append(("research_agent", True))
    except Exception as e:
        print(f"  Research Agent: FAIL {e}")
        results.append(("research_agent", False))

    # --- Summary ---
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  {name:25s} {status}")
    print()
    print(f"TOTAL: {sum(1 for _, ok in results if ok)}/{len(results)} passed")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
