"""
MCP Server Endpoints for Trading Intelligence Platform.

Exposes trading platform capabilities as MCP tools:
- Market data analysis
- Trade setup generation
- Risk analysis
- Backtesting
- LLM narrative generation
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
# MCP Server Class
# ---------------------------------------------------------------------------

class TradingMCPServer:
    """MCP Server exposing trading intelligence platform tools."""

    def __init__(self, enable_llm: bool = False):
        self.orchestrator = ChiefTradingAgent(enable_llm=enable_llm)
        self.risk_engine = RiskEngine()
        self.llm_generator = LLMNarrativeGenerator() if enable_llm else None

    def analyze_market(self, symbol: str, candles: list[dict]) -> dict[str, Any]:
        """Analyze market data and return comprehensive analysis."""
        try:
            asset_class = asset_class_from_symbol(symbol)
            norm_candles = [
                NormalizedCandle(**{k: v for k, v in c.items() if k != "asset_class"})
                for c in candles
            ]

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
                    "confidence": setup.confidence,
                })

            return {
                "status": "success",
                "symbol": symbol,
                "analysis": {
                    "candidate_setups": setups,
                    "data_quality_valid": result.data_quality.is_valid,
                    "risk_can_trade": result.risk_result.can_trade,
                    "risk_explanation": result.risk_result.explanation,
                    "red_team_observations": result.red_team_observations,
                    "regime_data": result.regime_data,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def calculate_risk(self, trade_setup: dict) -> dict[str, Any]:
        """Calculate risk parameters for a trade setup."""
        try:
            setup = TradeSetup(**trade_setup)
            risk_assessment = self.risk_engine.can_trade(setup, equity=10000.0)

            return {
                "status": "success",
                "risk_assessment": {
                    "can_trade": risk_assessment.can_trade,
                    "position_size": risk_assessment.position_size,
                    "explanation": risk_assessment.explanation,
                    "risk_percent": risk_assessment.risk_percent,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def run_backtest(
        self,
        symbol: str,
        candles: list[dict],
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Run backtest on historical data."""
        try:
            bt_config = BacktestConfig(**config) if config else BacktestConfig()
            engine = BacktestEngine(bt_config)

            norm_candles = [NormalizedCandle(**c) for c in candles]
            result = engine.run(norm_candles, symbol)

            return {
                "status": "success",
                "results": {
                    "total_trades": result.total_trades,
                    "winning_trades": result.winning_trades,
                    "losing_trades": result.losing_trades,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_pnl": result.total_pnl,
                    "max_drawdown": result.max_drawdown,
                    "sharpe_ratio": result.sharpe_ratio,
                },
                "summary": result.summary(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def generate_narrative(
        self,
        symbol: str,
        analysis: dict,
        trade_setup: dict | None = None,
    ) -> dict[str, Any]:
        """Generate LLM narrative for trade analysis."""
        if not self.llm_generator:
            return {
                "status": "error",
                "error": "LLM not enabled. Initialize with enable_llm=True.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        try:
            from .domain_models import TradeSetup

            setup = TradeSetup(**trade_setup) if trade_setup else None

            import asyncio
            narrative = asyncio.run(
                self.llm_generator.generate_trade_narrative(
                    symbol=symbol,
                    analysis_result=analysis,
                    trade_setup=setup,
                )
            )

            return {
                "status": "success",
                "narrative": {
                    "thesis": narrative.thesis,
                    "risk_assessment": narrative.risk_assessment,
                    "exit_strategy": narrative.exit_strategy,
                    "confidence": narrative.confidence,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def simulate_trade(self, trade_setup: dict, candles: list[dict]) -> dict[str, Any]:
        """Simulate a trade on historical data."""
        try:
            setup = TradeSetup(**trade_setup)
            config = BacktestConfig()
            simulator = TradeSimulator(config)

            norm_candles = [NormalizedCandle(**c) for c in candles]
            entry_candle = norm_candles[0] if norm_candles else None

            if not entry_candle:
                return {
                    "status": "error",
                    "error": "No candle data provided",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            trade = simulator.create_trade(setup, entry_candle)
            if not trade:
                return {
                    "status": "error",
                    "error": "Failed to create trade",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            # Simulate through candles
            for candle in norm_candles[1:]:
                if trade.is_open:
                    simulator.check_exit(trade, candle)

            return {
                "status": "success",
                "trade": {
                    "id": trade.id,
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "pnl": trade.pnl,
                    "status": trade.status,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def health_check(self) -> dict[str, Any]:
        """Check server health and available tools."""
        return {
            "status": "success",
            "tools": [
                "analyze_market",
                "calculate_risk",
                "run_backtest",
                "generate_narrative",
                "simulate_trade",
                "health_check",
            ],
            "llm_enabled": self.llm_generator is not None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Tool Definitions (for MCP protocol)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "analyze_market",
        "description": "Analyze market data and generate trade setups",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Trading symbol"},
                "candles": {
                    "type": "array",
                    "description": "List of candle data",
                    "items": {"type": "object"},
                },
            },
            "required": ["symbol", "candles"],
        },
    },
    {
        "name": "calculate_risk",
        "description": "Calculate risk parameters for a trade setup",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trade_setup": {"type": "object", "description": "Trade setup details"},
            },
            "required": ["trade_setup"],
        },
    },
    {
        "name": "run_backtest",
        "description": "Run backtest on historical data",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "candles": {"type": "array", "items": {"type": "object"}},
                "config": {"type": "object"},
            },
            "required": ["symbol", "candles"],
        },
    },
    {
        "name": "generate_narrative",
        "description": "Generate LLM narrative for trade analysis",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "analysis": {"type": "object"},
                "trade_setup": {"type": "object"},
            },
            "required": ["symbol", "analysis"],
        },
    },
    {
        "name": "simulate_trade",
        "description": "Simulate a trade on historical data",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trade_setup": {"type": "object"},
                "candles": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["trade_setup", "candles"],
        },
    },
    {
        "name": "health_check",
        "description": "Check server health and available tools",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
