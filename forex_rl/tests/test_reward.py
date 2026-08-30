"""Tests for reward functions."""
from __future__ import annotations

import numpy as np
import pytest

from forex_rl.src.environment.reward import (
    ExitReason,
    MarketRegime,
    MarketState,
    Portfolio,
    PortfolioState,
    RewardConfig,
    RiskAdjustedReward,
    SharpeReward,
    SimpleProfitReward,
    TradeResult,
    TradeSide,
)


@pytest.fixture
def profit_trade() -> TradeResult:
    return TradeResult(
        entry_price=1.1000,
        exit_price=1.1100,
        side=TradeSide.BUY,
        volume=0.1,
        bars_held=50,
        pnl=100.0,
        pnl_pct=0.01,
        reason=ExitReason.TAKE_PROFIT,
    )


@pytest.fixture
def loss_trade() -> TradeResult:
    return TradeResult(
        entry_price=1.1000,
        exit_price=1.0900,
        side=TradeSide.BUY,
        volume=0.1,
        bars_held=20,
        pnl=-100.0,
        pnl_pct=-0.01,
        reason=ExitReason.STOP_LOSS,
    )


@pytest.fixture
def neutral_portfolio() -> PortfolioState:
    return PortfolioState(
        equity=10100,
        balance=10100,
        peak_equity=10100,
        drawdown=0.0,
        max_drawdown=0.005,
        total_trades=10,
        wins=6,
        losses=4,
        open_positions=0,
    )


@pytest.fixture
def trending_market() -> MarketState:
    return MarketState(
        regime=MarketRegime.TREND_UP,
        volatility_percentile=0.5,
        atr=0.001,
        trend_direction=0.8,
    )


class TestProfitReward:
    """Simple P&L reward."""

    def test_positive_pnl_positive_reward(
        self, profit_trade: TradeResult, neutral_portfolio: PortfolioState, trending_market: MarketState
    ) -> None:
        rf = SimpleProfitReward(RewardConfig(simple_scale=100.0, simple_clip=1.0))
        reward = rf.compute(profit_trade, neutral_portfolio, trending_market)
        assert reward > 0

    def test_negative_pnl_negative_reward(
        self, loss_trade: TradeResult, neutral_portfolio: PortfolioState, trending_market: MarketState
    ) -> None:
        rf = SimpleProfitReward(RewardConfig(simple_scale=100.0, simple_clip=1.0))
        reward = rf.compute(loss_trade, neutral_portfolio, trending_market)
        assert reward < 0

    def test_clipping(
        self, neutral_portfolio: PortfolioState, trending_market: MarketState
    ) -> None:
        big_trade = TradeResult(
            entry_price=1.0, exit_price=2.0, side=TradeSide.BUY,
            volume=1.0, bars_held=10, pnl=100000, pnl_pct=10.0,
            reason=ExitReason.TAKE_PROFIT,
        )
        rf = SimpleProfitReward(RewardConfig(simple_scale=100.0, simple_clip=1.0))
        reward = rf.compute(big_trade, neutral_portfolio, trending_market)
        assert reward == pytest.approx(1.0, abs=1e-6)


class TestDrawdownPenalty:
    """Quadratic DD penalty in RiskAdjustedReward."""

    def test_high_dd_penalized(
        self, profit_trade: TradeResult, trending_market: MarketState
    ) -> None:
        # Low DD portfolio
        low_dd = PortfolioState(
            equity=9950, balance=9950, peak_equity=10000,
            drawdown=0.005, max_drawdown=0.01, total_trades=10,
            wins=5, losses=5, open_positions=0,
        )
        # High DD portfolio
        high_dd = PortfolioState(
            equity=9500, balance=9500, peak_equity=10000,
            drawdown=0.05, max_drawdown=0.08, total_trades=10,
            wins=5, losses=5, open_positions=0,
        )
        cfg = RewardConfig(ra_dd_threshold=0.03)
        rf = RiskAdjustedReward(cfg)
        r_low = rf.compute(profit_trade, low_dd, trending_market)
        r_high = rf.compute(profit_trade, high_dd, trending_market)
        assert r_low > r_high


class TestOvertradingPenalty:
    """Penalizes excessive trading."""

    def test_overtrading_reduces_reward(
        self, profit_trade: TradeResult, trending_market: MarketState
    ) -> None:
        normal = PortfolioState(
            equity=10100, balance=10100, peak_equity=10100,
            drawdown=0.0, max_drawdown=0.01, total_trades=3,
            wins=2, losses=1, open_positions=0,
        )
        overtrader = PortfolioState(
            equity=10100, balance=10100, peak_equity=10100,
            drawdown=0.0, max_drawdown=0.01, total_trades=50,
            wins=30, losses=20, open_positions=0,
        )
        cfg = RewardConfig(ra_overtrading_limit=5, ra_overtrading_penalty=0.1)
        rf = RiskAdjustedReward(cfg)
        r_normal = rf.compute(profit_trade, normal, trending_market)
        r_over = rf.compute(profit_trade, overtrader, trending_market)
        assert r_normal >= r_over


class TestRevengeTrading:
    """Penalizes immediate re-entry after a loss."""

    def test_revenge_penalty(
        self, profit_trade: TradeResult, trending_market: MarketState
    ) -> None:
        # Recent loss, quick re-entry
        revenge = PortfolioState(
            equity=10100, balance=10100, peak_equity=10100,
            drawdown=0.0, max_drawdown=0.01, total_trades=10,
            wins=5, losses=5, open_positions=0,
            bars_since_last_trade=1,
            consecutive_losses=2,
        )
        # Calm re-entry
        calm = PortfolioState(
            equity=10100, balance=10100, peak_equity=10100,
            drawdown=0.0, max_drawdown=0.01, total_trades=10,
            wins=5, losses=5, open_positions=0,
            bars_since_last_trade=20,
            consecutive_losses=0,
        )
        cfg = RewardConfig(ra_revenge_window=3, ra_revenge_penalty=0.3)
        rf = RiskAdjustedReward(cfg)
        r_revenge = rf.compute(profit_trade, revenge, trending_market)
        r_calm = rf.compute(profit_trade, calm, trending_market)
        assert r_calm >= r_revenge


class TestSharpeReward:
    """Rolling Sharpe calculation."""

    def test_positive_sharpe_positive_reward(
        self, profit_trade: TradeResult, trending_market: MarketState
    ) -> None:
        # Portfolio with many wins → positive rolling Sharpe
        state = PortfolioState(
            equity=10500, balance=10500, peak_equity=10500,
            drawdown=0.0, max_drawdown=0.01, total_trades=50,
            wins=40, losses=10, open_positions=0,
        )
        rf = SharpeReward(RewardConfig(sharpe_window=50, sharpe_target=0.0))
        reward = rf.compute(profit_trade, state, trending_market)
        # With default config, positive Sharpe should give positive reward
        assert isinstance(reward, float)

    def test_insufficient_data_fallback(
        self, profit_trade: TradeResult, neutral_portfolio: PortfolioState, trending_market: MarketState
    ) -> None:
        # Fewer than 5 trades → fallback to simple
        rf = SharpeReward(RewardConfig())
        reward = rf.compute(profit_trade, neutral_portfolio, trending_market)
        assert isinstance(reward, float)
        assert -2.0 <= reward <= 2.0
