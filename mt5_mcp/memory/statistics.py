"""Statistics dashboard over the journal: expectancy, profit factor, Sharpe,
drawdown, equity curve, best symbols/setups/sessions, monthly profits."""

from __future__ import annotations

import math
from collections import defaultdict

from memory.trade_journal import Journal, get_journal


def _load_trades(journal: Journal) -> list[dict]:
    trades = journal.list_trades(limit=100_000)
    trades = [t for t in trades if t.get("pnl") is not None]
    trades.sort(key=lambda t: t.get("close_time") or t.get("created_at") or "")
    return trades


def equity_curve(journal: Journal, start_balance: float = 0.0) -> list[dict]:
    trades = _load_trades(journal)
    equity = start_balance
    curve = [{"time": None, "equity": round(equity, 2)}]
    for trade in trades:
        equity += float(trade.get("pnl", 0.0) or 0.0)
        curve.append(
            {
                "time": trade.get("close_time") or trade.get("created_at"),
                "equity": round(equity, 2),
                "ticket": trade.get("ticket"),
            }
        )
    return curve


def max_drawdown(curve: list[dict]) -> dict:
    if not curve:
        return {"max_drawdown": 0.0, "max_drawdown_percent": 0.0}
    peak = curve[0]["equity"]
    peak_time = curve[0]["time"]
    max_dd = 0.0
    max_dd_pct = 0.0
    dd_start = dd_end = None
    for point in curve[1:]:
        value = point["equity"]
        if value > peak:
            peak = value
            peak_time = point["time"]
        else:
            dd = peak - value
            dd_pct = dd / peak * 100.0 if peak else 0.0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
                dd_start = peak_time
                dd_end = point["time"]
    return {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_percent": round(max_dd_pct, 2),
        "peak_time": dd_start,
        "trough_time": dd_end,
    }


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2 or sum(returns) == 0:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return 0.0
    return mean / math.sqrt(variance) * math.sqrt(len(returns))


def dashboard(start_balance: float = 0.0) -> dict:
    journal: Journal = get_journal()
    trades = _load_trades(journal)
    if not trades:
        return {"trades": 0, "message": "journal is empty - run sync_journal first"}

    pnls = [float(t.get("pnl", 0.0) or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    rrs = [float(t.get("rr", 0.0) or 0.0) for t in trades if t.get("rr")]

    monthly: dict[str, float] = defaultdict(float)
    for trade in trades:
        close_time = trade.get("close_time")
        if close_time:
            monthly[close_time[:7]] += float(trade.get("pnl", 0.0) or 0.0)

    curve = equity_curve(journal, start_balance)
    dd = max_drawdown(curve)

    from memory.learning import best_pairs, best_sessions, best_setups

    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "expectancy": round(net / len(trades), 2),
        "average_rr": round(sum(rrs) / len(rrs), 2) if rrs else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2)
        if gross_loss
        else (99.99 if gross_win else 0.0),
        "sharpe_ratio": round(_sharpe(pnls), 3),
        "net_profit": round(net, 2),
        "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "average_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "max_drawdown": dd,
        "equity_curve": curve,
        "monthly_profits": dict(sorted(monthly.items())),
        "best_sessions": best_sessions()[:5],
        "best_symbols": best_pairs()[:5],
        "best_setups": best_setups()[:5],
    }
