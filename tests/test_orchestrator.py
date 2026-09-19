"""Tests for Chief Trading Agent orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from mt5_mcp.orchestrator import (
    ChiefTradingAgent,
    OrchestratorInput,
    OrchestratorOutput,
    DataQualityReport,
    RiskEngineResult,
)
from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.asset import AssetClass


def _make_candle(**kwargs):
    """Helper to create a valid candle with defaults."""
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    defaults = dict(
        timestamp=base_time,
        symbol="EURUSD",
        timeframe="H1",
        open=1.0800,
        high=1.0850,
        low=1.0790,
        close=1.0825,
        volume=1000,
        spread=1.5,
    )
    defaults.update(kwargs)
    return NormalizedCandle(**defaults)


def test_orchestrator_init():
    """Test ChiefTradingAgent initialization."""
    agent = ChiefTradingAgent(risk_percent=0.5, min_rr=3.0)
    assert agent.risk_percent == 0.5
    assert agent.min_rr == 3.0
    assert agent.enable_red_team is True


def test_orchestrator_init_no_red_team():
    """Test ChiefTradingAgent with red team disabled."""
    agent = ChiefTradingAgent(risk_percent=0.5, min_rr=3.0, enable_red_team=False)
    assert agent.enable_red_team is False


def test_orchestrator_pipeline_quality_fail():
    """Test pipeline returns early when data quality fails."""
    agent = ChiefTradingAgent(risk_percent=0.5, min_rr=3.0)

    # Use future timestamp to trigger data quality failure
    future_time = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(timestamp=future_time),
    ]

    input_data = OrchestratorInput(
        task_id="test_1",
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        candles=candles,
    )

    output = agent.run_pipeline(input_data)
    # Should fail fast on data quality
    assert output.data_quality.is_valid is False
    assert output.risk_result.can_trade is False
    assert "quality" in output.risk_result.explanation.lower()


def test_orchestrator_pipeline_success():
    """Test pipeline with valid data produces setups."""
    agent = ChiefTradingAgent(risk_percent=0.5, min_rr=3.0)
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    candles = [
        _make_candle(timestamp=base_time),
        _make_candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0820, high=1.0870, low=1.0810, close=1.0855,
            volume=1100, spread=1.4,
        ),
    ]

    input_data = OrchestratorInput(
        task_id="test_2",
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        candles=candles,
    )

    output = agent.run_pipeline(input_data)
    # Data quality should pass
    assert output.data_quality.is_valid is True
    # May or may not have setups depending on structure analysis
    assert output.candidate_setups is not None
    # Risk result should be produced
    assert output.risk_result is not None


def test_orchestrator_with_custom_config():
    """Test orchestrator with custom risk parameters."""
    agent = ChiefTradingAgent(risk_percent=1.0, min_rr=2.0, max_positions=3)
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    candles = [
        _make_candle(timestamp=base_time),
        _make_candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0820, high=1.0870, low=1.0810, close=1.0855,
            volume=1100, spread=1.4,
        ),
    ]

    input_data = OrchestratorInput(
        task_id="test_3",
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        candles=candles,
    )

    output = agent.run_pipeline(input_data)
    # The orchestrator's risk_percent config should be 1.0
    assert agent.risk_percent == 1.0
    assert agent.risk_engine.risk_percent == 1.0


def test_orchestrator_output_models():
    """Test output models have correct fields."""
    agent = ChiefTradingAgent()

    candles = [
        _make_candle(),
    ]

    input_data = OrchestratorInput(
        task_id="test_4",
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        candles=candles,
    )

    output = agent.run_pipeline(input_data)
    assert isinstance(output, OrchestratorOutput)
    assert hasattr(output, "task_id")
    assert hasattr(output, "data_quality")
    assert hasattr(output, "risk_result")
    assert hasattr(output, "candidate_setups")