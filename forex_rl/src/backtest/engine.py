from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..agents.policy import BaseAgent
from .metrics import BacktestMetrics, TradeResult


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    commission_per_lot: float = 7.0
    risk_per_trade: float = 0.01
    max_positions: int = 1


@dataclass
class BacktestResult:
    trades: list[TradeResult]
    equity_curve: list[float]
    metrics: dict[str, Any]
    regime_breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    split_results: list[BacktestResult]
    aggregate_metrics: dict[str, Any]
    oos_equity_curve: list[float]


class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self, agent: BaseAgent, env: Any) -> BacktestResult:
        obs, info = env.reset()
        done = False
        equity_curve = [info.get("equity", self.config.initial_balance)]
        trades: list[TradeResult] = []
        current_position: Optional[dict] = None

        while not done:
            action = agent.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

            # Use actual equity from environment, not reward-derived
            eq = info.get("equity", equity_curve[-1])
            equity_curve.append(eq)

            if "trade_closed" in info and info["trade_closed"]:
                tc = info["trade_closed"]
                trade = TradeResult(
                    entry_time=tc.get("entry_time", 0),
                    exit_time=tc.get("exit_time", 0),
                    side=tc.get("side", "BUY"),
                    entry_price=tc.get("entry_price", 0.0),
                    exit_price=tc.get("exit_price", 0.0),
                    pnl=tc.get("pnl", 0.0),
                    pnl_pct=tc.get("pnl_pct", 0.0),
                    duration=tc.get("duration", 0),
                    slippage=tc.get("slippage", 0.0),
                    spread_cost=tc.get("spread_cost", 0.0),
                    regime=tc.get("regime", "unknown"),
                )
                trades.append(trade)

        metrics = BacktestMetrics.compute_all(trades, equity_curve)
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            regime_breakdown=self._compute_regime_breakdown(trades),
        )

    def run_walk_forward(
        self, agent_factory: Any, splits: list[tuple[int, int]]
    ) -> WalkForwardResult:
        split_results: list[BacktestResult] = []
        oos_equity: list[float] = [self.config.initial_balance]

        for train_start, train_end in splits:
            agent = agent_factory()
            train_result = self.run(agent, self._create_split_env(train_start, train_end))
            oos_result = self.run(agent, self._create_split_env(train_end, train_end + (train_end - train_start)))

            split_results.append(oos_result)

            if oos_result.equity_curve:
                oos_equity.extend(oos_result.equity_curve[1:])

        all_trades = []
        for r in split_results:
            all_trades.extend(r.trades)

        aggregate = BacktestMetrics.compute_all(all_trades, oos_equity)
        return WalkForwardResult(
            split_results=split_results,
            aggregate_metrics=aggregate,
            oos_equity_curve=oos_equity,
        )

    def _compute_regime_breakdown(self, trades: list[TradeResult]) -> dict[str, Any]:
        regimes: dict[str, list[TradeResult]] = {}
        for t in trades:
            regimes.setdefault(t.regime, []).append(t)

        breakdown: dict[str, Any] = {}
        for regime, regime_trades in regimes.items():
            wins = [t for t in regime_trades if t.pnl > 0]
            total_pnl = sum(t.pnl for t in regime_trades)
            breakdown[regime] = {
                "trades": len(regime_trades),
                "win_rate": len(wins) / len(regime_trades) if regime_trades else 0,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(regime_trades) if regime_trades else 0,
            }
        return breakdown

    def _create_split_env(self, start: int, end: int) -> Any:
        return None
