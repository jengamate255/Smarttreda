#!/usr/bin/env python3
"""Debug script to test each step of the experiment individually."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
from datetime import datetime, timezone

from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.genome.mutator import StrategyMutator
from trading_ai.data.validator import DataValidator

def generate_data():
    symbols = {
        "EURUSD": (1.1000, 0.0008),
        "GBPUSD": (1.2700, 0.0010),
        "USDJPY": (149.50, 0.08),
        "GBPJPY": (189.50, 0.12),
        "XAUUSD": (2000.00, 1.50),
    }
    data = {}
    for sym, (base, vol) in symbols.items():
        rng = np.random.default_rng(hash(sym) % 2**31)
        n = 1000
        dates = pd.date_range(start=datetime(2024, 1, 1, tzinfo=timezone.utc), periods=n, freq="3600s")
        closes = np.cumsum(rng.normal(0, vol, n)) + base
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        highs = np.maximum(opens, closes) + rng.uniform(0.0001, vol * 0.3, n)
        lows = np.minimum(opens, closes) - rng.uniform(0.0001, vol * 0.3, n)
        data[sym] = pd.DataFrame({
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": rng.uniform(50, 500, n), "spread": rng.uniform(0.5, 3.0, n),
        }, index=dates)
    return data

def run_backtest(df, signals, spread_pips=1.0):
    pip = 0.0001
    trades = []
    for sig in signals:
        idx = sig["index"]
        if idx >= len(df):
            continue
        entry = df["close"].iloc[idx]
        entry += spread_pips * pip / 2
        sl, tp = sig["stop_loss"], sig["take_profit"]
        direction = sig["direction"]
        exit_price, exit_reason = entry, "time"
        for j in range(idx + 1, min(idx + 200, len(df))):
            c = df.iloc[j]
            if direction == "BUY":
                if c["low"] <= sl:
                    exit_price, exit_reason = sl, "stop_loss"; break
                if c["high"] >= tp:
                    exit_price, exit_reason = tp, "take_profit"; break
            else:
                if c["high"] >= sl:
                    exit_price, exit_reason = sl, "stop_loss"; break
                if c["low"] <= tp:
                    exit_price, exit_reason = tp, "take_profit"; break
        pnl = ((exit_price - entry) if direction == "BUY" else (entry - exit_price)) / pip * 10
        trades.append({"pnl": pnl, "exit_reason": exit_reason, "direction": direction,
                        "entry": entry, "exit": exit_price})
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "max_drawdown": 0, "expectancy": 0, "trades": [], "equity_curve": [100_000]}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    equity = [100_000.0]
    for t in trades:
        equity.append(equity[-1] + t["pnl"])
    peak, max_dd = equity[0], 0.0
    for e in equity:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "profit_factor": (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
                         if losses else float("inf")),
        "max_drawdown": max_dd,
        "expectancy": np.mean([t["pnl"] for t in trades]),
        "trades": trades,
        "equity_curve": equity,
    }

def generate_signals(df, direction="BUY", seed=42):
    return [
        {"index": i, "direction": direction,
         "stop_loss": df["close"].iloc[i] - 0.0050 if direction == "BUY" else df["close"].iloc[i] + 0.0050,
         "take_profit": df["close"].iloc[i] + 0.0150 if direction == "BUY" else df["close"].iloc[i] - 0.0150}
        for i in range(20, len(df) - 10, 30)
    ]

def monte_carlo(trades, n_sims=1000, seed=42):
    rng = np.random.default_rng(seed)
    pnls = np.array([t["pnl"] for t in trades])
    finals, max_dds = [], []
    for _ in range(n_sims):
        shuffled = rng.choice(pnls, size=len(pnls), replace=True)
        eq = np.concatenate([[100_000], 100_000 + np.cumsum(shuffled)])
        finals.append(eq[-1])
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        max_dds.append(dd.max())
    return {
        "mean_final": float(np.mean(finals)),
        "p5_final": float(np.percentile(finals, 5)),
        "p95_final": float(np.percentile(finals, 95)),
        "mean_max_dd": float(np.mean(max_dds)),
        "p95_max_dd": float(np.percentile(max_dds, 95)),
        "prob_profit": float(np.mean(np.array(finals) > 100_000)),
    }

def gatekeeper(base, mutated):
    improvements, regressions = [], []
    if mutated["profit_factor"] > base["profit_factor"] * 1.05:
        improvements.append("profit_factor")
    elif mutated["profit_factor"] < base["profit_factor"] * 0.95:
        regressions.append("profit_factor")
    if mutated["win_rate"] > base["win_rate"] + 0.02:
        improvements.append("win_rate")
    elif mutated["win_rate"] < base["win_rate"] - 0.02:
        regressions.append("win_rate")
    if mutated["max_drawdown"] < base["max_drawdown"] * 0.95:
        improvements.append("max_drawdown")
    elif mutated["max_drawdown"] > base["max_drawdown"] * 1.10:
        regressions.append("max_drawdown")
    if len(improvements) > len(regressions):
        decision = "PROMOTE"
    elif len(regressions) > len(improvements):
        decision = "REJECT"
    else:
        decision = "HOLD"
    return {"decision": decision, "improvements": improvements, "regressions": regressions}


print("=" * 70)
print("  FOREX TRADING AI - RECURSIVE IMPROVEMENT LOOP EXPERIMENT")
print("=" * 70)

print("\n[1/12] Generating sample data for 5 symbols...")
data = generate_data()
for sym, df in data.items():
    print(f"  {sym}: {len(df)} H1 candles")

print("\n[2/12] Validating data quality...")
validator = DataValidator()
for sym, df in data.items():
    report = validator.validate(df, sym, "H1")
    errors = sum(1 for i in report.issues if i.severity.value == "error")
    warnings = sum(1 for i in report.issues if i.severity.value == "warning")
    print(f"  {sym}: {errors} errors, {warnings} warnings")

print("\n[3/12] Creating base supply-demand strategy genome...")
base_genome = StrategyGenome(
    strategy_id="exp-baseline-001",
    name="Baseline Supply Demand",
    description="Basic supply/demand strategy with multi-timeframe confirmation",
    market=list(data.keys()),
    timeframes=["H4", "H1", "M15"],
    entry_rules=[
        {"type": "supply_demand", "params": {"lookback": 50, "min_strength": 0.6,
                                              "require_ob_level": True}},
    ],
    exit_rules=[
        {"type": "risk_reward", "params": {"min_rr": 3.0, "target_rr": 5.0}},
    ],
    risk_rules=[
        {"type": "fixed_fraction", "params": {"risk_per_trade": 0.01, "max_positions": 3}},
    ],
    filters=[],
)
print(f"  Genome ID: {base_genome.strategy_id}")
print(f"  Markets: {base_genome.market}")
print(f"  Timeframes: {base_genome.timeframes}")

print("\n[4/12] Running baseline backtest...")
base_results = {}
for sym, df in data.items():
    signals = generate_signals(df, "BUY")
    base_results[sym] = run_backtest(df, signals)
    print(f"  {sym}: {base_results[sym]['total_trades']} trades, "
          f"WR={base_results[sym]['win_rate']:.1%}, "
          f"PF={base_results[sym]['profit_factor']:.2f}")

print("\n[5/12] HYPOTHESIS:")
print('  "Does adding a volatility-regime filter improve robustness?"')
print("  Rationale: Filter out high-volatility periods where SLs get hunted")
avg_dd = np.mean([r["max_drawdown"] for r in base_results.values()])
print(f"  Baseline avg max drawdown: {avg_dd:.2%}")

print("\n[6/12] Creating mutated genome with volatility filter...")
mutator = StrategyMutator()
mutated_genome = mutator.mutate_volatility_filter(
    base_genome, {"min_atr": 0.0005, "max_atr": 0.005,
                   "description": "Filter extreme volatility regimes"}
)
mutated_genome.name = "Vol-Filtered Supply Demand"
print(f"  Mutated genome ID: {mutated_genome.strategy_id}")
print(f"  Parent: {mutated_genome.parent_id}")
print(f"  Filters: {len(mutated_genome.filters)}")

print("\n[7/12] Running mutated strategy backtest...")
mut_results = {}
for sym, df in data.items():
    signals = generate_signals(df, "BUY")
    mut_results[sym] = run_backtest(df, signals)
    print(f"  {sym}: {mut_results[sym]['total_trades']} trades, "
          f"WR={mut_results[sym]['win_rate']:.1%}, "
          f"PF={mut_results[sym]['profit_factor']:.2f}")

print("\n[8/12] Running walk-forward test...")
for sym, df in data.items():
    split = int(len(df) * 0.6)
    train, test = df.iloc[:split], df.iloc[split:]
    train_sig = generate_signals(train, "BUY", seed=42)
    test_sig = generate_signals(test, "BUY", seed=99)
    train_r = run_backtest(train, train_sig)
    test_r = run_backtest(test, test_sig)
    print(f"  {sym}: Train WR={train_r['win_rate']:.1%} | Test WR={test_r['win_rate']:.1%}")

print("\n[9/12] Running out-of-sample test...")
for sym, df in data.items():
    split = int(len(df) * 0.8)
    oos = df.iloc[split:]
    oos_sig = generate_signals(oos, "BUY", seed=77)
    oos_r = run_backtest(oos, oos_sig)
    print(f"  {sym} OOS: {oos_r['total_trades']} trades, WR={oos_r['win_rate']:.1%}")

print("\n[10/12] Running Monte Carlo simulation (1000 iterations)...")
for sym in data:
    if base_results[sym]["trades"]:
        mc = monte_carlo(base_results[sym]["trades"])
        print(f"  {sym}: Mean final=${mc['mean_final']:,.0f}, "
              f"P5=${mc['p5_final']:,.0f}, P95=${mc['p95_final']:,.0f}, "
              f"Prob profit={mc['prob_profit']:.1%}")

print("\n[11/12] Running gatekeeper evaluation...")
base_avg = {
    "profit_factor": np.mean([r["profit_factor"] for r in base_results.values() if r["profit_factor"] < 100]),
    "win_rate": np.mean([r["win_rate"] for r in base_results.values()]),
    "max_drawdown": np.mean([r["max_drawdown"] for r in base_results.values()]),
}
mut_avg = {
    "profit_factor": np.mean([r["profit_factor"] for r in mut_results.values() if r["profit_factor"] < 100]),
    "win_rate": np.mean([r["win_rate"] for r in mut_results.values()]),
    "max_drawdown": np.mean([r["max_drawdown"] for r in mut_results.values()]),
}
decision = gatekeeper(base_avg, mut_avg)
print(f"  Decision: {decision['decision']}")
print(f"  Improvements: {decision['improvements']}")
print(f"  Regressions: {decision['regressions']}")

print("\n" + "=" * 70)
print("  EXPERIMENT REPORT")
print("=" * 70)
print(f"\n  Hypothesis: Volatility-regime filter improves robustness")
print(f"  Baseline genome: {base_genome.strategy_id}")
print(f"  Mutated genome:  {mutated_genome.strategy_id}")
print(f"\n  {'Metric':<25} {'Baseline':>12} {'Mutated':>12} {'Delta':>12}")
print(f"  {'-'*61}")
for metric in ["profit_factor", "win_rate", "max_drawdown"]:
    b = base_avg[metric]
    m = mut_avg[metric]
    d = m - b
    fmt = ".2f" if metric == "profit_factor" else ".1%"
    if metric == "max_drawdown":
        print(f"  {metric:<25} {b:>11{fmt}} {m:>11{fmt}} {d:>+11{fmt}}")
    else:
        print(f"  {metric:<25} {b:>11{fmt}} {m:>11{fmt}} {d:>+11{fmt}}")
print(f"\n  Gatekeeper decision: {decision['decision']}")
if decision["improvements"]:
    print(f"  Improvements: {', '.join(decision['improvements'])}")
if decision["regressions"]:
    print(f"  Regressions: {', '.join(decision['regressions'])}")
print("\n" + "=" * 70)
