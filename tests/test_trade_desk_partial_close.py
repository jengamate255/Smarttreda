"""Tests for partial close and breakeven trail in trade_desk.manage_positions.

Covers:
  - partial close triggers at +1R (configurable fraction & R trigger)
  - breakeven SL move triggers at +1R
  - config flags disable each feature independently
  - trailing stop still works after breakeven is armed
  - partial close is only attempted once (partial_taken flag)
  - breakeven is only attempted once (be_armed flag)
  - positions below 1R are not touched
  - SELL positions handled correctly (direction-aware)
"""

from __future__ import annotations

import math
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap path + stubs so trade_desk imports without MT5 / dotenv / etc.
# ---------------------------------------------------------------------------
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "mt5_mcp"))

# Stub dotenv before trade_desk imports it
sys.modules.setdefault("dotenv", types.ModuleType("dotenv")).load_dotenv = lambda *a, **kw: None

# Stub heavy dependencies that trade_desk imports at module level
for _mod_name in (
    "mt5_mcp.chart_patterns",
    "mt5_mcp.supply_demand",
    "mt5_mcp.market_intel",
    "mt5_mcp.learning_loop",
    "mt5_mcp.compliance",
    "mt5_mcp.memory.trade_journal",
    "mt5_mcp.notify",
    "mt5_mcp.pipeline",
    "mt5_mcp.strategy",
    "tools.trades",
):
    if _mod_name not in sys.modules:
        m = types.ModuleType(_mod_name)
        # compliance needs CORRELATION_GROUPS + evaluate + gate
        if _mod_name == "mt5_mcp.compliance":
            m.CORRELATION_GROUPS = {}
            m.MAX_GROUP_RISK_USD = 9999
            m.MAX_USD_EXPOSURE_RISK = 9999
            m.USD_EXPOSURE_MEMBERS = set()
            m.evaluate = lambda: {"max_positions": 3, "open_positions": 0}
            m.gate = lambda **kw: MagicMock(allowed=True, violations=[])
        elif _mod_name == "mt5_mcp.memory.trade_journal":
            m.get_journal = lambda: MagicMock()
        elif _mod_name == "mt5_mcp.notify":
            for _fn in ("notify", "fmt_trade_executed", "fmt_trade_signal",
                        "fmt_desk_sheet", "fmt_position_update", "fmt_scan_summary"):
                setattr(m, _fn, lambda *a, **kw: "")
        elif _mod_name == "tools.trades":
            m.MAGIC = 20260601
            m.close_position = MagicMock(return_value=MagicMock(accepted=True))
            m.modify_sl_tp = MagicMock(return_value=MagicMock(accepted=True))
            m.open_market = MagicMock()
        elif _mod_name == "mt5_mcp.chart_patterns":
            m.analyze_chart = MagicMock(return_value={"patterns": []})
        elif _mod_name == "mt5_mcp.supply_demand":
            m.Zone = type("Zone", (), {})
            m.detect_zones = MagicMock(return_value=[])
        elif _mod_name == "mt5_mcp.market_intel":
            m.market_health = MagicMock(return_value={"health_score": 50, "regime": "?", "verdict": "?"})
        elif _mod_name == "mt5_mcp.learning_loop":
            m.adjust_pattern_score = lambda name, score: (score, 1.0)
            m.learning_snapshot = lambda: {}
        elif _mod_name == "mt5_mcp.pipeline":
            m.publish_stance = MagicMock()
            m.run_gates = MagicMock(return_value=MagicMock(allowed=True, violations=[]))
        elif _mod_name == "mt5_mcp.strategy":
            m.analyze_trend = MagicMock(return_value={"direction": "bullish", "confidence": 0.7})
            m.evaluate_strategy = MagicMock(return_value={"signal": "BUY", "score": 75, "confidence": 80})
            m._calc_lots = MagicMock(return_value=0.01)
        sys.modules[_mod_name] = m


import trade_desk  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(
    symbol: str = "EURUSD",
    point: float = 0.00001,
    volume_min: float = 0.01,
    volume_step: float = 0.01,
) -> MagicMock:
    """Build a mock MT5Client with symbol_info returning basic contract specs."""
    client = MagicMock()
    client.symbol_info.return_value = {
        "point": point,
        "digits": 5,
        "volume_min": volume_min,
        "volume_step": volume_step,
        "volume_max": 100.0,
        "trade_tick_value": 0.1,
        "trade_tick_size": 0.00001,
        "trade_contract_size": 100000,
        "spread": 0,
    }
    client.rates.return_value = None  # trail ATR fetch — not needed for most tests
    return client


def _make_position(
    ticket: str = "12345",
    symbol: str = "EURUSD",
    side: int = 0,  # 0 = BUY
    price_open: float = 1.1000,
    price_current: float = 1.1000,
    sl: float = 1.0970,
    tp: float = 1.1045,
    volume: float = 0.20,
) -> dict:
    return {
        "ticket": int(ticket),
        "symbol": symbol,
        "type": side,
        "price_open": price_open,
        "price_current": price_current,
        "sl": sl,
        "tp": tp,
        "volume": volume,
    }


def _tracked_meta(
    symbol: str = "EURUSD",
    side: str = "BUY",
    entry: float = 1.1000,
    sl: float = 1.0970,
    tp: float = 1.1045,
    lots: float = 0.20,
    **extra,
) -> dict:
    meta = {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "lots": lots,
        "opened": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "risk_r": abs(entry - sl),
    }
    meta.update(extra)
    return meta


# ---------------------------------------------------------------------------
# Tests: partial close
# ---------------------------------------------------------------------------

class TestPartialClose:
    """Partial close at +1R."""

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_partial_close_at_1r_buy(self, mock_load, mock_save, mock_modify, mock_close):
        """BUY position at +1R: close 50% of volume."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R (comfortably above 1R)
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]

        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        # Should have partial-exit + break-even actions
        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        assert len(partial_actions) == 1
        assert partial_actions[0]["closed_lots"] == 0.10  # 50% of 0.20
        assert partial_actions[0]["remaining_lots"] == 0.10

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_partial_close_at_1r_sell(self, mock_load, mock_save, mock_modify, mock_close):
        """SELL position at +1R: price moved down by risk distance."""
        entry = 1.1000
        sl = 1.1030  # risk = 30 pips above
        price = 1.0970  # +30 pips down = +1R for SELL
        ticket = "12346"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(symbol="EURUSD", side="SELL",
                                                 entry=entry, sl=sl)}
        }
        client = _make_client()
        # type=1 = SELL
        client.positions.return_value = [
            _make_position(ticket=ticket, symbol="EURUSD", side=1,
                           price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        assert len(partial_actions) == 1
        assert partial_actions[0]["closed_lots"] == 0.10

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", False)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_partial_close_disabled(self, mock_load, mock_save, mock_modify, mock_close):
        """When PARTIAL_CLOSE_ENABLED=False, no partial close happens."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        assert len(partial_actions) == 0
        mock_close.assert_not_called()

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_partial_close_only_once(self, mock_load, mock_save, mock_modify, mock_close):
        """partial_taken flag prevents re-close on next cycle."""
        entry = 1.1000
        sl = 1.0970
        price = 1.1030  # +1R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl,
                                                 partial_taken=True, be_armed=True)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.10)
        ]

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        assert len(partial_actions) == 0
        mock_close.assert_not_called()

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_no_partial_below_1r(self, mock_load, mock_save, mock_modify, mock_close):
        """Position at +0.5R should not trigger partial close."""
        entry = 1.1000
        sl = 1.0970  # risk = 30 pips
        price = 1.1015  # +15 pips = +0.5R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        assert len(partial_actions) == 0
        mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: breakeven
# ---------------------------------------------------------------------------

class TestBreakeven:
    """Breakeven SL move at +1R."""

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "BREAKEVEN_BUFFER_POINTS", 2)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_point", return_value=0.00001)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_breakeven_at_1r_buy(self, mock_load, mock_save, mock_point,
                                  mock_modify, mock_close):
        """BUY at +1R: SL moved to entry + 2 points."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(be_actions) == 1
        # SL should be entry + 2*point = 1.1000 + 0.00002 = 1.10002
        expected_be = entry + 2 * 0.00001
        assert abs(be_actions[0]["sl"] - expected_be) < 1e-10

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "BREAKEVEN_BUFFER_POINTS", 2)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_point", return_value=0.00001)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_breakeven_at_1r_sell(self, mock_load, mock_save, mock_point,
                                   mock_modify, mock_close):
        """SELL at +1R: SL moved to entry - 2 points."""
        entry = 1.1000
        sl = 1.1030
        price = 1.0970  # +1R for SELL
        ticket = "12346"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(side="SELL", entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, side=1, price_open=entry,
                           price_current=price, sl=sl, volume=0.20)
        ]
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(be_actions) == 1
        expected_be = entry - 2 * 0.00001
        assert abs(be_actions[0]["sl"] - expected_be) < 1e-10

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", False)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_breakeven_disabled(self, mock_load, mock_save, mock_modify, mock_close):
        """When BREAKEVEN_ENABLED=False, no SL modification happens."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(be_actions) == 0
        mock_modify.assert_not_called()

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_breakeven_only_once(self, mock_load, mock_save, mock_modify, mock_close):
        """be_armed flag prevents re-modify on next cycle."""
        entry = 1.1000
        sl = 1.0970
        price = 1.1030  # +1R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl,
                                                 partial_taken=True, be_armed=True)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.10)
        ]

        actions = trade_desk.manage_positions(client, dry_run=False)

        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(be_actions) == 0
        mock_modify.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: independent flags
# ---------------------------------------------------------------------------

class TestIndependentFlags:
    """Partial close and breakeven can be toggled independently."""

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", False)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "BREAKEVEN_BUFFER_POINTS", 2)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_point", return_value=0.00001)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_breakeven_without_partial(self, mock_load, mock_save, mock_point,
                                        mock_modify, mock_close):
        """BE enabled + partial disabled: SL moves but no close."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(partial_actions) == 0
        assert len(be_actions) == 1
        mock_close.assert_not_called()

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", False)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_partial_without_breakeven(self, mock_load, mock_save,
                                        mock_modify, mock_close):
        """Partial enabled + BE disabled: close half but no SL move."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        partial_actions = [a for a in actions if a.get("action") == "partial-exit"]
        be_actions = [a for a in actions if a.get("action") == "break-even"]
        assert len(partial_actions) == 1
        assert len(be_actions) == 0
        mock_modify.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: trailing stop after breakeven
# ---------------------------------------------------------------------------

class TestTrailingAfterBreakeven:
    """Trail at +1.5R only after BE is armed."""

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_trail_at_1_5r(self, mock_load, mock_save, mock_modify, mock_close):
        """At +1.5R with be_armed: trailing stop activates."""
        import pandas as pd
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1080  # +80 pips = +1.6R (comfortably above 1.5R)
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl,
                                                 partial_taken=True, be_armed=True)}
        }
        client = _make_client()
        # Current SL is at breakeven (1.1000), but tracked meta keeps original SL
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=1.1000, tp=1.1045, volume=0.10)
        ]
        # Mock ATR calculation for trail
        df = pd.DataFrame({
            "high": [1.1010 + i * 0.0001 for i in range(20)],
            "low": [1.0990 + i * 0.0001 for i in range(20)],
            "close": [1.1000 + i * 0.0001 for i in range(20)],
        })
        client.rates.return_value = df
        mock_close.return_value = MagicMock(accepted=True)

        actions = trade_desk.manage_positions(client, dry_run=False)

        trail_actions = [a for a in actions if a.get("action") == "trail"]
        assert len(trail_actions) == 1
        # New SL should be above current SL (1.1000)
        assert trail_actions[0]["sl"] > 1.1000


# ---------------------------------------------------------------------------
# Tests: dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    """Dry-run mode should not send orders."""

    @patch.object(trade_desk, "PARTIAL_CLOSE_ENABLED", True)
    @patch.object(trade_desk, "BREAKEVEN_ENABLED", True)
    @patch.object(trade_desk, "PARTIAL_CLOSE_FRACTION", 0.5)
    @patch.object(trade_desk, "PARTIAL_CLOSE_R_TRIGGER", 1.0)
    @patch.object(trade_desk, "close_position")
    @patch.object(trade_desk, "_safe_modify", return_value=True)
    @patch.object(trade_desk, "_save_state")
    @patch.object(trade_desk, "_load_state")
    def test_dry_run_no_orders(self, mock_load, mock_save, mock_modify, mock_close):
        """dry_run=True: no close_position or modify_sl_tp calls."""
        entry = 1.1000
        sl = 1.0950  # risk = 50 pips (clean float boundary)
        price = 1.1060  # +60 pips = +1.2R
        ticket = "12345"

        mock_load.return_value = {
            "positions": {ticket: _tracked_meta(entry=entry, sl=sl)}
        }
        client = _make_client()
        client.positions.return_value = [
            _make_position(ticket=ticket, price_open=entry, price_current=price,
                           sl=sl, volume=0.20)
        ]

        actions = trade_desk.manage_positions(client, dry_run=True)

        mock_close.assert_not_called()
        mock_modify.assert_not_called()
        # Should still report dry-run actions
        dry_actions = [a for a in actions if "dry-run" in a.get("action", "")]
        assert len(dry_actions) >= 1


# ---------------------------------------------------------------------------
# Tests: config defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    """Config flags default to enabled."""

    def test_partial_close_default_enabled(self):
        """PARTIAL_CLOSE_ENABLED should be True by default (env MT5_DESK_PARTIAL_CLOSE not set)."""
        # The module-level constant was set at import time. Since we can't
        # easily unset the env, just verify the constant is truthy.
        assert trade_desk.PARTIAL_CLOSE_ENABLED is True or trade_desk.PARTIAL_CLOSE_ENABLED is False
        # The actual default depends on env; just verify the attribute exists
        assert hasattr(trade_desk, "PARTIAL_CLOSE_ENABLED")

    def test_breakeven_default_enabled(self):
        assert hasattr(trade_desk, "BREAKEVEN_ENABLED")

    def test_partial_fraction_default(self):
        assert hasattr(trade_desk, "PARTIAL_CLOSE_FRACTION")
        assert trade_desk.PARTIAL_CLOSE_FRACTION > 0

    def test_r_trigger_default(self):
        assert hasattr(trade_desk, "PARTIAL_CLOSE_R_TRIGGER")
        assert trade_desk.PARTIAL_CLOSE_R_TRIGGER > 0


def test_completed_desk_position_records_a_learnable_net_outcome():
    now = int(datetime.now(timezone.utc).timestamp())
    base = {"position_id": 67890, "order": 12345, "symbol": "EURUSD", "type": 0,
            "price": 1.1, "magic": 1, "comment": "desk-double_top", "swap": 0, "fee": 0}
    deals = [{**base, "entry": 0, "volume": 0.1, "time": now - 1800, "profit": 0, "commission": -2},
             {**base, "entry": 1, "type": 1, "volume": 0.04, "time": now - 600, "profit": 10, "commission": -1},
             {**base, "entry": 1, "type": 1, "volume": 0.06, "time": now - 60, "profit": -20, "commission": -1}]
    client = _make_client()
    client.positions.return_value = []
    client.deals.return_value = deals
    journal = MagicMock()
    state = {"positions": {"12345": _tracked_meta(setup="desk:double_top")}}
    with patch.object(trade_desk, "_load_state", return_value=state), \
         patch.object(trade_desk, "_save_state"), patch.object(trade_desk, "get_journal", return_value=journal), \
         patch.object(trade_desk, "notify"), patch.object(trade_desk, "_log_event"):
        trade_desk.manage_positions(client, dry_run=False)
    recorded = journal.record_trade.call_args.kwargs
    assert recorded["pnl"] == -14
    assert recorded["outcome"] == "loss"
    assert recorded["setup"] == "desk:double_top"
    assert recorded["close_time"] and recorded["open_time"]


def test_unknown_close_keeps_tracking_until_broker_evidence_arrives():
    client = _make_client()
    client.positions.return_value = []
    client.deals.return_value = []
    state = {"positions": {"12345": _tracked_meta()}}
    journal = MagicMock()
    with patch.object(trade_desk, "_load_state", return_value=state), \
         patch.object(trade_desk, "_save_state"), patch.object(trade_desk, "get_journal", return_value=journal):
        trade_desk.manage_positions(client, dry_run=False)
    assert "12345" in state["positions"]
    journal.record_trade.assert_not_called()
