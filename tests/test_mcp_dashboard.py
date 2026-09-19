"""Tests for MCP Server and Dashboard API."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.mcp_server import TradingMCPServer, TOOLS
from mt5_mcp.dashboard import TradingDashboard
from mt5_mcp.domain_models import TradeSetup


def _make_candles(n=100):
    candles = []
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        o = 1.1000 + (0.001 if i % 10 < 5 else -0.001)
        h = o + 0.001
        l = o - 0.001
        c = o + (0.0005 if i % 10 < 5 else -0.0005)
        candles.append(NormalizedCandle(
            timestamp=base + timedelta(hours=i),
            symbol="EURUSD", timeframe="H1",
            open=o, high=h, low=l, close=c,
            volume=100, spread=2,
        ))
    return candles


def _candle_dicts(n=100):
    from dataclasses import asdict
    return [asdict(c) for c in _make_candles(n)]


class TestMCPTools:

    def test_tools_defined(self):
        assert len(TOOLS) == 6
        names = [t["name"] for t in TOOLS]
        assert "analyze_market" in names
        assert "calculate_risk" in names
        assert "run_backtest" in names
        assert "generate_narrative" in names
        assert "simulate_trade" in names
        assert "health_check" in names


class TestMCPServer:

    def test_health_check(self):
        server = TradingMCPServer(enable_llm=False)
        result = server.health_check()
        assert result["status"] == "success"
        assert "tools" in result
        assert result["llm_enabled"] is False

    def test_analyze_market(self):
        server = TradingMCPServer(enable_llm=False)
        candles = _candle_dicts(100)
        result = server.analyze_market("EURUSD", candles)
        assert result["status"] == "success"
        assert result["symbol"] == "EURUSD"
        assert "analysis" in result

    def test_calculate_risk(self):
        server = TradingMCPServer(enable_llm=False)
        setup = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "timeframe": "H1",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        result = server.calculate_risk(setup)
        assert result["status"] == "success"
        assert "risk_assessment" in result

    def test_run_backtest(self):
        server = TradingMCPServer(enable_llm=False)
        candles = _candle_dicts(100)
        result = server.run_backtest("EURUSD", candles)
        assert result["status"] == "success"
        assert "results" in result

    def test_simulate_trade(self):
        server = TradingMCPServer(enable_llm=False)
        setup = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "timeframe": "H1",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        candles = _candle_dicts(10)
        result = server.simulate_trade(setup, candles)
        assert result["status"] == "success"
        assert "trade" in result


class TestDashboard:

    def test_health(self):
        dash = TradingDashboard(enable_llm=False)
        result = dash.health()
        assert result["status"] == "success"
        assert result["data"]["platform"] == "Trading Intelligence Platform"

    def test_analyze_symbol(self):
        dash = TradingDashboard(enable_llm=False)
        candles = _candle_dicts(100)
        result = dash.analyze_symbol("EURUSD", candles)
        assert result["status"] == "success"
        assert result["data"]["symbol"] == "EURUSD"

    def test_get_trade_setup_cached(self):
        dash = TradingDashboard(enable_llm=False)
        candles = _candle_dicts(100)
        dash.analyze_symbol("EURUSD", candles)
        result = dash.get_trade_setup("EURUSD")
        assert result["status"] == "success"

    def test_get_trade_setup_not_cached(self):
        dash = TradingDashboard(enable_llm=False)
        result = dash.get_trade_setup("USDJPY")
        assert result["status"] == "error"

    def test_calculate_risk(self):
        dash = TradingDashboard(enable_llm=False)
        setup = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "timeframe": "H1",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        result = dash.calculate_risk_for_setup(setup)
        assert result["status"] == "success"
        assert "data" in result

    def test_run_backtest(self):
        dash = TradingDashboard(enable_llm=False)
        candles = _candle_dicts(100)
        result = dash.run_backtest("EURUSD", candles)
        assert result["status"] == "success"
        assert "data" in result

    def test_simulate_trade(self):
        dash = TradingDashboard(enable_llm=False)
        setup = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "timeframe": "H1",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        candles = _candle_dicts(10)
        result = dash.simulate_trade(setup, candles)
        assert result["status"] == "success"
        assert "data" in result

    def test_trade_history(self):
        dash = TradingDashboard(enable_llm=False)
        result = dash.get_trade_history()
        assert result["status"] == "success"
        assert result["total"] == 0

    def test_portfolio_summary_empty(self):
        dash = TradingDashboard(enable_llm=False)
        result = dash.get_portfolio_summary()
        assert result["status"] == "success"
        assert result["data"]["total_trades"] == 0

    def test_portfolio_summary_with_trades(self):
        dash = TradingDashboard(enable_llm=False)
        setup = {
            "symbol": "EURUSD",
            "direction": "LONG",
            "timeframe": "H1",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        candles = _candle_dicts(10)
        dash.simulate_trade(setup, candles)
        result = dash.get_portfolio_summary()
        assert result["status"] == "success"
        assert result["data"]["total_trades"] == 1
