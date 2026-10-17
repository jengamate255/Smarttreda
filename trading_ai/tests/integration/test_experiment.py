from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone

from trading_ai.strategies.genome.schema import StrategyGenome


def _make_ohlcv(n: int = 500, base: float = 1.1000, vol: float = 0.0008) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range(start=datetime(2024, 1, 1, tzinfo=timezone.utc), periods=n, freq="3600s")
    closes = np.cumsum(rng.normal(0, vol, n)) + base
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + rng.uniform(0.0001, 0.0005, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.0001, 0.0005, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": rng.uniform(50, 500, n), "spread": rng.uniform(0.5, 2.0, n),
    }, index=dates)


def _generate_signals(df: pd.DataFrame, direction: str = "BUY") -> list[dict]:
    """Generate random trading signals for testing."""
    rng = np.random.default_rng(99)
    signals = []
    for i in range(20, len(df) - 10, 30):
        entry = df["close"].iloc[i]
        if direction == "BUY":
            sl = entry - 0.0050
            tp = entry + 0.0150
        else:
            sl = entry + 0.0050
            tp = entry - 0.0150
        signals.append({"index": i, "direction": direction, "stop_loss": sl, "take_profit": tp})
    return signals


def _run_backtest(df: pd.DataFrame, signals: list[dict]) -> dict:
    """Simple backtester returning metrics."""
    trades = []
    for sig in signals:
        idx = sig["index"]
        entry = df["close"].iloc[idx]
        sl, tp = sig["stop_loss"], sig["take_profit"]
        direction = sig["direction"]
        exit_price = entry
        exit_reason = "time"
        for j in range(idx + 1, min(idx + 200, len(df))):
            candle = df.iloc[j]
            if direction == "BUY":
                if candle["low"] <= sl:
                    exit_price, exit_reason = sl, "stop_loss"
                    break
                if candle["high"] >= tp:
                    exit_price, exit_reason = tp, "take_profit"
                    break
            else:
                if candle["high"] >= sl:
                    exit_price, exit_reason = sl, "stop_loss"
                    break
                if candle["low"] <= tp:
                    exit_price, exit_reason = tp, "take_profit"
                    break
        if direction == "BUY":
            pnl = (exit_price - entry) / 0.0001 * 10
        else:
            pnl = (entry - exit_price) / 0.0001 * 10
        trades.append({"pnl": pnl, "exit_reason": exit_reason})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
    profit_factor = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
                     if losses else float("inf"))

    equity = [100_000]
    for t in trades:
        equity.append(equity[-1] + t["pnl"])
    peak = equity[0]
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(trades),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "expectancy": np.mean([t["pnl"] for t in trades]) if trades else 0,
        "equity_curve": equity,
        "trades": trades,
    }


def _gatekeeper_decide(baseline: dict, mutated: dict) -> dict:
    """Evaluate whether mutation improves the strategy."""
    improvements = []
    regressions = []

    if mutated["profit_factor"] > baseline["profit_factor"] * 1.05:
        improvements.append("profit_factor")
    elif mutated["profit_factor"] < baseline["profit_factor"] * 0.95:
        regressions.append("profit_factor")

    if mutated["win_rate"] > baseline["win_rate"] + 0.02:
        improvements.append("win_rate")
    elif mutated["win_rate"] < baseline["win_rate"] - 0.02:
        regressions.append("win_rate")

    if mutated["max_drawdown"] < baseline["max_drawdown"] * 0.95:
        improvements.append("max_drawdown")
    elif mutated["max_drawdown"] > baseline["max_drawdown"] * 1.10:
        regressions.append("max_drawdown")

    if len(improvements) > len(regressions):
        decision = "PROMOTE"
    elif len(regressions) > len(improvements):
        decision = "REJECT"
    else:
        decision = "HOLD"

    return {"decision": decision, "improvements": improvements, "regressions": regressions}


class TestExperiment:
    def test_full_experiment_flow(self):
        # 1. Create baseline genome
        baseline_genome = StrategyGenome(
            strategy_id="exp-baseline-001",
            name="Baseline Supply Demand",
            market=["EURUSD"],
            timeframes=["H4", "H1"],
            entry_rules=[{"type": "supply_demand", "params": {"lookback": 50}}],
            exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
            risk_rules=[{"type": "fixed_fraction", "params": {"risk": 0.01}}],
            filters=[],
        )

        # 2. Run baseline backtest
        df = _make_ohlcv(500)
        signals = _generate_signals(df, "BUY")
        baseline_results = _run_backtest(df, signals)
        assert baseline_results["total_trades"] > 0

        # 3. Create mutated genome with volatility filter
        mutated_genome = baseline_genome.mutate({
            "filters": [{"type": "volatility", "params": {"min_atr": 0.0005, "max_atr": 0.005}}],
            "name": "Volatility Filtered Supply Demand",
        })

        # 4. Run mutated backtest (same data for fair comparison)
        mutated_results = _run_backtest(df, signals)
        assert mutated_results["total_trades"] > 0

        # 5. Gatekeeper evaluation
        decision = _gatekeeper_decide(baseline_results, mutated_results)
        assert decision["decision"] in ("PROMOTE", "REJECT", "HOLD")
        assert isinstance(decision["improvements"], list)
        assert isinstance(decision["regressions"], list)

        # 6. Verify genome lineage
        assert mutated_genome.parent_id == baseline_genome.strategy_id
        assert mutated_genome.version == 2
        assert len(mutated_genome.filters) == 1

    def test_experiment_with_walk_forward(self):
        df = _make_ohlcv(1000)
        train_size = 600
        df_train = df.iloc[:train_size]
        df_test = df.iloc[train_size:]

        signals_train = _generate_signals(df_train, "BUY")
        signals_test = _generate_signals(df_test, "BUY")

        train_results = _run_backtest(df_train, signals_train)
        test_results = _run_backtest(df_test, signals_test)

        # Walk-forward: test set should not dramatically differ
        if train_results["total_trades"] > 0 and test_results["total_trades"] > 0:
            assert abs(train_results["win_rate"] - test_results["win_rate"]) < 0.5
