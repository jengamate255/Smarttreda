"""
Dashboard API for Trading Intelligence Platform.

Provides REST-like interface for interacting with the trading platform.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .backtest_engine import BacktestConfig, BacktestEngine
from .data_schema import NormalizedCandle
from .domain_models import TradeSetup
from .orchestrator import ChiefTradingAgent, OrchestratorInput
from .llm_narrative import LLMNarrativeGenerator
from .risk_engine import RiskEngine
from .trade_simulator import TradeSimulator
from .asset import AssetClass, asset_class_from_symbol


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

class TradingDashboard:
    """Dashboard API for the trading intelligence platform."""

    def __init__(self, enable_llm: bool = False):
        self.orchestrator = ChiefTradingAgent(enable_llm=enable_llm)
        self.risk_engine = RiskEngine()
        self.llm_generator = LLMNarrativeGenerator() if enable_llm else None
        self.trade_history: list[dict] = []
        self.analysis_cache: dict[str, dict] = {}

    def analyze_symbol(
        self,
        symbol: str,
        candles: list[dict],
        timeframe: str = "H1",
    ) -> dict[str, Any]:
        """Full analysis pipeline for a symbol."""
        try:
            asset_class = asset_class_from_symbol(symbol)
            norm_candles = []
            for c in candles:
                candle_data = {k: v for k, v in c.items() if k != "asset_class"}
                # Convert timestamp string to datetime if needed
                if isinstance(candle_data.get("timestamp"), str):
                    from dateutil.parser import parse
                    candle_data["timestamp"] = parse(candle_data["timestamp"])
                norm_candles.append(NormalizedCandle(**candle_data))

            input_data = OrchestratorInput(
                task_id=f"task_{symbol}_{datetime.now(timezone.utc).timestamp()}",
                symbol=symbol,
                asset_class=asset_class,
                candles=norm_candles,
            )

            result = self.orchestrator.run_pipeline(input_data)

            setups = []
            for setup in result.candidate_setups:
                setups.append({
                    "direction": setup.direction,
                    "entry": setup.entry,
                    "stop_loss": setup.stop_loss,
                    "take_profit": setup.take_profit,
                    "expected_rr": setup.expected_rr,
                    "status": setup.status.value if setup.status else None,
                })

            analysis = {
                "symbol": symbol,
                "timeframe": timeframe,
                "candidate_setups": setups,
                "data_quality_valid": result.data_quality.is_valid,
                "risk_can_trade": result.risk_result.can_trade,
                "risk_explanation": result.risk_result.explanation,
                "red_team_observations": result.red_team_observations,
                "regime_data": result.regime_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            self.analysis_cache[symbol] = analysis

            return {
                "status": "success",
                "data": analysis,
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    def get_trade_setup(self, symbol: str) -> dict[str, Any]:
        """Get cached trade setup for a symbol."""
        if symbol in self.analysis_cache:
            return {
                "status": "success",
                "data": self.analysis_cache[symbol],
            }
        return {
            "status": "error",
            "error": f"No analysis cached for {symbol}",
        }

    def calculate_risk_for_setup(self, trade_setup: dict) -> dict[str, Any]:
        """Calculate risk for a trade setup."""
        try:
            setup = TradeSetup(**trade_setup)
            risk_assessment = self.risk_engine.can_trade(setup, equity=10000.0)

            return {
                "status": "success",
                "data": {
                    "can_trade": risk_assessment.can_trade,
                    "position_size": risk_assessment.position_size,
                    "explanation": risk_assessment.explanation,
                    "risk_percent": risk_assessment.risk_percent,
                },
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    def run_backtest(
        self,
        symbol: str,
        candles: list[dict],
        initial_capital: float = 10000.0,
        risk_per_trade: float = 0.01,
    ) -> dict[str, Any]:
        """Run backtest and return results."""
        try:
            config = BacktestConfig(
                initial_capital=initial_capital,
                risk_per_trade=risk_per_trade,
            )
            engine = BacktestEngine(config)

            norm_candles = [NormalizedCandle(**c) for c in candles]
            result = engine.run(norm_candles, symbol)

            return {
                "status": "success",
                "data": {
                    "summary": result.summary(),
                    "total_trades": result.total_trades,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_pnl": result.total_pnl,
                    "max_drawdown": result.max_drawdown,
                    "sharpe_ratio": result.sharpe_ratio,
                },
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    def simulate_trade(
        self,
        trade_setup: dict,
        candles: list[dict],
    ) -> dict[str, Any]:
        """Simulate a trade and add to history."""
        try:
            setup = TradeSetup(**trade_setup)
            config = BacktestConfig()
            simulator = TradeSimulator(config)

            norm_candles = [NormalizedCandle(**c) for c in candles]

            if not norm_candles:
                return {
                    "status": "error",
                    "error": "No candle data provided",
                }

            trade = simulator.create_trade(setup, norm_candles[0])
            if not trade:
                return {
                    "status": "error",
                    "error": "Failed to create trade",
                }

            for candle in norm_candles[1:]:
                if trade.is_open:
                    simulator.check_exit(trade, candle)

            trade_record = {
                "id": trade.id,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "pnl": trade.pnl,
                "status": trade.status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            self.trade_history.append(trade_record)

            return {
                "status": "success",
                "data": trade_record,
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    def get_trade_history(self, limit: int = 10) -> dict[str, Any]:
        """Get recent trade history."""
        return {
            "status": "success",
            "data": self.trade_history[-limit:],
            "total": len(self.trade_history),
        }

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary from trade history."""
        if not self.trade_history:
            return {
                "status": "success",
                "data": {
                    "total_trades": 0,
                    "total_pnl": 0.0,
                    "win_rate": 0.0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                },
            }

        total_trades = len(self.trade_history)
        winning = sum(1 for t in self.trade_history if (t.get("pnl", 0) or 0) > 0)
        losing = total_trades - winning
        total_pnl = sum(t.get("pnl", 0) or 0 for t in self.trade_history)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

        return {
            "status": "success",
            "data": {
                "total_trades": total_trades,
                "winning_trades": winning,
                "losing_trades": losing,
                "win_rate": round(win_rate, 3),
                "total_pnl": round(total_pnl, 2),
            },
        }

    def health(self) -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "success",
            "data": {
                "platform": "Trading Intelligence Platform",
                "version": "1.0.0",
                "modules": [
                    "orchestrator",
                    "risk_engine",
                    "backtest_engine",
                    "trade_simulator",
                ],
                "llm_enabled": self.llm_generator is not None,
                "cached_symbols": list(self.analysis_cache.keys()),
                "trade_history_count": len(self.trade_history),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
