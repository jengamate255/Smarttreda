"""Trading metrics over backtest trade frames."""

from __future__ import annotations

import numpy as np
import pandas as pd


def trade_metrics(trades: pd.DataFrame, start_balance: float = 10_000.0) -> dict:
    """Expectancy-first metrics over a trade frame (pnl in account currency)."""
    if trades is None or len(trades) == 0:
        return {"trades": 0}
    pnl = trades["pnl"].to_numpy(float)
    r = trades["r_multiple"].to_numpy(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else (np.inf if gross_win > 0 else 0.0)

    equity = start_balance + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[start_balance], equity]))
    dd_series = (peak - np.concatenate([[start_balance], equity])) / peak
    max_dd = float(dd_series.max() * 100.0)

    max_consec_loss = cur = 0
    for v in pnl:
        if v <= 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0
    max_consec_win = cur = 0
    for v in pnl:
        if v > 0:
            cur += 1
            max_consec_win = max(max_consec_win, cur)
        else:
            cur = 0

    sharpe = float("nan")
    if len(pnl) > 1 and pnl.std() > 0:
        sharpe = float(np.mean(pnl) / pnl.std() * np.sqrt(len(pnl)))

    return {
        "trades": len(trades),
        "win_rate": float(np.mean(pnl > 0)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy": float(pnl.mean()),
        "expectancy_r": float(r.mean()) if len(r) else 0.0,
        "total_pnl": float(pnl.sum()),
        "total_r": float(r.sum()),
        "profit_factor": float(pf),
        "max_drawdown_pct": max_dd,
        "sharpe_approx": sharpe,
        "max_consecutive_losses": int(max_consec_loss),
        "max_consecutive_wins": int(max_consec_win),
        "final_balance": float(start_balance + pnl.sum()),
        "avg_bars_held": float(trades["bars_held"].mean()) if "bars_held" in trades else 0.0,
    }


def exit_reason_breakdown(trades: pd.DataFrame) -> dict:
    if trades is None or len(trades) == 0 or "exit_reason" not in trades:
        return {}
    return trades["exit_reason"].value_counts().to_dict()
