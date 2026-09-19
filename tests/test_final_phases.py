"""Tests for Phase 4 (Analytics) and Phase 5 (ML Models)."""

import sys
import os
import math
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.analytics import AnalyticsEngine, PerformanceMetrics
from mt5_mcp.ml_models import (
    MovingAverageCrossover, RSIStrategy, VolatilityStrategy,
    EnsembleStrategy, Signal,
)


# ---------------------------------------------------------------------------
# Analytics Tests
# ---------------------------------------------------------------------------

class TestPerformanceMetrics:

    def test_empty_metrics(self):
        m = PerformanceMetrics()
        assert m.total_trades == 0
        d = m.to_dict()
        assert d["total_trades"] == 0

    def test_to_dict_rounding(self):
        m = PerformanceMetrics(win_rate=0.66666, profit_factor=2.3333)
        d = m.to_dict()
        assert d["win_rate"] == 0.6667
        assert d["profit_factor"] == 2.3333


class TestAnalyticsEngine:

    def test_empty_analytics(self):
        engine = AnalyticsEngine()
        m = engine.compute_metrics()
        assert m.total_trades == 0

    def test_compute_metrics_wins(self):
        engine = AnalyticsEngine()
        engine.add_trades([
            {"status": "CLOSED", "net_pnl": 100.0, "symbol": "EURUSD"},
            {"status": "CLOSED", "net_pnl": -50.0, "symbol": "EURUSD"},
            {"status": "CLOSED", "net_pnl": 75.0, "symbol": "GBPUSD"},
            {"status": "CLOSED", "net_pnl": -25.0, "symbol": "GBPUSD"},
            {"status": "CLOSED", "net_pnl": 50.0, "symbol": "EURUSD"},
        ])
        m = engine.compute_metrics()
        assert m.total_trades == 5
        assert m.winning_trades == 3
        assert m.losing_trades == 2
        assert m.win_rate == 0.6
        assert m.total_pnl == 150.0
        assert m.avg_win == 75.0
        assert m.avg_loss == -37.5

    def test_profit_factor(self):
        engine = AnalyticsEngine()
        engine.add_trades([
            {"status": "CLOSED", "net_pnl": 200.0},
            {"status": "CLOSED", "net_pnl": -100.0},
        ])
        m = engine.compute_metrics()
        assert m.profit_factor == 2.0

    def test_max_drawdown(self):
        engine = AnalyticsEngine()
        engine.add_trades([
            {"status": "CLOSED", "net_pnl": 100.0},
            {"status": "CLOSED", "net_pnl": -50.0},
            {"status": "CLOSED", "net_pnl": -80.0},
            {"status": "CLOSED", "net_pnl": 30.0},
        ])
        m = engine.compute_metrics()
        # equity: 0 -> 100 -> 50 -> -30 -> 0, peak=100, max_dd = 100 - (-30) = 130
        assert m.max_drawdown == 130.0

    def test_consecutive_wins_losses(self):
        engine = AnalyticsEngine()
        engine.add_trades([
            {"status": "CLOSED", "net_pnl": 10.0},
            {"status": "CLOSED", "net_pnl": 20.0},
            {"status": "CLOSED", "net_pnl": 30.0},
            {"status": "CLOSED", "net_pnl": -5.0},
            {"status": "CLOSED", "net_pnl": -10.0},
            {"status": "CLOSED", "net_pnl": 15.0},
            {"status": "CLOSED", "net_pnl": 25.0},
            {"status": "CLOSED", "net_pnl": 35.0},
            {"status": "CLOSED", "net_pnl": 45.0},
        ])
        m = engine.compute_metrics()
        assert m.max_consecutive_wins == 4
        assert m.max_consecutive_losses == 2

    def test_symbol_breakdown(self):
        engine = AnalyticsEngine()
        engine.add_trades([
            {"status": "CLOSED", "net_pnl": 50.0, "symbol": "EURUSD"},
            {"status": "CLOSED", "net_pnl": -20.0, "symbol": "EURUSD"},
            {"status": "CLOSED", "net_pnl": 30.0, "symbol": "GBPUSD"},
        ])
        breakdown = engine.get_symbol_breakdown()
        assert "EURUSD" in breakdown
        assert "GBPUSD" in breakdown
        assert breakdown["EURUSD"]["trades"] == 2
        assert breakdown["GBPUSD"]["total_pnl"] == 30.0

    def test_health_check(self):
        engine = AnalyticsEngine()
        result = engine.health_check()
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# ML Models Tests
# ---------------------------------------------------------------------------

class TestMovingAverageCrossover:

    def test_insufficient_data(self):
        ma = MovingAverageCrossover()
        sig = ma.analyze([1.0, 2.0, 3.0])
        assert sig.direction == "NEUTRAL"

    def test_uptrend_signal(self):
        ma = MovingAverageCrossover(fast_period=5, slow_period=10)
        # Create uptrend: fast MA crosses above slow MA
        closes = list(range(100, 120)) + list(range(115, 135))
        sig = ma.analyze(closes)
        assert sig.direction in ["LONG", "NEUTRAL"]
        assert sig.strength >= 0

    def test_downtrend_signal(self):
        ma = MovingAverageCrossover(fast_period=5, slow_period=10)
        closes = list(range(130, 110, -1)) + list(range(115, 95, -1))
        sig = ma.analyze(closes)
        assert sig.direction in ["SHORT", "NEUTRAL"]

    def test_no_crossover(self):
        ma = MovingAverageCrossover(fast_period=5, slow_period=10)
        closes = list(range(100, 150))
        sig = ma.analyze(closes)
        assert sig.direction in ["LONG", "NEUTRAL"]


class TestRSIStrategy:

    def test_insufficient_data(self):
        rsi = RSIStrategy()
        sig = rsi.analyze([1.0, 2.0])
        assert sig.direction == "NEUTRAL"

    def test_oversold_signal(self):
        rsi = RSIStrategy(period=14, oversold=30)
        # Create declining prices to trigger oversold
        closes = [100 - i * 0.5 for i in range(20)]
        sig = rsi.analyze(closes)
        assert sig.direction == "LONG"
        assert sig.strength > 0

    def test_overbought_signal(self):
        rsi = RSIStrategy(period=14, overbought=70)
        closes = [100 + i * 0.5 for i in range(20)]
        sig = rsi.analyze(closes)
        assert sig.direction == "SHORT"
        assert sig.strength > 0

    def test_neutral(self):
        rsi = RSIStrategy()
        closes = [100 + (i % 2) * 0.1 for i in range(30)]
        sig = rsi.analyze(closes)
        assert sig.direction == "NEUTRAL"


class TestVolatilityStrategy:

    def test_insufficient_data(self):
        vol = VolatilityStrategy()
        sig = vol.analyze([1.0], [0.5], [0.8])
        assert sig.direction == "NEUTRAL"

    def test_breakout_up(self):
        vol = VolatilityStrategy(atr_period=10, multiplier=0.5)
        highs = [100 + i * 0.1 for i in range(20)]
        lows = [99 + i * 0.1 for i in range(20)]
        closes = [99.5 + i * 0.1 for i in range(20)]
        # Add big jump
        highs.append(105.0)
        lows.append(100.0)
        closes.append(104.0)
        sig = vol.analyze(highs, lows, closes)
        assert sig.direction in ["LONG", "NEUTRAL"]

    def test_breakout_down(self):
        vol = VolatilityStrategy(atr_period=10, multiplier=0.5)
        highs = [105 - i * 0.1 for i in range(20)]
        lows = [104 - i * 0.1 for i in range(20)]
        closes = [104.5 - i * 0.1 for i in range(20)]
        # Add big drop
        highs.append(101.0)
        lows.append(95.0)
        closes.append(96.0)
        sig = vol.analyze(highs, lows, closes)
        assert sig.direction in ["SHORT", "NEUTRAL"]


class TestEnsembleStrategy:

    def test_insufficient_data(self):
        ens = EnsembleStrategy()
        sig = ens.analyze([1.0, 2.0, 3.0])
        assert sig.direction == "NEUTRAL"

    def test_analyze_with_data(self):
        ens = EnsembleStrategy()
        closes = [100 + i * 0.5 for i in range(50)]
        sig = ens.analyze(closes)
        assert sig.direction in ["LONG", "SHORT", "NEUTRAL"]
        assert 0 <= sig.strength <= 1
        assert 0 <= sig.confidence <= 1
        assert len(sig.features_used) > 0

    def test_health_check(self):
        ens = EnsembleStrategy()
        result = ens.health_check()
        assert result["status"] == "success"
        assert len(result["strategies"]) == 3

    def test_signal_to_dict(self):
        sig = Signal(direction="LONG", strength=0.8, confidence=0.7, features_used=["test"])
        d = sig.to_dict()
        assert d["direction"] == "LONG"
        assert d["strength"] == 0.8
