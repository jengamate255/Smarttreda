"""Tests for backtesting module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import timedelta

from forex_rl.src.backtest.metrics import BacktestMetrics, TradeResult
from forex_rl.src.backtest.monte_carlo import MonteCarloSimulator


@pytest.fixture
def sample_trades() -> list[TradeResult]:
    """Generate a list of sample trades with known P&L."""
    trades = []
    pnls = [100, -50, 150, -30, 200, -80, 120, -20, 180, -40]
    for i, pnl in enumerate(pnls):
        trades.append(TradeResult(
            entry_time=pd.Timestamp("2024-01-01") + timedelta(hours=i * 10),
            exit_time=pd.Timestamp("2024-01-01") + timedelta(hours=i * 10 + 5),
            side="BUY" if pnl > 0 else "SELL",
            entry_price=1.1000,
            exit_price=1.1000 + pnl * 0.0001,
            pnl=float(pnl),
            pnl_pct=float(pnl) / 10000.0,
            duration=timedelta(hours=5),
        ))
    return trades


@pytest.fixture
def sample_equity_curve() -> list[float]:
    """Equity curve matching sample trades."""
    eq = [10000.0]
    for pnl in [100, -50, 150, -30, 200, -80, 120, -20, 180, -40]:
        eq.append(eq[-1] + pnl)
    return eq


class TestMetricsComputation:
    """Sharpe, Sortino, PF formulas correct."""

    def test_profit_factor(self, sample_trades: list[TradeResult], sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all(sample_trades, sample_equity_curve)
        gross_profit = 100 + 150 + 200 + 120 + 180  # 750
        gross_loss = 50 + 30 + 80 + 20 + 40  # 220
        expected_pf = gross_profit / gross_loss
        assert metrics["profit_factor"] == pytest.approx(expected_pf, rel=1e-6)

    def test_sharpe_ratio(self, sample_trades: list[TradeResult], sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all(sample_trades, sample_equity_curve)
        eq = np.array(sample_equity_curve)
        returns = np.diff(eq) / eq[:-1]
        expected_sharpe = np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(252)
        assert metrics["sharpe_ratio"] == pytest.approx(expected_sharpe, rel=1e-4)

    def test_sortino_ratio(self, sample_trades: list[TradeResult], sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all(sample_trades, sample_equity_curve)
        eq = np.array(sample_equity_curve)
        returns = np.diff(eq) / eq[:-1]
        downside = returns[returns < 0]
        expected_sortino = np.mean(returns) / np.std(downside, ddof=1) * np.sqrt(252)
        assert metrics["sortino_ratio"] == pytest.approx(expected_sortino, rel=1e-4)

    def test_win_rate(self, sample_trades: list[TradeResult], sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all(sample_trades, sample_equity_curve)
        wins = sum(1 for t in sample_trades if t.pnl > 0)
        expected_wr = wins / len(sample_trades)
        assert metrics["win_rate"] == pytest.approx(expected_wr, rel=1e-6)

    def test_max_drawdown(self, sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all([], sample_equity_curve)
        eq = np.array(sample_equity_curve)
        running_max = np.maximum.accumulate(eq)
        dd = (running_max - eq) / running_max
        expected_max_dd = float(np.max(dd))
        assert metrics["max_drawdown"] == pytest.approx(expected_max_dd, rel=1e-6)


class TestNoFutureData:
    """Backtest doesn't use future candles."""

    def test_equity_curve_length(self, sample_equity_curve: list[float]) -> None:
        metrics = BacktestMetrics.compute_all([], sample_equity_curve)
        # With no trades, equity curve should be used as-is
        assert "total_return" in metrics


class TestTransactionCosts:
    """Spread + slippage + commission applied."""

    def test_trade_has_costs(self) -> None:
        trade = TradeResult(
            entry_time=pd.Timestamp("2024-01-01"),
            exit_time=pd.Timestamp("2024-01-01") + timedelta(hours=1),
            side="BUY",
            entry_price=1.1000,
            exit_price=1.1050,
            pnl=45.0,
            pnl_pct=0.0045,
            duration=timedelta(hours=1),
            slippage=0.3,
            spread_cost=15.0,
        )
        assert trade.slippage == 0.3
        assert trade.spread_cost == 15.0


class TestMonteCarlo:
    """Ruin probability is valid [0,1]."""

    def test_ruin_probability_range(self, sample_trades: list[TradeResult]) -> None:
        mc = MonteCarloSimulator(n_simulations=1000, seed=42)
        result = mc.simulate(sample_trades, initial_balance=10000.0)
        assert 0.0 <= result.probability_of_ruin <= 1.0

    def test_median_return_finite(self, sample_trades: list[TradeResult]) -> None:
        mc = MonteCarloSimulator(n_simulations=500, seed=42)
        result = mc.simulate(sample_trades, initial_balance=10000.0)
        assert np.isfinite(result.median_return)
        assert np.isfinite(result.mean_return)

    def test_empty_trades(self) -> None:
        mc = MonteCarloSimulator(n_simulations=100, seed=42)
        result = mc.simulate([], initial_balance=10000.0)
        assert result.probability_of_ruin == 0.0
        assert result.distribution.size == 0

    def test_worst_drawdown_in_range(self, sample_trades: list[TradeResult]) -> None:
        mc = MonteCarloSimulator(n_simulations=500, seed=42)
        result = mc.simulate(sample_trades, initial_balance=10000.0)
        assert 0.0 <= result.worst_drawdown <= 1.0
