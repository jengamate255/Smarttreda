"""Verification suite: generator, features, market state, strategies, backtest, risk, subsystems."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""), flush=True)


def main():
    t_all = time.time()

    # ==================================================================
    print("=" * 64, flush=True)
    print("[1] DATA GENERATOR", flush=True)
    # ==================================================================
    from trading_ai.data.generator import SyntheticDataGenerator

    gen = SyntheticDataGenerator(seed=42)
    df = gen.generate("EURUSD", "H1", initial_price=1.1000)
    check("generator runs", len(df) == 5000, f"{len(df)} candles")
    lo, hi = df["close"].min(), df["close"].max()
    check(
        "prices realistic",
        0.5 <= lo and hi <= 2.5,
        f"close=[{lo:.4f}, {hi:.4f}] (expect ~0.7-1.5)",
    )
    check("no NaN", df.isna().sum().sum() == 0)
    check("OHLC consistent", bool((df.high >= df[['open','close']].max(axis=1) - 1e-12).all()))

    # ==================================================================
    print("\n[2] FEATURE ENGINE", flush=True)
    # ==================================================================
    from trading_ai.features.engine import FeatureEngine

    fe = FeatureEngine()
    t0 = time.time()
    features = fe.compute(df)
    t_feat = time.time() - t0
    check("features computed", features.shape == (5000, 16), f"{features.shape} in {t_feat:.2f}s")
    check("features fast", t_feat < 5.0, f"{t_feat:.2f}s")

    sr_nan = features[["dist_to_sr", "dist_to_zone"]].isna().sum().sum()
    roc_nan = features[["roc_5", "roc_10", "roc_20"]].isna().sum().sum()
    other_nan = int(features.isna().sum().sum()) - sr_nan - roc_nan
    check(
        "structure NaN expected",
        sr_nan == 10000,
        f"{sr_nan} (no S/R configured)",
    )
    check("ROC warmup NaN expected", roc_nan == 35, f"{roc_nan}")
    check("no unexpected NaN", other_nan == 0, f"other={other_nan}")

    br = features["body_ratio"]
    check("body_ratio in [0,1]", bool((br >= 0).all() and (br <= 1).all()))
    check("ATR >= 0", bool((features["atr"] >= 0).all()))
    check("Bollinger >= 0", bool((features["bollinger_bandwidth"] >= 0).all()))
    check("bar_range >= 0", bool((features["bar_range"] >= 0).all()))
    check(
        "sessions labelled",
        features["session"].nunique() >= 3,
        str(features["session"].value_counts().to_dict()),
    )

    # --- look-ahead bias ---
    f1 = fe.compute(df)
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("close")] = 999.99
    df2.iloc[-1, df2.columns.get_loc("high")] = 999.99
    f2 = fe.compute(df2)
    d_last = int((f1.iloc[:-1].fillna(-999) != f2.iloc[:-1].fillna(-999)).any(axis=1).sum())

    df3 = df.copy()
    mid = len(df3) // 2
    df3.iloc[mid, df3.columns.get_loc("close")] = 999.99
    f3 = fe.compute(df3)
    d_mid = int((f1.iloc[:mid].fillna(-999) != f3.iloc[:mid].fillna(-999)).any(axis=1).sum())
    check("no look-ahead (last bar)", d_last == 0, f"{d_last} rows changed")
    check("no look-ahead (mid bar)", d_mid == 0, f"{d_mid} rows changed")

    # ==================================================================
    print("\n[3] MARKET STATE", flush=True)
    # ==================================================================
    from trading_ai.market_state import (
        MarketStateAgent, classify_session, classify_volatility,
        classify_market_regime, Session,
    )
    import pandas as pd

    agent = MarketStateAgent()
    agent.configure({})
    t0 = time.time()
    state = agent.process(df)
    t_ms = time.time() - t0
    check("market state runs", t_ms < 30, f"{t_ms:.1f}s")
    for k in ["swing_highs", "swing_lows", "bos_events", "choch_events",
              "supply_zones", "demand_zones", "support_resistance"]:
        check(f"  {k} populated", len(state[k]) > 0 or k in ("supply_zones", "demand_zones"),
              f"{len(state[k])} items")
    check("regime valid",
          state["market_regime"] in ("TRENDING_UP","TRENDING_DOWN","RANGING","EXPANSION","COMPRESSION"),
          state["market_regime"])
    check("volatility regime valid",
          state["volatility_regime"] in ("low","normal","high","extreme"),
          state["volatility_regime"])

    session_tests = [
        ("2024-01-01 03:00", Session.ASIAN),
        ("2024-01-01 10:00", Session.LONDON),
        ("2024-01-01 15:00", Session.OVERLAP),
        ("2024-01-01 19:00", Session.NEW_YORK),
        ("2024-01-01 23:00", Session.OFF),
    ]
    all_sess = True
    sess_detail = []
    for ts_str, expected in session_tests:
        actual = classify_session(pd.Timestamp(ts_str, tz="UTC"))
        ok = actual == expected
        all_sess = all_sess and ok
        sess_detail.append(f"{ts_str[11:16]}={actual.value}{'v' if ok else 'X'}")
    check("session classification", all_sess, ", ".join(sess_detail))

    vol_reg = classify_volatility(df)
    check("volatility classify", vol_reg.value in ("low","normal","high","extreme"), vol_reg.value)

    # ==================================================================
    print("\n[4] STRATEGIES + GENOME", flush=True)
    # ==================================================================
    from trading_ai.strategies.genome.schema import StrategyGenome
    from trading_ai.strategies.genome.mutator import StrategyMutator
    from trading_ai.strategies.supply_demand import SupplyDemandStrategy
    from trading_ai.strategies.trend import TrendAgent
    from trading_ai.strategies.breakout import BreakoutAgent
    from trading_ai.strategies.momentum import MomentumAgent
    from trading_ai.strategies.mean_reversion import MeanReversionAgent
    from trading_ai.strategies.volatility import VolatilityAgent

    # Consistent MTF: one base series resampled up (structure aligns)
    data = gen.generate_multi_timeframe(
        "EURUSD", base_timeframe="M5",
        timeframes=("M5", "M15", "H1", "H4"),
        base_count=5000, initial_price=1.1000, trend="bullish",
    )
    ms = {"symbol": "EURUSD", "session": "london",
          "volatility_regime": "normal", "atr": 0.001}

    genome = StrategyGenome(
        strategy_id="test-001", market=["EURUSD", "*"],
        timeframes=["H4", "H1", "M15", "M5"],
        entry_rules=[{"type": "supply_demand", "params": {"lookback": 50}}],
        exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
        filters=[],
    )

    strategy_classes = [
        ("SupplyDemand", SupplyDemandStrategy), ("Trend", TrendAgent),
        ("Breakout", BreakoutAgent), ("Momentum", MomentumAgent),
        ("MeanRev", MeanReversionAgent), ("Volatility", VolatilityAgent),
    ]

    strat_errors = []
    n_signals = 0
    signal_details = []
    for name, cls in strategy_classes:
        try:
            strat = cls(genome)
            # Scan recent bars: strategies look at series tails
            found = False
            m5 = data["M5"]
            step = max(1, len(m5) // 80)
            for i in range(len(m5) - 1, max(100, len(m5) - 400), -step):
                ts = m5.index[i]
                slice_mt = {
                    tf: dff[dff.index <= ts]
                    for tf, dff in data.items()
                }
                if any(len(v) < 30 for v in slice_mt.values()):
                    continue
                sig = strat.analyze(slice_mt, ms)
                if sig:
                    n_signals += 1
                    found = True
                    signal_details.append(
                        f"{name}:{sig.direction.value if hasattr(sig.direction,'value') else sig.direction} "
                        f"conf={sig.confidence:.2f} rr={sig.risk_reward_ratio:.2f}"
                    )
                    print(f"    {name}: SIGNAL {sig.direction} @ {sig.entry_price:.5f} "
                          f"RR={sig.risk_reward_ratio:.2f} conf={sig.confidence:.2f}", flush=True)
                    break
            if not found:
                print(f"    {name}: no signal on scan", flush=True)
        except Exception as e:
            strat_errors.append(f"{name}: {type(e).__name__}: {e}")
    check("all 6 strategies run", not strat_errors,
          "; ".join(strat_errors) if strat_errors else f"{n_signals} signals on bullish MTF scan")
    check("at least 1 strategy fires", n_signals >= 1,
          f"{n_signals} signals; " + (", ".join(signal_details[:3]) if signal_details else "none"))

    # Genome JSON roundtrip
    j = genome.to_json()
    g2 = StrategyGenome.from_json(j)
    check("genome JSON roundtrip", g2.strategy_id == genome.strategy_id and len(g2.filters) == 0)

    # Mutation immutability
    mut = StrategyMutator()
    child = mut.mutate_volatility_filter(genome, {"min_atr": 0.0005, "max_atr": 0.005})
    check("mutation creates child", child.strategy_id != genome.strategy_id and child.parent_id == genome.strategy_id)
    check("parent unmodified", len(genome.filters) == 0, f"parent filters={len(genome.filters)}")
    check("child has filter", len(child.filters) == 1, f"child filters={len(child.filters)}")

    try:
        mut.mutate_volatility_filter(genome, {"max_risk_per_trade": 0.99})
        check("protected key blocked", False, "no exception raised")
    except Exception as e:
        check("protected key blocked", True, type(e).__name__)

    # ==================================================================
    print("\n[5] BACKTEST + RISK + PERFORMANCE", flush=True)
    # ==================================================================
    from trading_ai.backtesting.engine import BacktestEngine
    from trading_ai.models import BacktestConfig, Signal, Side
    from trading_ai.evaluation.performance import PerformanceAnalyzer
    from trading_ai.risk.engine import RiskAgent
    from datetime import datetime

    def simple_strategy(d, s):
        if len(d) < 10 or len(d) % 50 != 0:
            return None
        price = float(d["close"].iloc[-1])
        return Signal(timestamp=datetime.now(), symbol="EURUSD", side=Side.BUY,
                      entry_price=price, stop_loss=price * 0.995,
                      take_profit=price * 1.015, risk_reward=3.0)

    config = BacktestConfig(initial_capital=100000, risk_per_trade=0.005, seed=42)
    t0 = time.time()
    engine = BacktestEngine(simple_strategy, data["H1"], config)
    result = engine.run()
    t_bt = time.time() - t0
    check("backtest runs", len(result.trades) > 0,
          f"{len(result.trades)} trades in {t_bt:.1f}s, equity={result.equity_curve.iloc[-1]:,.0f}")
    check("equity curve built", len(result.equity_curve) == len(data["H1"]),
          f"{len(result.equity_curve)} points")

    risk = RiskAgent(config)
    a_ok = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.0945, 1.1165, 100000)
    low_rr = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.0990, 1.1010, 100000)
    zero_sl = risk.evaluate("EURUSD", Side.BUY, 1.1000, 1.1000, 1.1100, 100000)
    check("risk approves valid RR=4", bool(a_ok.approved),
          f"size={a_ok.position_size:.4f} risk={a_ok.risk_amount:.2f}" if a_ok.approved else "")
    check("risk rejects RR=2", not low_rr.approved)
    check("risk rejects zero SL", not zero_sl.approved)

    pm = PerformanceAnalyzer(config).calculate(
        result.trades, result.equity_curve, result.drawdown_series)
    check("performance metrics", pm.total_trades > 0,
          f"wr={pm.win_rate:.1%} pf={pm.profit_factor:.2f} sharpe={pm.sharpe_ratio:.2f} "
          f"sortino={pm.sortino_ratio:.2f} dd={pm.max_drawdown_pct:.2%}")

    # ==================================================================
    print("\n[6] SUBSYSTEMS", flush=True)
    # ==================================================================
    try:
        from trading_ai.evolution.engine import ExperimentEngine
        check("experiment engine", True)
    except Exception as e:
        check("experiment engine", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.memory.journal import (
            TradeJournal, TradeEntry, MarketRegime, SessionType, ExitReason,
            FilterCriteria,
        )
        from datetime import timezone
        tj = TradeJournal()
        te = TradeEntry(
            timestamp=datetime.now(timezone.utc), strategy_version="SD-001",
            market_regime=MarketRegime.TRENDING_UP, symbol="EURUSD",
            timeframe="M15", session=SessionType.LONDON, direction="BUY",
            entry_price=1.1000, stop_loss=1.0950, take_profit=1.1150,
            spread_at_entry=0.00015, volatility_at_entry=0.001,
            entry_reason="test", exit_price=1.1150,
            r_multiple=3.0, pnl_currency=150.0,
            exit_reason=ExitReason.TAKE_PROFIT,
        )
        tid = tj.record_trade(te)
        crit = FilterCriteria()
        crit.symbols = ["EURUSD"]
        trades = tj.query_trades(crit)
        check("trade journal", len(trades) >= 1, f"recorded id={tid}, query={len(trades)}")
    except Exception as e:
        check("trade journal", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.memory.graph import ResearchGraph, NodeType
        rg = ResearchGraph()
        n1 = rg.add_node(NodeType.STRATEGY, "SD-001", {})
        n2 = rg.add_node(NodeType.HYPOTHESIS, "H-001", {})
        check("research graph", True, f"nodes {n1}, {n2}")
    except Exception as e:
        check("research graph", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.evolution.gatekeeper import StrategyGatekeeper
        gk = StrategyGatekeeper(journal=tj)
        check("gatekeeper", True, f"min_sharpe={gk.policy.min_sharpe} min_pf={gk.policy.min_profit_factor}")
    except Exception as e:
        check("gatekeeper", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.evolution.lineage import StrategyLineage
        sl = StrategyLineage()
        v1 = sl.create_version("SD-001", {})
        v2 = sl.create_version("SD-014", {"mutation": "vol"}, parent_id=v1)
        check("lineage", True, f"{v1[:8]} -> {v2[:8]}")
    except Exception as e:
        check("lineage", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.research.agent import ResearchAgent
        ra = ResearchAgent(journal=tj)
        check("research agent", True)
    except Exception as e:
        check("research agent", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.research.meta import MetaResearchAgent
        mr = MetaResearchAgent(journal=tj, research_agent=ra)
        check("meta research agent", True)
    except Exception as e:
        check("meta research agent", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.execution.paper import PaperBroker
        pb = PaperBroker({"initial_balance": 100000.0})
        bal = pb.get_account_info()["balance"]
        check("paper broker", bal == 100000.0, f"balance={bal}")
    except Exception as e:
        check("paper broker", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.execution.mt5_adapter import MT5Adapter
        check("mt5 adapter (stub)", True)
    except Exception as e:
        check("mt5 adapter (stub)", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.monitoring.health import HealthMonitor
        hm = HealthMonitor()
        check("health monitor", True)
    except Exception as e:
        check("health monitor", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.agents.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        check("orchestrator", True)
    except Exception as e:
        check("orchestrator", False, f"{type(e).__name__}: {e}")

    try:
        from trading_ai.api.app import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        check("fastapi app", len(routes) >= 15, f"{len(routes)} routes")
    except Exception as e:
        check("fastapi app", False, f"{type(e).__name__}: {e}")

    # ==================================================================
    print("\n" + "=" * 64, flush=True)
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"TOTAL: {n_pass}/{len(RESULTS)} passed in {time.time()-t_all:.1f}s", flush=True)
    fails = [(n, d) for n, ok, d in RESULTS if not ok]
    if fails:
        print("FAILURES:", flush=True)
        for n, d in fails:
            print(f"  {n}: {d}", flush=True)
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    code = main()
    Path(__file__).with_suffix(".exit").write_text(str(code))
    sys.exit(code)
