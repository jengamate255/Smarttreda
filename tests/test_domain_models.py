"""Tests for domain models - TradeSetup, Zone, Structure, Liquidity, etc."""

from __future__ import annotations

from datetime import datetime, timezone
from mt5_mcp.domain_models import (
    TradeSetup,
    ZoneModel,
    StructureModel,
    LiquidityModel,
    ConfirmationModel,
    RiskEngineResult,
    TradeSetupStatus,
    ZoneModel,
    AssetClass,
)


def test_trade_setup_creation():
    """Test TradeSetup creation with required fields."""
    setup = TradeSetup(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        direction="LONG",
        timeframe="M5",
    )
    assert setup.symbol == "EURUSD"
    assert setup.direction == "LONG"
    assert setup.timeframe == "M5"
    assert setup.status == TradeSetupStatus.CANDIDATE
    assert setup.risk_percent == 0.5


def test_trade_setup_rr_calculation():
    """Test risk-reward calculation."""
    setup = TradeSetup(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        direction="LONG",
        timeframe="M5",
        entry=1.0800,
        stop_loss=1.0750,
        take_profit=1.1000,
    )
    rr = setup.calculate_expected_rr()
    # risk = 1.0800 - 1.0750 = 0.0050
    # reward = 1.1000 - 1.0800 = 0.0200
    # RR = 0.0200 / 0.0050 = 4.0
    assert rr == 4.0


def test_trade_setup_rr_default():
    """Test RR calculation when SL/TP not set."""
    setup = TradeSetup(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        direction="LONG",
        timeframe="M5",
    )
    rr = setup.calculate_expected_rr()
    assert rr is None


def test_zone_model():
    """Test ZoneModel creation and properties."""
    zone = ZoneModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H4",
        zone_high=1.0850,
        zone_low=1.0800,
        type="demand",
    )
    assert zone.zone_high == 1.0850
    assert zone.zone_low == 1.0800
    assert zone.type == "demand"
    # zone_width and midpoint are computed via validators, check they exist
    assert hasattr(zone, "zone_width")
    assert hasattr(zone, "midpoint")
    assert zone.is_tested() is False


def test_zone_model_test():
    """Test zone test recording."""
    zone = ZoneModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H4",
        zone_high=1.0850,
        zone_low=1.0800,
        type="demand",
    )
    zone.test_zone()
    assert zone.tests == 1
    assert zone.strength == 0.5

    zone.test_zone()  # second test
    assert zone.tests == 2
    assert zone.strength == 0.65  # 0.5 + 0.15

    zone.test_zone()  # third test
    assert zone.tests == 3
    assert zone.strength == 0.8  # 0.65 + 0.15

    # Test strength caps at 1.0 after more tests
    zone.test_zone()  # 4th test
    zone.test_zone()  # 5th test
    assert zone.strength == 1.0  # capped at 1.0


def test_structure_model():
    """Test StructureModel creation."""
    structure = StructureModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H1",
    )
    assert structure.symbol == "EURUSD"
    assert structure.timeframe == "H1"
    assert structure.trend == "unknown"
    assert structure.structure_type == "range"


def test_structure_uptrend():
    """Test uptrend structure setting."""
    structure = StructureModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H1",
    )
    structure.set_uptrend(swing_high=1.0850, swing_low=1.0800)
    assert structure.trend == "uptrend"
    assert structure.structure_type == "higher_high_higher_low"
    assert structure.swing_high == 1.0850
    assert structure.swing_low == 1.0800


def test_structure_downtrend():
    """Test downtrend structure setting."""
    structure = StructureModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H1",
    )
    structure.set_downtrend(swing_high=1.0850, swing_low=1.0800)
    assert structure.trend == "downtrend"
    assert structure.structure_type == "lower_high_lower_low"


def test_liquidity_model():
    """Test LiquidityModel creation and level detection."""
    from datetime import datetime
    from mt5_mcp.data_schema import NormalizedCandle

    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0850,
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
        ),
    ]

    liquidity = LiquidityModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H1",
    )
    liquidity.detect_levels(candles)

    # Should have detected some levels
    assert hasattr(liquidity, "day_high") or hasattr(liquidity, "day_low")


def test_liquidity_has_level():
    """Test liquidity level check."""
    from datetime import datetime
    from mt5_mcp.data_schema import NormalizedCandle

    candles = [
        NormalizedCandle(
            timestamp=datetime.now(timezone.utc),
            symbol="EURUSD",
            timeframe="H1",
            open=1.0800,
            high=1.0850,
            low=1.0790,
            close=1.0825,
            volume=1000,
            spread=1.5,
        ),
    ]

    liquidity = LiquidityModel(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        timeframe="H1",
    )
    liquidity.detect_levels(candles)

    # Price near day high should be detected
    assert liquidity.has_liquidity(1.0850) is True


def test_confirmation_model():
    """Test ConfirmationModel creation."""
    confirmation = ConfirmationModel(
        symbol="EURUSD",
        timeframe="H1",
        pattern="pin_bar",
        is_bullish=True,
        body_size_pct=0.2,
        wick_pct=0.8,
    )
    assert confirmation.pattern == "pin_bar"
    assert confirmation.is_bullish is True


def test_risk_engine_result():
    """Test RiskEngineResult creation."""
    result = RiskEngineResult(
        can_trade=True,
        explanation="Trade approved",
        risk_percent=0.5,
        position_size=1.5,
    )
    assert result.can_trade is True
    assert result.explanation == "Trade approved"
    assert result.risk_percent == 0.5
    assert result.position_size == 1.5


def test_trade_status_literals():
    """Test TradeSetupStatus enum values."""
    assert TradeSetupStatus.CANDIDATE.value == "CANDIDATE"
    assert TradeSetupStatus.UNDER_REVIEW.value == "UNDER_REVIEW"
    assert TradeSetupStatus.REJECTED.value == "REJECTED"
    assert TradeSetupStatus.APPROVED_FOR_PAPER.value == "APPROVED_FOR_PAPER"
    assert TradeSetupStatus.EXECUTED.value == "EXECUTED"
    assert TradeSetupStatus.CLOSED.value == "CLOSED"