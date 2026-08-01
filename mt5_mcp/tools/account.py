"""Account tools: balance, equity, margin, daily P/L, drawdown."""

from __future__ import annotations

import time

from pydantic import BaseModel

from client import get_client


class AccountSnapshot(BaseModel):
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    daily_pnl: float = 0.0
    floating_profit: float = 0.0
    drawdown: float = 0.0
    drawdown_percent: float = 0.0
    currency: str = ""
    leverage: int = 0
    login: int = 0
    server: str = ""


def _start_of_day_epoch() -> int:
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))


def account_info() -> AccountSnapshot:
    client = get_client()
    client.ensure_connected()
    info = client.account_info()
    positions = client.positions()
    floating = sum(float(p.get("profit", 0.0) or 0.0) for p in positions)

    today = _start_of_day_epoch()
    closed_today = client.deals_since(today)
    trade_deals = [d for d in closed_today if d.get("type") in (0, 1)]
    closed_pnl = sum(float(d.get("profit", 0.0) or 0.0) for d in trade_deals)

    balance = float(info.get("balance", 0.0) or 0.0)
    equity = float(info.get("equity", 0.0) or 0.0)
    drawdown = max(0.0, balance - equity)
    drawdown_pct = drawdown / balance * 100.0 if balance else 0.0

    return AccountSnapshot(
        balance=balance,
        equity=equity,
        margin=float(info.get("margin", 0.0) or 0.0),
        margin_free=float(info.get("margin_free", 0.0) or 0.0),
        margin_level=float(info.get("margin_level", 0.0) or 0.0),
        daily_pnl=round(closed_pnl + floating, 2),
        floating_profit=round(floating, 2),
        drawdown=round(drawdown, 2),
        drawdown_percent=round(drawdown_pct, 2),
        currency=str(info.get("currency", "")),
        leverage=int(info.get("leverage", 0) or 0),
        login=int(info.get("login", 0) or 0),
        server=str(info.get("server", "")),
    )


def daily_pnl() -> dict:
    client = get_client()
    client.ensure_connected()
    positions = client.positions()
    floating = sum(float(p.get("profit", 0.0) or 0.0) for p in positions)
    trade_deals = [
        d for d in client.deals_since(_start_of_day_epoch()) if d.get("type") in (0, 1)
    ]
    closed = sum(float(d.get("profit", 0.0) or 0.0) for d in trade_deals)
    return {
        "closed_today": round(closed, 2),
        "floating": round(floating, 2),
        "total": round(closed + floating, 2),
        "trade_count": len(trade_deals),
    }


def register(mcp) -> None:
    @mcp.tool(
        name="account_info",
        description="Account balance, equity, margin, margin level, daily P/L, drawdown, currency and leverage.",
    )
    def tool_account_info() -> AccountSnapshot:
        return account_info()

    @mcp.tool(
        name="account_daily_pnl", description="Today's closed and floating profit/loss."
    )
    def tool_account_daily_pnl() -> dict:
        return daily_pnl()
