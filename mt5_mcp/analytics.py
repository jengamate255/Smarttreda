"""Advanced Analytics Engine.

Provides statistical analysis, performance metrics, and risk analytics.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, field


@dataclass
class PerformanceMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_trade_duration_min: float = 0.0
    risk_reward_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


class AnalyticsEngine:
    """Computes advanced trading analytics."""

    def __init__(self):
        self._trade_history: list[dict[str, Any]] = []

    def add_trades(self, trades: list[dict[str, Any]]) -> None:
        self._trade_history.extend(trades)

    def clear(self) -> None:
        self._trade_history.clear()

    def compute_metrics(self) -> PerformanceMetrics:
        closed = [t for t in self._trade_history if t.get("status") == "CLOSED"]
        if not closed:
            return PerformanceMetrics()

        pnls = [t.get("net_pnl", t.get("pnl", 0.0)) for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(closed) if closed else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if closed else 0.0

        # Max drawdown
        peak = 0.0
        equity = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0

        # Sharpe (assuming risk-free rate = 0)
        if len(pnls) > 1:
            mean_ret = sum(pnls) / len(pnls)
            variance = sum((p - mean_ret) ** 2 for p in pnls) / (len(pnls) - 1)
            std_dev = math.sqrt(variance)
            sharpe = (mean_ret / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0
        else:
            sharpe = 0.0

        # Sortino (downside deviation)
        downside = [p for p in pnls if p < 0]
        if len(downside) > 1:
            mean_ret = sum(pnls) / len(pnls)
            down_var = sum(p ** 2 for p in downside) / len(downside)
            down_std = math.sqrt(down_var)
            sortino = (mean_ret / down_std) * math.sqrt(252) if down_std > 0 else 0.0
        else:
            sortino = 0.0

        calmar = total_pnl / max_dd if max_dd > 0 else 0.0

        # Consecutive wins/losses
        max_consec_wins = max_consec_losses = current_wins = current_losses = 0
        for p in pnls:
            if p > 0:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)

        # Avg duration
        durations = [t.get("duration_minutes", 0) for t in closed if t.get("duration_minutes")]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        # Risk-reward
        risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        return PerformanceMetrics(
            total_trades=len(closed),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            total_pnl=round(total_pnl, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            expectancy=round(expectancy, 4),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            calmar_ratio=round(calmar, 4),
            max_consecutive_wins=max_consec_wins,
            max_consecutive_losses=max_consec_losses,
            avg_trade_duration_min=round(avg_duration, 1),
            risk_reward_ratio=round(risk_reward, 4),
        )

    def get_symbol_breakdown(self) -> dict[str, dict[str, Any]]:
        by_symbol: dict[str, list[float]] = {}
        for t in self._trade_history:
            if t.get("status") == "CLOSED":
                sym = t.get("symbol", "UNKNOWN")
                pnl = t.get("net_pnl", t.get("pnl", 0.0))
                by_symbol.setdefault(sym, []).append(pnl)

        result = {}
        for sym, pnls in by_symbol.items():
            wins = [p for p in pnls if p > 0]
            result[sym] = {
                "trades": len(pnls),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0,
                "total_pnl": round(sum(pnls), 2),
                "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            }
        return result

    def get_time_analysis(self) -> dict[str, Any]:
        hourly_pnl: dict[int, float] = {}
        for t in self._trade_history:
            if t.get("status") == "CLOSED" and t.get("open_time"):
                try:
                    dt = datetime.fromisoformat(t["open_time"])
                    hour = dt.hour
                    pnl = t.get("net_pnl", t.get("pnl", 0.0))
                    hourly_pnl[hour] = hourly_pnl.get(hour, 0.0) + pnl
                except Exception:
                    pass

        best_hour = max(hourly_pnl, key=hourly_pnl.get) if hourly_pnl else None
        worst_hour = min(hourly_pnl, key=hourly_pnl.get) if hourly_pnl else None

        return {
            "hourly_pnl": {str(k): round(v, 2) for k, v in sorted(hourly_pnl.items())},
            "best_hour": best_hour,
            "worst_hour": worst_hour,
        }

    def health_check(self) -> dict[str, Any]:
        metrics = self.compute_metrics()
        return {
            "status": "success",
            "total_trades": len(self._trade_history),
            "metrics": metrics.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
