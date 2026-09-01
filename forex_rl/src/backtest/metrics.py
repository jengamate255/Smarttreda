from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class TradeResult:
    entry_time: Any
    exit_time: Any
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    duration: Any
    slippage: float = 0.0
    spread_cost: float = 0.0
    regime: str = "unknown"


class BacktestMetrics:
    @staticmethod
    def compute_all(
        trades: list[TradeResult],
        equity_curve: list[float],
        risk_free_rate: float = 0.0,
    ) -> dict[str, Any]:
        eq = np.array(equity_curve)
        if len(eq) < 2:
            return {}

        returns = np.diff(eq) / eq[:-1]
        total_return = (eq[-1] - eq[0]) / eq[0]

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        gross_profit = sum(t.pnl for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0.0
        # Clamp inf: 0 losing trades with any trades means PF is undefined.
        # Use gross_profit as a finite cap so aggregates don't blow up to inf
        # (degenerate folds previously turned mean-PF into inf).
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = max(gross_profit, 1.0)  # finite cap
        else:
            profit_factor = 0.0

        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1) if len(returns) > 1 else 0.0
        downside = returns[returns < 0]
        downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 0.0

        sharpe = (
            (mean_ret - risk_free_rate) / std_ret * np.sqrt(252)
            if std_ret > 0
            else 0.0
        )
        sortino = (
            (mean_ret - risk_free_rate) / downside_std * np.sqrt(252)
            if downside_std > 0
            else 0.0
        )

        running_max = np.maximum.accumulate(eq)
        drawdowns = (running_max - eq) / running_max
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        dd_durations: list[int] = []
        in_dd = False
        dd_len = 0
        for d in drawdowns:
            if d > 0:
                in_dd = True
                dd_len += 1
            else:
                if in_dd:
                    dd_durations.append(dd_len)
                in_dd = False
                dd_len = 0
        if in_dd:
            dd_durations.append(dd_len)
        avg_dd = float(np.mean(dd_durations)) if dd_durations else 0.0

        win_rate = len(wins) / len(trades) if trades else 0.0
        loss_rate = 1.0 - win_rate

        avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
        avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0

        expectancy = (
            win_rate * avg_win + loss_rate * avg_loss if trades else 0.0
        )

        avg_holding = (
            float(np.mean([t.duration / 24.0 if isinstance(t.duration, (int, float)) else t.duration.total_seconds() / 3600 for t in trades]))
            if trades
            else 0.0
        )

        pnl_list = [t.pnl for t in trades]
        largest_win = max(pnl_list) if pnl_list else 0.0
        largest_loss = min(pnl_list) if pnl_list else 0.0

        max_consec_wins = _max_consecutive(trades, positive=True)
        max_consec_losses = _max_consecutive(trades, positive=False)

        calmar = abs(total_return / max_dd) if max_dd > 0 else 0.0

        return {
            "total_return": float(total_return),
            "net_profit": float(eq[-1] - eq[0]),
            "profit_factor": profit_factor,
            "sharpe_ratio": float(sharpe),
            "sortino_ratio": float(sortino),
            "calmar_ratio": float(calmar),
            "max_drawdown": max_dd,
            "avg_drawdown_duration": avg_dd,
            "win_rate": win_rate,
            "loss_rate": loss_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "num_trades": len(trades),
            "avg_holding_time_hours": avg_holding,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        }

    @staticmethod
    def regime_report(trades: list[TradeResult], regimes: list[str]) -> pd.DataFrame:
        rows = []
        for regime in regimes:
            regime_trades = [t for t in trades if t.regime == regime]
            if not regime_trades:
                rows.append({"regime": regime, "trades": 0, "win_rate": 0, "pnl": 0, "avg_pnl": 0})
                continue
            wins = [t for t in regime_trades if t.pnl > 0]
            total_pnl = sum(t.pnl for t in regime_trades)
            rows.append(
                {
                    "regime": regime,
                    "trades": len(regime_trades),
                    "win_rate": len(wins) / len(regime_trades),
                    "pnl": total_pnl,
                    "avg_pnl": total_pnl / len(regime_trades),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def monthly_returns(equity_curve: list[float]) -> pd.DataFrame:
        eq = pd.Series(equity_curve)
        monthly = eq.resample("M").last().pct_change().dropna()
        return monthly.to_frame("monthly_return")

    @staticmethod
    def drawdown_curve(equity_curve: list[float]) -> pd.Series:
        eq = pd.Series(equity_curve, dtype=float)
        running_max = eq.cummax()
        drawdown = (running_max - eq) / running_max
        return drawdown


def _max_consecutive(trades: list[TradeResult], positive: bool) -> int:
    max_c = 0
    current = 0
    for t in trades:
        is_win = t.pnl > 0
        if is_win == positive:
            current += 1
            max_c = max(max_c, current)
        else:
            current = 0
    return max_c
