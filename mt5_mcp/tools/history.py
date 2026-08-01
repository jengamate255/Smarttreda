"""History tools: closed trades, win rate, averages, monthly/daily profits."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from client import get_client
from common import pip_size


def _order_stats(order: dict) -> dict | None:
    symbol = str(order.get("symbol", ""))
    profit = float(order.get("profit", 0.0) or 0.0)
    if not symbol or profit == 0.0:
        return None
    try:
        info = get_client().symbol_info(symbol)
        pip = pip_size(info)
    except Exception:
        pip = 0.0001
    is_buy = int(order.get("type", 0)) == 0
    open_price = float(order.get("price_open", 0.0) or 0.0)
    close_price = float(order.get("price_current", open_price) or open_price)
    pips = (
        (close_price - open_price) / pip if is_buy else (open_price - close_price) / pip
    )
    sl = float(order.get("sl", 0.0) or 0.0)
    tp = float(order.get("tp", 0.0) or 0.0)
    rr = None
    if sl and tp:
        sl_dist = abs(open_price - sl)
        tp_dist = abs(tp - open_price)
        rr = tp_dist / sl_dist if sl_dist else None
    return {
        "ticket": int(order.get("ticket", 0)),
        "symbol": symbol,
        "side": "BUY" if is_buy else "SELL",
        "volume": float(order.get("volume", 0.0) or 0.0),
        "open_price": open_price,
        "close_price": close_price,
        "pips": round(pips, 1),
        "profit": round(profit, 2),
        "rr": round(rr, 2) if rr else None,
        "open_time": datetime.fromtimestamp(
            float(order.get("time_setup", 0) or 0), tz=timezone.utc
        ).isoformat()
        if order.get("time_setup")
        else None,
        "close_time": datetime.fromtimestamp(
            float(order.get("time_done", 0) or 0), tz=timezone.utc
        ).isoformat()
        if order.get("time_done")
        else None,
        "comment": str(order.get("comment", "")),
    }


def trade_history(days: int = 30, symbol: str = "") -> list[dict]:
    client = get_client()
    client.ensure_connected()
    orders = client.closed_orders(days=days, symbol=symbol)
    return [row for row in (_order_stats(o) for o in orders) if row]


def history_statistics(days: int = 30, symbol: str = "") -> dict:
    client = get_client()
    client.ensure_connected()
    orders = client.closed_orders(days=days, symbol=symbol)
    rows = [row for row in (_order_stats(o) for o in orders) if row]
    if not rows:
        return {"trades": 0, "message": "no closed trades in period"}

    wins = [r for r in rows if r["profit"] > 0]
    losses = [r for r in rows if r["profit"] < 0]
    gross_win = sum(r["profit"] for r in wins)
    gross_loss = abs(sum(r["profit"] for r in losses))
    monthly: dict[str, float] = defaultdict(float)
    daily: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["close_time"]:
            close_dt = datetime.fromisoformat(row["close_time"])
            monthly[close_dt.strftime("%Y-%m")] += row["profit"]
            daily[close_dt.strftime("%Y-%m-%d")] += row["profit"]

    rr_values = [r["rr"] for r in rows if r.get("rr")]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0.0,
        "net_profit": round(sum(r["profit"] for r in rows), 2),
        "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "average_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "average_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else None,
        "profit_factor": round(gross_win / gross_loss, 2)
        if gross_loss
        else (99.99 if gross_win else 0.0),
        "average_pips": round(sum(r["pips"] for r in rows) / len(rows), 1),
        "monthly_profits": dict(sorted(monthly.items())),
        "daily_profits": dict(sorted(daily.items())),
        "best_trade": max(rows, key=lambda r: r["profit"]),
        "worst_trade": min(rows, key=lambda r: r["profit"]),
    }


def register(mcp) -> None:
    @mcp.tool(
        name="trade_history",
        description="Closed trades over the last N days, optionally filtered by symbol.",
    )
    def tool_trade_history(days: int = 30, symbol: str = "") -> dict:
        found = trade_history(days, symbol)
        return {"trades": found, "count": len(found)}

    @mcp.tool(
        name="history_statistics",
        description="Win rate, average win/loss, average RR, profit factor, monthly and daily profits.",
    )
    def tool_history_statistics(days: int = 30, symbol: str = "") -> dict:
        return history_statistics(days, symbol)
