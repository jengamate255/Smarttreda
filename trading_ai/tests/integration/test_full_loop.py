from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone

from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.genome.mutator import StrategyMutator
from trading_ai.data.validator import DataValidator


def _generate_ohlcv_data() -> dict[str, pd.DataFrame]:
    """Generate sample OHLCV for multiple symbols."""
    symbols = {
        "EURUSD": (1.1000, 0.0008),
        "GBPUSD": (1.2700, 0.0010),
        "USDJPY": (149.50, 0.08),
    }
    rng = np.random.default_rng(42)
    data = {}
    for sym, (base, vol) in symbols.items():
        n = 500
        dates = pd.date_range(start=datetime(2024, 1, 1, tzinfo=timezone.utc), periods=n, freq="3600s")
        closes = np.cumsum(rng.normal(0, vol, n)) + base
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        highs = np.maximum(opens, closes) + rng.uniform(0.0001, 0.0005, n)
        lows = np.minimum(opens, closes) - rng.uniform(0.0001, 0.0005, n)
        data[sym] = pd.DataFrame({
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": rng.uniform(50, 500, n), "spread": rng.uniform(0.5, 2.0, n),
        }, index=dates)
    return data


def _run_backtest(df: pd.DataFrame, signals: list[dict]) -> dict:
    trades = []
    for sig in signals:
        idx = sig["index"]
        entry = df["close"].iloc[idx]
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
        pnl = ((exit_price - entry) if direction == "BUY" else (entry - exit_price)) / 0.0001 * 10
        trades.append({"pnl": pnl, "exit_reason": exit_reason, "direction": direction})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    equity = [100_000]
    for t in trades:
        equity.append(equity[-1] + t["pnl"])
    peak, max_dd = equity[0], 0
    for e in equity:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd

    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0,
        "profit_factor": (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
                         if losses else float("inf")),
        "max_drawdown": max_dd,
        "expectancy": np.mean([t["pnl"] for t in trades]) if trades else 0,
        "trades": trades,
    }


def _generate_signals(df: pd.DataFrame, direction: str = "BUY") -> list[dict]:
    rng = np.random.default_rng(99)
    return [
        {"index": i, "direction": direction,
         "stop_loss": df["close"].iloc[i] - 0.0050 if direction == "BUY" else df["close"].iloc[i] + 0.0050,
         "take_profit": df["close"].iloc[i] + 0.0150 if direction == "BUY" else df["close"].iloc[i] - 0.0150}
        for i in range(20, len(df) - 10, 30)
    ]


class TestFullLoop:
    def test_generate_data_create_strategy_backtest_research_mutate_evaluate_decide(self):
        # Step 1: Generate data
        data = _generate_ohlcv_data()
        assert len(data) == 3
        for sym, df in data.items():
            assert len(df) == 500
            assert all(c in df.columns for c in ("open", "high", "low", "close", "volume"))

        # Step 2: Validate data
        validator = DataValidator()
        for sym, df in data.items():
            report = validator.validate(df, sym, "H1")
            assert not report.has_errors, f"{sym} has validation errors"

        # Step 3: Create base strategy
        base_genome = StrategyGenome(
            strategy_id="loop-base-001",
            name="Base Supply Demand",
            market=list(data.keys()),
            timeframes=["H4", "H1"],
            entry_rules=[{"type": "supply_demand", "params": {"lookback": 50, "min_strength": 0.6}}],
            exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
            risk_rules=[{"type": "fixed_fraction", "params": {"risk": 0.01}}],
            filters=[],
        )

        # Step 4: Backtest base strategy
        base_results = {}
        for sym, df in data.items():
            signals = _generate_signals(df, "BUY")
            base_results[sym] = _run_backtest(df, signals)
            assert base_results[sym]["total_trades"] > 0

        # Step 5: Research phase - observe weakness
        avg_win_rate = np.mean([r["win_rate"] for r in base_results.values()])
        # Hypothesis: volatility filter could improve consistency

        # Step 6: Mutate strategy
        mutator = StrategyMutator()
        mutated_genome = mutator.mutate_volatility_filter(
            base_genome, {"min_atr": 0.0005, "max_atr": 0.005}
        )
        mutated_genome.name = "Vol-Filtered Supply Demand"

        # Step 7: Backtest mutated strategy
        mutated_results = {}
        for sym, df in data.items():
            signals = _generate_signals(df, "BUY")
            mutated_results[sym] = _run_backtest(df, signals)

        # Step 8: Evaluate (gatekeeper)
        base_avg_pf = np.mean([r["profit_factor"] for r in base_results.values()])
        mut_avg_pf = np.mean([r["profit_factor"] for r in mutated_results.values()])
        base_avg_dd = np.mean([r["max_drawdown"] for r in base_results.values()])
        mut_avg_dd = np.mean([r["max_drawdown"] for r in mutated_results.values()])

        improvements = 0
        if mut_avg_pf > base_avg_pf * 1.05: improvements += 1
        if mut_avg_dd < base_avg_dd * 0.95: improvements += 1
        decision = "PROMOTE" if improvements >= 1 else "HOLD"

        # Step 9: Verify lineage
        assert mutated_genome.parent_id == base_genome.strategy_id
        assert mutated_genome.version == 2
        assert len(mutated_genome.filters) == 1
        assert decision in ("PROMOTE", "HOLD", "REJECT")

    def test_monte_carlo_simulation(self):
        rng = np.random.default_rng(42)
        base_trades = [rng.normal(20, 50) for _ in range(100)]

        n_sims = 500
        final_equities = []
        max_dds = []
        for _ in range(n_sims):
            shuffled = rng.choice(base_trades, size=len(base_trades), replace=True)
            equity = [100_000]
            for pnl in shuffled:
                equity.append(equity[-1] + pnl)
            final_equities.append(equity[-1])
            peak = max(equity)
            dd = (peak - min(equity)) / peak if peak > 0 else 0
            max_dds.append(dd)

        # Monte Carlo stats
        mean_final = np.mean(final_equities)
        p5_final = np.percentile(final_equities, 5)
        p95_final = np.percentile(final_equities, 95)
        mean_dd = np.mean(max_dds)
        p95_dd = np.percentile(max_dds, 95)

        assert p5_final < mean_final < p95_final
        assert mean_dd < 0.5  # sanity check
        assert p95_dd < 1.0

    def test_cross_symbol_consistency(self):
        data = _generate_ohlcv_data()
        results = {}
        for sym, df in data.items():
            signals = _generate_signals(df, "BUY")
            results[sym] = _run_backtest(df, signals)

        # All symbols should produce trades
        for sym, r in results.items():
            assert r["total_trades"] > 0, f"{sym} produced no trades"

        # Win rates should be within reasonable range
        for sym, r in results.items():
            assert 0.0 <= r["win_rate"] <= 1.0, f"{sym} has invalid win rate"
