"""Tests for risk management module."""
from __future__ import annotations

import math

import pytest

from forex_rl.src.risk.position_sizing import (
    fixed_fractional,
    kelly_criterion,
    volatility_adjusted,
    atr_position_size,
)
from forex_rl.src.risk.risk_manager import RiskConfig, RiskManager


@pytest.fixture
def risk_manager() -> RiskManager:
    return RiskManager(RiskConfig())


class TestPositionSizing:
    """Correct lot calculation via fixed_fractional."""

    def test_basic_sizing(self) -> None:
        lots = fixed_fractional(
            balance=1000.0,
            risk_pct=0.005,
            sl_distance=0.005,
            tick_value=10.0,
        )
        # risk_amount = 1000 * 0.005 = 5
        # lots = 5 / (0.005 * 10.0) = 5 / 0.05 = 100
        assert lots == 100.0

    def test_sizing_within_cap(self) -> None:
        lots = fixed_fractional(
            balance=1000.0,
            risk_pct=0.005,
            sl_distance=0.010,
            tick_value=10.0,
        )
        # risk_amount = 1000 * 0.005 = 5
        # lots = 5 / (0.010 * 10.0) = 5 / 0.1 = 50
        assert lots == 50.0

    def test_zero_balance(self) -> None:
        assert fixed_fractional(0, 0.005, 0.005, 1.0) == 0.0

    def test_zero_sl_distance(self) -> None:
        assert fixed_fractional(10000, 0.005, 0.0, 1.0) == 0.0

    def test_small_account(self) -> None:
        lots = fixed_fractional(500.0, 0.01, 0.010, 10.0)
        expected = 500.0 * 0.01 / (0.010 * 10.0)
        assert lots == round(expected, 2)


class TestMaxDrawdownBlock:
    """Blocks trading when DD > limit."""

    def test_blocks_on_max_dd(self, risk_manager: RiskManager) -> None:
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.12,  # exceeds 10% limit
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -50,
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert not allowed
        assert "MAX_DD" in reason

    def test_allows_within_dd(self, risk_manager: RiskManager) -> None:
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.05,  # within 10%
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -50,
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert allowed


class TestDailyLossBlock:
    """Blocks trading when daily loss > limit."""

    def test_blocks_on_daily_dd(self, risk_manager: RiskManager) -> None:
        risk_manager.set_daily_start(10000)
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.02,
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -300,  # 3% daily loss
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert not allowed
        assert "DAILY_DD" in reason

    def test_allows_within_daily_dd(self, risk_manager: RiskManager) -> None:
        risk_manager.set_daily_start(10000)
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.02,
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -100,  # 1% daily loss
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert allowed


class TestMaxPositionsBlock:
    """Blocks when max positions reached."""

    def test_blocks_at_max(self, risk_manager: RiskManager) -> None:
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.02,
            "total_trades": 5,
            "open_positions": 3,  # at max
            "daily_pnl": -50,
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert not allowed
        assert "MAX_POSITIONS" in reason


class TestRrMinimum:
    """Rejects trades with R:R < minimum."""

    def test_rejects_low_rr(self, risk_manager: RiskManager) -> None:
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.02,
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -50,
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        # R:R = 50 pips TP / 200 pips SL = 0.25 < 3.0
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0800,
            "tp_price": 1.1050,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert not allowed
        assert "MIN_RR" in reason

    def test_accepts_good_rr(self, risk_manager: RiskManager) -> None:
        state = {
            "equity": 10000,
            "balance": 10000,
            "drawdown": 0.01,
            "max_drawdown": 0.02,
            "total_trades": 5,
            "open_positions": 1,
            "daily_pnl": -50,
            "open_risk": 30,
            "consecutive_losses": 0,
            "bars_since_last_trade": 10,
        }
        # R:R = 150 pips TP / 50 pips SL = 3.0 >= 3.0
        trade = {
            "symbol": "EURUSD",
            "side": "BUY",
            "entry_price": 1.1000,
            "sl_price": 1.0950,
            "tp_price": 1.1150,
            "lots": 0.1,
            "spread_pips": 1.2,
        }
        allowed, reason = risk_manager.can_trade(state, trade)
        assert allowed


class TestKellySizing:
    """Kelly criterion formula."""

    def test_basic_kelly(self) -> None:
        # p=0.6, b=2.0, q=0.4
        # f* = (0.6 * 2.0 - 0.4) / 2.0 = 0.8 / 2.0 = 0.4
        # half Kelly = 0.2, clamped to 0.25 → 0.2
        result = kelly_criterion(0.6, 200.0, 100.0, fraction=0.5)
        assert result == 0.2

    def test_unprofitable_edge(self) -> None:
        # p=0.4, b=1.0 → f* = (0.4 * 1.0 - 0.6) / 1.0 = -0.2 → clamped to 0
        result = kelly_criterion(0.4, 100.0, 100.0)
        assert result == 0.0

    def test_perfect_win_rate(self) -> None:
        # p=1.0 returns 0 (edge case)
        result = kelly_criterion(1.0, 200.0, 100.0)
        assert result == 0.0

    def test_zero_win_rate(self) -> None:
        result = kelly_criterion(0.0, 200.0, 100.0)
        assert result == 0.0


class TestVolatilityAdjusted:
    """Inverse-vol scaling."""

    def test_high_vol_reduces_size(self) -> None:
        base = volatility_adjusted(0.001, 0.001, 0.002, 1.0)
        assert base == 0.5  # 1.0 * (0.001/0.002)

    def test_low_vol_increases_size(self) -> None:
        base = volatility_adjusted(0.001, 0.001, 0.0005, 1.0)
        assert base == 2.0  # 1.0 * (0.001/0.0005)

    def test_equal_vol(self) -> None:
        base = volatility_adjusted(0.001, 0.001, 0.001, 1.0)
        assert base == 1.0

    def test_zero_inputs(self) -> None:
        assert volatility_adjusted(0.0, 0.001, 0.001, 1.0) == 0.0
        assert volatility_adjusted(0.001, 0.0, 0.001, 1.0) == 0.0
        assert volatility_adjusted(0.001, 0.001, 0.0, 1.0) == 0.0
