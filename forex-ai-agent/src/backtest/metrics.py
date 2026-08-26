"""Performance metrics for backtest results.

All PnL figures are in R multiples (risk-normalized), which is sizing agnostic.
Breaks down performance by year, month, session and market regime where the
input frame carries those columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.engine import TradeRecord


def compute_metrics(trades: list[TradeRecord], equity_curve: pd.DataFrame) -> dict:
    r = np.array([t.pnl_r for t in trades], dtype=float)
    total = int(len(r))
    wins = r > 0
    losses = r < 0
    n_wins = int(wins.sum())
    n_losses = int(losses.sum())

    gross_win = float(r[wins].sum()) if n_wins else 0.0
    gross_loss = float(-r[losses].sum()) if n_losses else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    win_rate = n_wins / total if total else 0.0
    expectancy = float(r.mean()) if total else 0.0
    avg_win = float(r[wins].mean()) if n_wins else 0.0
    avg_loss = float(r[losses].mean()) if n_losses else 0.0

    # Streaks on the closed-trade series.
    max_win_streak = max_loss_streak = 0
    cur_w = cur_l = 0
    for x in r:
        if x > 0:
            cur_w += 1
            cur_l = 0
        elif x < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_win_streak = max(max_win_streak, cur_w)
        max_loss_streak = max(max_loss_streak, cur_l)

    equity = equity_curve["equity"].to_numpy() if len(equity_curve) else np.array([100_000.0])
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    max_dd = float(drawdown.max())
    max_dd_pct = float(max_dd / peak[np.argmax(drawdown)]) if len(peak) else 0.0

    # Sharpe / Sortino on per-bar equity returns (bar-equity is mark-to-market).
    if len(equity) > 2:
        rets = np.diff(equity) / equity[:-1]
        sharpe = float(rets.mean() / rets.std() * np.sqrt(len(equity))) if rets.std() > 0 else 0.0
        downside = rets[rets < 0]
        sortino = float(rets.mean() / downside.std() * np.sqrt(len(equity))) if len(downside) > 1 and downside.std() > 0 else 0.0
    else:
        sharpe = sortino = 0.0

    total_return = float(equity[-1] - equity[0]) if len(equity) else 0.0
    calmar = float(total_return / max_dd) if max_dd > 0 else 0.0

    largest_win = float(r.max()) if total else 0.0
    largest_loss = float(r.min()) if total else 0.0

    return {
        "total_trades": total,
        "win_rate": round(win_rate, 4),
        "loss_rate": round(n_losses / total if total else 0.0, 4),
        "no_result_rate": round((total - n_wins - n_losses) / total if total else 0.0, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_r": round(expectancy, 4),
        "average_r": round(expectancy, 4),
        "average_win_r": round(avg_win, 4),
        "average_loss_r": round(avg_loss, 4),
        "largest_win_r": round(largest_win, 4),
        "largest_loss_r": round(largest_loss, 4),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "max_drawdown_r": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "total_return_r": round(total_return, 4),
    }


def breakdown(trades: list[TradeRecord], frame: pd.DataFrame | None = None) -> dict:
    """Performance grouped by symbol, timeframe, year, month, session, regime."""
    if not trades:
        return {}

    rows = []
    for t in trades:
        rows.append({
            "time": t.time,
            "pnl_r": t.pnl_r,
            "symbol": t.symbol,
            "timeframe": t.timeframe,
            "side": t.side,
            "exit_reason": t.exit_reason,
        })
    tdf = pd.DataFrame(rows)
    if tdf.empty:
        return {}

    tdf["year"] = tdf["time"].dt.year
    tdf["month"] = tdf["time"].dt.to_period("M").astype(str)
    tdf["session"] = _session_of(tdf["time"])

    if frame is not None and not frame.empty and "structure_state" in frame.columns:
        ft = pd.to_datetime(frame["time"], utc=True)
        regime = frame["structure_state"].to_numpy()
        tdf["regime"] = [regime[int(np.argmin(np.abs(ft.to_numpy() - t.value))) if len(ft) else 0] for t in tdf["time"]]
        tdf.loc[tdf["regime"] == 1, "regime"] = "TRENDING_UP"
        tdf.loc[tdf["regime"] == -1, "regime"] = "TRENDING_DOWN"
        tdf.loc[tdf["regime"] == 0, "regime"] = "RANGING"
        tdf["regime"] = tdf["regime"].astype(str)

    out: dict[str, dict] = {}
    for group_col in ("symbol", "timeframe", "year", "month", "session", "regime"):
        if group_col not in tdf.columns:
            continue
        groups = {}
        for name, g in tdf.groupby(group_col, observed=True):
            if len(g) == 0:
                continue
            rr = g["pnl_r"].to_numpy()
            groups[str(name)] = {
                "trades": int(len(g)),
                "win_rate": round(float((rr > 0).mean()), 4),
                "expectancy_r": round(float(rr.mean()), 4),
                "profit_factor": round(float(rr[rr > 0].sum() / max(-rr[rr < 0].sum(), 1e-12)), 4),
            }
        out[group_col] = groups
    return out


def _session_of(series: pd.Series) -> pd.Series:
    h = series.dt.hour
    return h.apply(
        lambda x: "asian" if 0 <= x < 7 else ("london" if 7 <= x < 12 else ("new_york" if 12 <= x < 21 else "other"))
    )
