"""Tests for drawdown recovery mode.

Verifies:
- Drawdown > 3% triggers risk reduction from 1.0% to 0.5%
- Drawdown < 1% restores risk to 1.0%
- Hysteresis: once reduced, stays reduced until recovery threshold
- State persistence across calls
- Disabled mode returns max_risk_percent unchanged
- Alert and journal logging on mode transitions
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt5_mcp.drawdown_recovery import (
    drawdown_recovery_risk_percent,
    get_drawdown_recovery_status,
    reset_drawdown_recovery,
    _load_state,
    _save_state,
    _default_state,
    DEFAULT_MAX_RISK_PERCENT,
    DEFAULT_REDUCED_RISK_PERCENT,
    DEFAULT_REDUCE_THRESHOLD,
    DEFAULT_RESTORE_THRESHOLD,
)


@pytest.fixture(autouse=True)
def _mock_heavy_imports():
    """Mock heavy imports that would trigger MT5 connections or journal init."""
    mock_notify = MagicMock()
    mock_journal = MagicMock()
    with patch("mt5_mcp.drawdown_recovery._alert_risk_change") as mock_alert, \
         patch("mt5_mcp.drawdown_recovery._log_risk_change") as mock_log:
        yield {"notify": mock_alert, "journal": mock_log}


@pytest.fixture
def tmp_state(tmp_path):
    """Provide a temporary state file path."""
    return tmp_path / "dd_recovery_test.json"


class TestDrawdownRecoveryBasic:
    """Basic drawdown threshold tests."""

    def test_normal_mode_below_threshold(self, tmp_state):
        """Equity above threshold should stay in normal mode."""
        result = drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            reduce_threshold=3.0,
            restore_threshold=1.0,
            state_path=tmp_state,
        )
        assert result["mode"] == "normal"
        assert result["risk_percent"] == 1.0
        assert result["changed"] is False

    def test_reduced_mode_at_threshold(self, tmp_state):
        """Drawdown >= 3% should trigger risk reduction."""
        # First call: set peak equity at 10000
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Second call: equity drops to 9650 (3.5% drawdown)
        result = drawdown_recovery_risk_percent(
            equity=9650.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            reduce_threshold=3.0,
            restore_threshold=1.0,
            state_path=tmp_state,
        )
        assert result["mode"] == "reduced"
        assert result["risk_percent"] == 0.5
        assert result["changed"] is True
        assert result["drawdown_percent"] >= 3.0

    def test_recovery_below_restore_threshold(self, tmp_state):
        """Drawdown < 1% should restore normal risk."""
        # Set peak at 10000, then drop to trigger reduction
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        drawdown_recovery_risk_percent(
            equity=9650.0,  # 3.5% drawdown -> reduced
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Now recover to 9950 (0.5% drawdown from peak 10000)
        result = drawdown_recovery_risk_percent(
            equity=9950.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            reduce_threshold=3.0,
            restore_threshold=1.0,
            state_path=tmp_state,
        )
        assert result["mode"] == "normal"
        assert result["risk_percent"] == 1.0
        assert result["changed"] is True

    def test_hysteresis_between_thresholds(self, tmp_state):
        """Between 1% and 3% drawdown, mode should not change."""
        # Set up: peak 10000, drop to 9650 (3.5%) -> reduced
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        drawdown_recovery_risk_percent(
            equity=9650.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Now go to 9800 (2% drawdown) - between thresholds, should stay reduced
        result = drawdown_recovery_risk_percent(
            equity=9800.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        assert result["mode"] == "reduced"
        assert result["risk_percent"] == 0.5
        assert result["changed"] is False


class TestDrawdownRecoveryState:
    """State persistence tests."""

    def test_state_persisted_to_file(self, tmp_state):
        """State should be saved to the state file."""
        drawdown_recovery_risk_percent(
            equity=10000.0,
            state_path=tmp_state,
        )
        assert tmp_state.exists()
        state = json.loads(tmp_state.read_text(encoding="utf-8"))
        assert state["peak_equity"] == 10000.0
        assert state["mode"] == "normal"

    def test_peak_equity_tracks_highest(self, tmp_state):
        """Peak equity should only increase, never decrease."""
        drawdown_recovery_risk_percent(equity=10000.0, state_path=tmp_state)
        drawdown_recovery_risk_percent(equity=9500.0, state_path=tmp_state)
        drawdown_recovery_risk_percent(equity=10500.0, state_path=tmp_state)
        drawdown_recovery_risk_percent(equity=9800.0, state_path=tmp_state)

        state = json.loads(tmp_state.read_text(encoding="utf-8"))
        assert state["peak_equity"] == 10500.0

    def test_state_survives_reload(self, tmp_state):
        """State should persist across separate function calls."""
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Trigger reduction
        drawdown_recovery_risk_percent(
            equity=9600.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # New "session" - load state from file
        result = drawdown_recovery_risk_percent(
            equity=9600.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        assert result["mode"] == "reduced"


class TestDrawdownRecoveryDisabled:
    """Test disabled mode."""

    def test_disabled_returns_max_risk(self, tmp_state):
        """When disabled, should always return max_risk_percent."""
        result = drawdown_recovery_risk_percent(
            equity=9000.0,  # 10% drawdown
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
            enabled=False,
        )
        assert result["mode"] == "disabled"
        assert result["risk_percent"] == 1.0
        assert result["changed"] is False


class TestDrawdownRecoveryAlerts:
    """Test that alerts and journal events fire on transitions."""

    def test_alert_on_reduction(self, tmp_state, _mock_heavy_imports):
        """Should call alert when risk is reduced."""
        mock_alert = _mock_heavy_imports["notify"]
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        drawdown_recovery_risk_percent(
            equity=9600.0,  # 4% drawdown
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        mock_alert.assert_called_once()

    def test_alert_on_restoration(self, tmp_state, _mock_heavy_imports):
        """Should call alert when risk is restored."""
        mock_alert = _mock_heavy_imports["notify"]
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        drawdown_recovery_risk_percent(
            equity=9600.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Reset mock to check restoration alert
        mock_alert.reset_mock()
        drawdown_recovery_risk_percent(
            equity=9950.0,  # 0.5% drawdown -> recovery
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        mock_alert.assert_called_once()


class TestDrawdownRecoveryHelpers:
    """Test helper functions."""

    def test_get_status(self, tmp_state):
        """get_drawdown_recovery_status should return current state."""
        drawdown_recovery_risk_percent(
            equity=10000.0,
            state_path=tmp_state,
        )
        status = get_drawdown_recovery_status(state_path=tmp_state)
        assert status["peak_equity"] == 10000.0
        assert status["mode"] == "normal"

    def test_reset(self, tmp_state):
        """reset_drawdown_recovery should clear state."""
        # Set up reduced state
        drawdown_recovery_risk_percent(
            equity=10000.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        drawdown_recovery_risk_percent(
            equity=9600.0,
            max_risk_percent=1.0,
            reduced_risk_percent=0.5,
            state_path=tmp_state,
        )
        # Reset
        result = reset_drawdown_recovery(equity=10000.0, state_path=tmp_state)
        assert result["mode"] == "normal"
        assert result["peak_equity"] == 10000.0

        # Verify state is clean
        status = get_drawdown_recovery_status(state_path=tmp_state)
        assert status["mode"] == "normal"

    def test_drawdown_percent_calculation(self, tmp_state):
        """Verify drawdown percentage is calculated correctly."""
        drawdown_recovery_risk_percent(
            equity=10000.0,
            state_path=tmp_state,
        )
        result = drawdown_recovery_risk_percent(
            equity=9500.0,  # 5% drawdown
            state_path=tmp_state,
        )
        assert abs(result["drawdown_percent"] - 5.0) < 0.01
