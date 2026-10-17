"""PerformanceAnalyzer: multi-dimensional performance analysis with proper financial math."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd

from trading_ai.config.settings import SESSIONS, Timeframe
from trading_ai.models import BacktestConfig, ExitReason, PerformanceMetrics, TradeRecord

ANNUALIZATION_FACTOR = 252
TRADING_DAYS_PER_YEAR = 252


class PerformanceAnalyzer:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def calculate(
        self,
        trades: list[TradeRecord],
        equity_curve: pd.Series | None = None,
        drawdown_series: pd.Series | None = None,
    ) -> PerformanceMetrics:
        if not trades:
            return self._empty_metrics()

        pnls = [t.pnl for t in trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p < 0]

        total_trades = len(trades)
        win_count = len(winning)
        loss_count = len(losing)
        win_rate = win_count / total_trades if total_trades > 0 else 0.0

        gross_profit = sum(winning) if winning else 0.0
        gross_loss = abs(sum(losing)) if losing else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        avg_win = gross_profit / win_count if win_count > 0 else 0.0
        avg_loss = gross_loss / loss_count if loss_count > 0 else 0.0

        r_multiples = [t.r_multiple for t in trades if t.r_multiple != 0]
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

        avg_win_r = avg_win / (avg_loss if avg_loss > 0 else 1.0)
        win_loss_ratio = avg_win_r
        expectancy = (win_rate * win_loss_ratio) - (1 - win_rate) if avg_loss > 0 else 0.0

        net_profit = sum(pnls)

        max_dd, max_dd_pct = 0.0, 0.0
        if drawdown_series is not None and len(drawdown_series) > 0:
            max_dd_pct = float(drawdown_series.max())
            if equity_curve is not None and len(equity_curve) > 0:
                max_dd = float(equity_curve.max() - equity_curve.min())

        recovery_factor = net_profit / max_dd if max_dd > 0 else float("inf") if net_profit > 0 else 0.0

        sharpe, sortino = self._calculate_risk_adjusted_returns(equity_curve)

        calmar = 0.0
        if equity_curve is not None and len(equity_curve) > 1:
            total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
            n_periods = len(equity_curve)
            annualized = (1 + total_return) ** (ANNUALIZATION_FACTOR / n_periods) - 1 if n_periods > 0 else 0
            calmar = annualized / max_dd_pct if max_dd_pct > 0 else 0.0

        avg_bars = sum(t.bars_held for t in trades) / total_trades if total_trades > 0 else 0.0

        cw, cl = self._max_consecutive(pnls)

        initial_capital = self.config.initial_capital
        final_equity = equity_curve.iloc[-1] if equity_curve is not None and len(equity_curve) > 0 else initial_capital + net_profit
        return_pct = net_profit / initial_capital if initial_capital > 0 else 0.0

        annualized_return = 0.0
        if equity_curve is not None and len(equity_curve) > 1:
            total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
            n_periods = len(equity_curve)
            annualized_return = (1 + total_return) ** (ANNUALIZATION_FACTOR / n_periods) - 1 if n_periods > 0 else 0

        mae_stats = self._mae_mfe_stats(trades, "mae")
        mfe_stats = self._mae_mfe_stats(trades, "mfe")
        exposure_stats = self._exposure_stats(trades)

        return PerformanceMetrics(
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate=win_rate,
            expectancy=expectancy,
            average_r=avg_r,
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            recovery_factor=recovery_factor,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            average_win=avg_win,
            average_loss=avg_loss,
            largest_win=max(winning) if winning else 0.0,
            largest_loss=min(losing) if losing else 0.0,
            consecutive_wins=cw,
            consecutive_losses=cl,
            average_bars_held=avg_bars,
            total_commission=sum(t.commission for t in trades),
            total_spread_cost=sum(t.spread_cost for t in trades),
            total_slippage_cost=sum(t.slippage_cost for t in trades),
            net_profit=net_profit,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            initial_capital=initial_capital,
            final_equity=final_equity,
            return_pct=return_pct,
            annualized_return=annualized_return,
            mae_stats=mae_stats,
            mfe_stats=mfe_stats,
            exposure_stats=exposure_stats,
        )

    def segment_by(
        self,
        trades: list[TradeRecord],
        equity_curve: pd.Series | None,
        drawdown_series: pd.Series | None,
        dimension: str,
    ) -> dict[str, PerformanceMetrics]:
        groups: dict[str, list[TradeRecord]] = defaultdict(list)

        for trade in trades:
            key = self._get_segment_key(trade, dimension)
            groups[key].append(trade)

        results: dict[str, PerformanceMetrics] = {}
        for key, group_trades in groups.items():
            results[key] = self.calculate(group_trades, equity_curve, drawdown_series)
        return results

    def multi_dimensional_analysis(
        self,
        trades: list[TradeRecord],
        equity_curve: pd.Series | None = None,
        drawdown_series: pd.Series | None = None,
    ) -> dict[str, dict[str, PerformanceMetrics]]:
        dimensions = ["session", "symbol", "regime", "exit_reason"]
        result: dict[str, dict[str, PerformanceMetrics]] = {}
        for dim in dimensions:
            result[dim] = self.segment_by(trades, equity_curve, drawdown_series, dim)
        return result

    def _calculate_risk_adjusted_returns(
        self, equity_curve: pd.Series | None
    ) -> tuple[float, float]:
        if equity_curve is None or len(equity_curve) < 2:
            return 0.0, 0.0

        returns = equity_curve.pct_change().dropna()
        if len(returns) == 0:
            return 0.0, 0.0

        mean_return = float(returns.mean())
        std_return = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0

        sharpe = (mean_return * TRADING_DAYS_PER_YEAR) / (std_return * math.sqrt(TRADING_DAYS_PER_YEAR)) if std_return > 0 else 0.0

        negative_returns = returns[returns < 0]
        downside_std = float(negative_returns.std(ddof=1)) if len(negative_returns) > 1 else 0.0
        sortino = (mean_return * TRADING_DAYS_PER_YEAR) / (downside_std * math.sqrt(TRADING_DAYS_PER_YEAR)) if downside_std > 0 else 0.0

        return sharpe, sortino

    def _max_consecutive(self, values: list[float]) -> tuple[int, int]:
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for v in values:
            if v > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif v < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0

        return max_wins, max_losses

    def _mae_mfe_stats(self, trades: list[TradeRecord], field: str) -> dict[str, float]:
        values = [getattr(t, field) for t in trades]
        if not values:
            return {"mean": 0.0, "median": 0.0, "std": 0.0, "max": 0.0, "min": 0.0, "p95": 0.0}

        values.sort()
        n = len(values)
        mean = sum(values) / n
        median = values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2
        variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
        std = math.sqrt(variance)
        p95_idx = int(n * 0.95)
        p95 = values[min(p95_idx, n - 1)]

        return {
            "mean": mean,
            "median": median,
            "std": std,
            "max": max(values),
            "min": min(values),
            "p95": p95,
        }

    def _exposure_stats(self, trades: list[TradeRecord]) -> dict[str, float]:
        if not trades:
            return {"avg_bars_held": 0.0, "avg_volume": 0.0, "total_volume": 0.0, "avg_spread_cost": 0.0, "avg_slippage_cost": 0.0}

        return {
            "avg_bars_held": sum(t.bars_held for t in trades) / len(trades),
            "avg_volume": sum(t.volume for t in trades) / len(trades),
            "total_volume": sum(t.volume for t in trades),
            "avg_spread_cost": sum(t.spread_cost for t in trades) / len(trades),
            "avg_slippage_cost": sum(t.slippage_cost for t in trades) / len(trades),
        }

    def _get_segment_key(self, trade: TradeRecord, dimension: str) -> str:
        if dimension == "symbol":
            return trade.symbol
        if dimension == "exit_reason":
            return trade.exit_reason.value
        if dimension == "session":
            return self._classify_session(trade.entry_time)
        if dimension == "regime":
            return trade.metadata.get("regime", "unknown")
        if dimension == "strategy_version":
            return trade.metadata.get("strategy_version", "unknown")
        if dimension == "timeframe":
            return trade.metadata.get("timeframe", "unknown")
        return "unknown"

    def _classify_session(self, dt) -> str:
        if dt is None:
            return "unknown"
        t = dt.time() if hasattr(dt, "time") else None
        if t is None:
            return "unknown"
        sessions = []
        for name, window in SESSIONS.items():
            if window.contains(t):
                sessions.append(name)
        if not sessions:
            return "off_hours"
        if "overlap" in sessions:
            return "london_ny_overlap"
        return sessions[0]

    def _empty_metrics(self) -> PerformanceMetrics:
        return PerformanceMetrics(initial_capital=self.config.initial_capital)
