"""Tests for Phase 3 - Live Trading Integration."""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.connection import MT5ConnectionManager, ConnectionStatus
from mt5_mcp.execution import OrderExecutor, Order, OrderSide, OrderType, OrderStatus
from mt5_mcp.positions import PositionManager, Position
from mt5_mcp.account_monitor import AccountMonitor, AccountSnapshot
from mt5_mcp.journal import TradeJournal, TradeEntry


# ---------------------------------------------------------------------------
# Connection Manager Tests
# ---------------------------------------------------------------------------

class TestMT5ConnectionManager:

    def test_initial_status(self):
        mgr = MT5ConnectionManager()
        assert mgr.status == ConnectionStatus.DISCONNECTED
        assert not mgr.is_connected

    def test_connect_mock_mode(self):
        mgr = MT5ConnectionManager()
        result = mgr.connect(login=12345, server="MockServer")
        assert result["status"] == "success"
        assert mgr.is_connected
        assert mgr.account_info["login"] == 12345
        assert mgr.account_info["balance"] == 10000.0

    def test_disconnect(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        result = mgr.disconnect()
        assert result["status"] == "success"
        assert not mgr.is_connected

    def test_get_symbol_info_connected(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        info = mgr.get_symbol_info("EURUSD")
        assert info is not None
        assert info["symbol"] == "EURUSD"
        assert info["point"] == 0.0001

    def test_get_symbol_info_disconnected(self):
        mgr = MT5ConnectionManager()
        info = mgr.get_symbol_info("EURUSD")
        assert info is None

    def test_get_tick(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        tick = mgr.get_tick("EURUSD")
        assert tick is not None
        assert "bid" in tick
        assert "ask" in tick

    def test_health_check(self):
        mgr = MT5ConnectionManager()
        result = mgr.health_check()
        assert result["status"] == "success"
        assert result["connection_status"] == "DISCONNECTED"


# ---------------------------------------------------------------------------
# Order Executor Tests
# ---------------------------------------------------------------------------

class TestOrderExecutor:

    def test_market_order_not_connected(self):
        executor = OrderExecutor()
        order = executor.send_market_order("EURUSD", OrderSide.BUY, 0.1)
        assert order.status == OrderStatus.REJECTED
        assert order.error == "Not connected to MT5"

    def test_market_order_mock(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        executor = OrderExecutor(connection_manager=mgr)
        order = executor.send_market_order("EURUSD", OrderSide.BUY, 0.1, stop_loss=1.095, take_profit=1.110)
        assert order.status == OrderStatus.FILLED
        assert order.fill_price is not None
        assert order.fill_volume == 0.1

    def test_market_order_sell(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        executor = OrderExecutor(connection_manager=mgr)
        order = executor.send_market_order("EURUSD", OrderSide.SELL, 0.05)
        assert order.status == OrderStatus.FILLED

    def test_limit_order_mock(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        executor = OrderExecutor(connection_manager=mgr)
        order = executor.send_limit_order("EURUSD", OrderSide.BUY, 0.1, 1.0900)
        assert order.status == OrderStatus.PENDING
        assert order.id in executor._pending_orders

    def test_cancel_order(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        executor = OrderExecutor(connection_manager=mgr)
        order = executor.send_limit_order("EURUSD", OrderSide.BUY, 0.1, 1.0900)
        result = executor.cancel_order(order.id)
        assert result["status"] == "success"

    def test_cancel_nonexistent(self):
        executor = OrderExecutor()
        result = executor.cancel_order("fake_id")
        assert result["status"] == "error"

    def test_get_pending_orders(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        executor = OrderExecutor(connection_manager=mgr)
        executor.send_limit_order("EURUSD", OrderSide.BUY, 0.1, 1.0900)
        orders = executor.get_pending_orders()
        assert len(orders) == 1


# ---------------------------------------------------------------------------
# Position Manager Tests
# ---------------------------------------------------------------------------

class TestPositionManager:

    def test_initial_state(self):
        pm = PositionManager()
        assert pm.get_total_pnl() == 0.0
        assert pm.get_total_volume() == 0.0

    def test_refresh_empty(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        pm = PositionManager(connection_manager=mgr)
        positions = pm.refresh_positions()
        assert positions == []

    def test_get_positions_by_symbol(self):
        pm = PositionManager()
        p1 = Position(id=1, symbol="EURUSD", side="BUY", volume=0.1, open_price=1.1)
        p2 = Position(id=2, symbol="GBPUSD", side="SELL", volume=0.05, open_price=1.27)
        pm._positions = {1: p1, 2: p2}
        eurusd = pm.get_positions_by_symbol("EURUSD")
        assert len(eurusd) == 1
        assert eurusd[0].symbol == "EURUSD"

    def test_get_summary(self):
        pm = PositionManager()
        pm._positions = {
            1: Position(id=1, symbol="EURUSD", side="BUY", volume=0.1, open_price=1.1, profit=50.0),
            2: Position(id=2, symbol="EURUSD", side="SELL", volume=0.05, open_price=1.12, profit=-20.0),
        }
        summary = pm.get_summary()
        assert summary["total_positions"] == 2
        assert summary["total_pnl"] == 30.0
        assert summary["winning"] == 1
        assert summary["losing"] == 1

    def test_close_position_mock(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        pm = PositionManager(connection_manager=mgr)
        pm._positions = {
            1: Position(id=1, symbol="EURUSD", side="BUY", volume=0.1, open_price=1.1, pnl=50.0)
        }
        result = pm.close_position(1)
        assert result["status"] == "success"
        assert 1 not in pm._positions

    def test_close_nonexistent(self):
        pm = PositionManager()
        result = pm.close_position(999)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Account Monitor Tests
# ---------------------------------------------------------------------------

class TestAccountMonitor:

    def test_snapshot_not_connected(self):
        monitor = AccountMonitor()
        snap = monitor.snapshot()
        assert snap.balance == 0.0
        assert snap.equity == 0.0

    def test_snapshot_connected(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        monitor = AccountMonitor(connection_manager=mgr)
        snap = monitor.snapshot()
        assert snap.balance == 10000.0
        assert snap.equity == 10000.0
        assert snap.leverage == 100

    def test_drawdown_pct(self):
        snap = AccountSnapshot(balance=10000, equity=9500)
        assert snap.drawdown_pct == 5.0

    def test_margin_used_pct(self):
        snap = AccountSnapshot(equity=10000, margin=2000)
        assert snap.margin_used_pct == 20.0

    def test_get_history(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        monitor = AccountMonitor(connection_manager=mgr)
        monitor.snapshot()
        monitor.snapshot()
        history = monitor.get_history()
        assert len(history) == 2

    def test_health_check(self):
        mgr = MT5ConnectionManager()
        mgr.connect()
        monitor = AccountMonitor(connection_manager=mgr)
        result = monitor.health_check()
        assert result["status"] == "success"
        assert result["account"]["balance"] == 10000.0


# ---------------------------------------------------------------------------
# Trade Journal Tests
# ---------------------------------------------------------------------------

class TestTradeJournal:

    def test_record_open(self):
        journal = TradeJournal(journal_dir="test_journal")
        entry = journal.record_open("t1", "EURUSD", "BUY", 0.1, 1.1000)
        assert entry.status == "OPEN"
        assert entry.symbol == "EURUSD"
        # Cleanup
        import shutil
        shutil.rmtree("test_journal", ignore_errors=True)

    def test_record_close(self):
        journal = TradeJournal(journal_dir="test_journal2")
        journal.record_open("t1", "EURUSD", "BUY", 0.1, 1.1000)
        closed = journal.record_close("t1", 1.1050, 50.0)
        assert closed is not None
        assert closed.status == "CLOSED"
        assert closed.net_pnl == 50.0
        import shutil
        shutil.rmtree("test_journal2", ignore_errors=True)

    def test_close_nonexistent(self):
        journal = TradeJournal(journal_dir="test_journal3")
        result = journal.record_close("fake", 1.0, 0.0)
        assert result is None
        import shutil
        shutil.rmtree("test_journal3", ignore_errors=True)

    def test_get_stats(self):
        journal = TradeJournal(journal_dir="test_journal4")
        journal.record_open("t1", "EURUSD", "BUY", 0.1, 1.1000)
        journal.record_close("t1", 1.1050, 50.0)
        journal.record_open("t2", "GBPUSD", "SELL", 0.05, 1.2700)
        journal.record_close("t2", 1.2750, -25.0)
        stats = journal.get_stats()
        assert stats["closed"] == 2
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["total_pnl"] == 25.0
        import shutil
        shutil.rmtree("test_journal4", ignore_errors=True)

    def test_health_check(self):
        journal = TradeJournal(journal_dir="test_journal5")
        result = journal.health_check()
        assert result["status"] == "success"
        import shutil
        shutil.rmtree("test_journal5", ignore_errors=True)
