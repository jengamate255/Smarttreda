"""Tests for Risk Engine."""

from __future__ import annotations

from mt5_mcp.domain_models import TradeSetup
from mt5_mcp.risk_engine import RiskEngine, RiskEngineResult


def test_risk_engine_initialization():
    """Test RiskEngine default initialization."""
    engine = RiskEngine()
    assert engine.risk_percent == 0.5
    assert engine.max_daily_loss_percent == 3.0
    assert engine.max_drawdown_percent == 8.0
    assert engine.max_positions == 5
    assert engine.min_risk_reward == 3.0


def test_risk_engine_custom_init():
    """Test RiskEngine with custom parameters."""
    engine = RiskEngine(
        risk_percent=1.0,
        max_daily_loss_percent=2.0,
        max_positions=3,
        min_risk_reward=2.5,
    )
    assert engine.risk_percent == 1.0
    assert engine.max_daily_loss_percent == 2.0
    assert engine.max_positions == 3
    assert engine.min_risk_reward == 2.5


def test_can_trade_basic():
    """Test basic can_trade determination."""
    engine = RiskEngine(risk_percent=0.5, min_risk_reward=3.0)

    setup = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,
        take_profit=1.1000,
        risk_percent=0.5,
    )

    result = engine.can_trade(setup, equity=10000.0)
    assert result.can_trade is True
    assert "approved" in result.explanation.lower()


def test_can_trade_min_rr_not_met():
    """Test can_trade when RR is below minimum."""
    engine = RiskEngine(min_risk_reward=3.0)

    setup = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0790,  # very tight SL
        take_profit=1.0810,  # tiny TP
        risk_percent=0.5,
    )

    result = engine.can_trade(setup, equity=10000.0)
    # RR = (1.0810 - 1.0800) / (1.0800 - 1.0790) = 0.001 / 0.001 = 1.0
    # Which is below minimum 3.0
    assert result.can_trade is False
    assert "risk-reward" in result.explanation.lower()


def test_can_trade_max_positions():
    """Test can_trade when max positions reached."""
    engine = RiskEngine(max_positions=2)

    setup1 = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,
        take_profit=1.1000,
        risk_percent=0.5,
    )

    setup2 = TradeSetup(
        symbol="GBPUSD",
        asset_class="forex",
        direction="SHORT",
        timeframe="M5",
        entry=1.2000,
        stop_loss=1.2050,
        take_profit=1.1800,
        risk_percent=0.5,
    )

    # First trade approved
    result1 = engine.can_trade(setup1, equity=10000.0)
    assert result1.can_trade is True

    # Second trade - should check positions
    result2 = engine.can_trade(setup2, equity=10000.0, open_positions=2)
    # With 2 open positions and trying to open 3rd
    assert result2.can_trade is False
    assert "max positions" in result2.explanation.lower()


def test_can_trade_daily_loss():
    """Test can_trade with daily loss limit."""
    engine = RiskEngine(max_daily_loss_percent=5.0)

    # Simulate having lost the daily limit
    engine.daily_loss = 100.0  # $100 lost

    setup = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,
        take_profit=1.1000,
        risk_percent=0.5,
    )

    result = engine.can_trade(setup, equity=1000.0)
    # daily loss limit = 1000 * 5% = $50
    # daily_loss = 100 > 50, should be blocked
    assert result.can_trade is False
    assert "daily loss" in result.explanation.lower()


def test_position_size_calculation():
    """Test position size is calculated correctly."""
    engine = RiskEngine(risk_percent=1.0)  # 1% risk

    setup = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,  # 50 pips risk
        take_profit=1.1100,  # 150 pips reward
        risk_percent=1.0,
    )

    result = engine.can_trade(setup, equity=10000.0)
    # risk_amount = 10000 * 1% = $100
    # stop_distance = |1.0800 - 1.0750| = 0.0050
    # position_size = 100 / 0.0050 = 20000 units
    assert result.can_trade is True
    assert result.position_size > 0
    assert result.position_size == 20000.0


def test_risk_engine_state_tracking():
    """Test risk engine records trade results."""
    engine = RiskEngine(risk_percent=1.0)

    setup = TradeSetup(
        symbol="EURUSD",
        asset_class="forex",
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,
        take_profit=1.1100,
        risk_percent=1.0,
    )

    # Record winning trade
    engine.record_trade_result(profit_loss=100.0, setup=setup)
    # Daily loss should decrease (or stay at 0)
    assert engine.daily_loss >= 0

    # Record losing trade
    engine.record_trade_result(profit_loss=-50.0, setup=setup)
    # Daily loss should increase
    assert engine.daily_loss == 50.0  # 0 + 50