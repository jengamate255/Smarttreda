"""Tests for Backtesting Framework."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.backtest_engine import BacktestConfig, BacktestResult, BacktestEngine, TradeRecord
from mt5_mcp.trade_simulator import TradeSimulator
from mt5_mcp.domain_models import TradeSetup
from mt5_mcp.asset import AssetClass


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


class TestBacktestConfig:

    def test_default_config(self):
        config = BacktestConfig()
        assert config.initial_capital == 10000.0
        assert config.risk_per_trade == 0.01
        assert config.max_positions == 5

    def test_custom_config(self):
        config = BacktestConfig(initial_capital=50000.0, risk_per_trade=0.02)
        assert config.initial_capital == 50000.0
        assert config.risk_per_trade == 0.02


class TestTradeRecord:

    def test_trade_record_creation(self):
        trade = TradeRecord(
            id="trade_1", symbol="EURUSD", direction="LONG",
            entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc), entry_price=1.1000,
        )
        assert trade.is_open
        assert not trade.is_winner

    def test_trade_record_winner(self):
        trade = TradeRecord(
            id="trade_1", symbol="EURUSD", direction="LONG",
            entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc), entry_price=1.1000,
            exit_price=1.1050, pnl=50.0, status="CLOSED_TP",
        )
        assert not trade.is_open
        assert trade.is_winner


class TestBacktestResult:

    def test_empty_result(self):
        config = BacktestConfig()
        result = BacktestResult(config=config)
        assert result.total_trades == 0

    def test_calculate_metrics(self):
        config = BacktestConfig(initial_capital=10000.0)
        result = BacktestResult(
            config=config,
            trades=[
                TradeRecord(id="1", symbol="EURUSD", direction="LONG",
                           entry_time=datetime.now(timezone.utc), entry_price=1.1,
                           exit_price=1.105, pnl=50.0, status="CLOSED_TP"),
                TradeRecord(id="2", symbol="EURUSD", direction="LONG",
                           entry_time=datetime.now(timezone.utc), entry_price=1.1,
                           exit_price=1.095, pnl=-50.0, status="CLOSED_SL"),
            ],
            equity_curve=[10000, 10050, 10000],
        )
        result.calculate_metrics()
        assert result.total_trades == 2
        assert result.winning_trades == 1
        assert result.losing_trades == 1
        assert result.win_rate == 0.5
        assert result.total_pnl == 0.0

    def test_summary(self):
        config = BacktestConfig(initial_capital=10000.0)
        result = BacktestResult(
            config=config, total_trades=10, winning_trades=6,
            losing_trades=4, win_rate=0.6, total_pnl=200.0,
        )
        summary = result.summary()
        assert summary["total_trades"] == 10
        assert summary["win_rate"] == 0.6


class TestTradeSimulator:

    def test_create_trade(self):
        config = BacktestConfig()
        sim = TradeSimulator(config)
        setup = TradeSetup(
            symbol="EURUSD", direction="LONG", timeframe="H1",
            entry=1.1000, stop_loss=1.0950, take_profit=1.1100,
        )
        candle = NormalizedCandle(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="EURUSD", timeframe="H1",
            open=1.0990, high=1.1010, low=1.0980, close=1.1000,
            volume=100, spread=2,
        )
        trade = sim.create_trade(setup, candle)
        assert trade is not None
        assert trade.is_open
        assert trade.direction == "LONG"
        assert trade.position_size > 0

    def test_check_exit_sl(self):
        config = BacktestConfig()
        sim = TradeSimulator(config)
        setup = TradeSetup(
            symbol="EURUSD", direction="LONG", timeframe="H1",
            entry=1.1000, stop_loss=1.0950, take_profit=1.1100,
        )
        candle = NormalizedCandle(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="EURUSD", timeframe="H1",
            open=1.1000, high=1.1010, low=1.0980, close=1.1000,
            volume=100, spread=2,
        )
        trade = sim.create_trade(setup, candle)
        exit_candle = NormalizedCandle(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            symbol="EURUSD", timeframe="H1",
            open=1.0990, high=1.0995, low=1.0940, close=1.0945,
            volume=100, spread=2,
        )
        closed = sim.check_exit(trade, exit_candle)
        assert closed
        assert trade.status == "CLOSED_SL"
        assert trade.pnl < 0

    def test_check_exit_tp(self):
        config = BacktestConfig()
        sim = TradeSimulator(config)
        setup = TradeSetup(
            symbol="EURUSD", direction="LONG", timeframe="H1",
            entry=1.1000, stop_loss=1.0950, take_profit=1.1100,
        )
        candle = NormalizedCandle(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            symbol="EURUSD", timeframe="H1",
            open=1.1000, high=1.1010, low=1.0980, close=1.1000,
            volume=100, spread=2,
        )
        trade = sim.create_trade(setup, candle)
        exit_candle = NormalizedCandle(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            symbol="EURUSD", timeframe="H1",
            open=1.1050, high=1.1120, low=1.1040, close=1.1110,
            volume=100, spread=2,
        )
        closed = sim.check_exit(trade, exit_candle)
        assert closed
        assert trade.status == "CLOSED_TP"
        assert trade.pnl > 0


class TestBacktestEngine:

    def test_run_insufficient_data(self):
        config = BacktestConfig()
        engine = BacktestEngine(config)
        candles = _make_candles(n=10)
        result = engine.run(candles, "EURUSD")
        assert result.total_trades == 0

    def test_run_with_data(self):
        config = BacktestConfig(initial_capital=10000.0)
        engine = BacktestEngine(config)
        candles = _make_candles(n=100)
        result = engine.run(candles, "EURUSD")
        assert result.config == config
        assert len(result.equity_curve) > 0

    def test_run_produces_metrics(self):
        config = BacktestConfig()
        engine = BacktestEngine(config)
        candles = _make_candles(n=200)
        result = engine.run(candles, "EURUSD")
        assert isinstance(result.total_trades, int)
        assert isinstance(result.win_rate, float)
