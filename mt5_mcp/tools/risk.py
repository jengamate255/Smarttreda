"""Risk manager: lot sizing, daily/weekly loss limits, exposure, warnings."""

from __future__ import annotations

import time

from client import get_client
from common import pip_size, pip_value_per_lot
from config import get_settings


def _day_start_epoch() -> int:
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))


def _week_start_epoch() -> int:
    now = time.localtime()
    start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(start - now.tm_wday * 86400)


def _closed_pnl_since(client, epoch: int) -> float:
    deals = client.deals_since(epoch)
    return sum(
        float(d.get("profit", 0.0) or 0.0) for d in deals if d.get("type") in (0, 1)
    )


def risk_report() -> dict:
    client = get_client()
    client.ensure_connected()
    settings = get_settings().risk
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)
    positions = client.positions()
    warnings: list[str] = []

    floating = sum(float(p.get("profit", 0.0) or 0.0) for p in positions)
    daily_closed = _closed_pnl_since(client, _day_start_epoch())
    weekly_closed = _closed_pnl_since(client, _week_start_epoch())
    daily_pnl = daily_closed + floating

    open_risk = 0.0
    total_exposure = 0.0
    for pos in positions:
        symbol = str(pos.get("symbol", ""))
        try:
            info = client.symbol_info(symbol)
            pip = pip_size(info)
        except Exception:
            continue
        sl = float(pos.get("sl", 0.0) or 0.0)
        volume = float(pos.get("volume", 0.0) or 0.0)
        open_price = float(pos.get("price_open", 0.0) or 0.0)
        total_exposure += volume * float(info.get("contract_size", 100_000) or 100_000)
        if sl:
            sl_dist = abs(open_price - sl)
            risk_amount = (
                sl_dist / pip * pip_value_per_lot(info) * volume if pip else 0.0
            )
            open_risk += risk_amount

    max_daily = equity * settings.max_daily_loss_percent / 100.0
    max_weekly = equity * settings.max_weekly_loss_percent / 100.0
    max_open = equity * settings.max_open_risk_percent / 100.0

    if daily_pnl <= -max_daily:
        warnings.append(
            f"DAILY LOSS LIMIT hit: {daily_pnl:.2f} (limit {max_daily:.2f}) - stop trading today"
        )
    elif daily_pnl <= -max_daily * 0.8:
        warnings.append(
            f"daily loss approaching limit: {daily_pnl:.2f} of {max_daily:.2f}"
        )
    if weekly_closed + floating <= -max_weekly:
        warnings.append(
            f"WEEKLY LOSS LIMIT hit: {weekly_closed + floating:.2f} (limit {max_weekly:.2f})"
        )
    if open_risk > max_open:
        warnings.append(
            f"open risk {open_risk:.2f} exceeds max {max_open:.2f} - reduce positions"
        )

    return {
        "equity": equity,
        "balance": float(account.get("balance", 0.0) or 0.0),
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit": round(max_daily, 2),
        "daily_loss_percent_used": round(max(0.0, -daily_pnl) / max_daily * 100.0, 1)
        if max_daily
        else 0.0,
        "weekly_pnl": round(weekly_closed + floating, 2),
        "weekly_limit": round(max_weekly, 2),
        "weekly_loss_percent_used": round(
            max(0.0, -(weekly_closed + floating)) / max_weekly * 100.0, 1
        )
        if max_weekly
        else 0.0,
        "open_risk": round(open_risk, 2),
        "max_open_risk": round(max_open, 2),
        "open_risk_percent_used": round(open_risk / max_open * 100.0, 1)
        if max_open
        else 0.0,
        "open_positions": len(positions),
        "max_positions": settings.max_positions,
        "total_exposure": round(total_exposure, 2),
        "warnings": warnings,
        "trading_allowed": not any("hit" in w for w in warnings),
    }


def assess_trade(
    symbol: str, sl_pips: float, risk_percent: float | None = None
) -> dict:
    client = get_client()
    client.ensure_connected()
    settings = get_settings().risk
    risk_percent = (
        risk_percent if risk_percent is not None else settings.default_risk_percent
    )
    info = client.symbol_info(symbol)
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)
    pip_value = pip_value_per_lot(info)
    risk_amount = equity * risk_percent / 100.0
    from tools.trades import calc_lots

    lots_info = calc_lots(symbol, sl_pips, risk_percent)
    return {
        **lots_info,
        "risk_check": "ok"
        if risk_percent <= settings.max_risk_percent
        else "exceeds max",
        "max_risk_percent": settings.max_risk_percent,
        "min_rr": settings.min_risk_reward,
        "pip_value_per_lot": round(pip_value, 6),
        "equity": equity,
        "risk_amount": round(risk_amount, 2),
    }


def register(mcp) -> None:
    @mcp.tool(
        name="risk_report",
        description="Daily/weekly loss usage, open risk, exposure, position count and warnings. Trading is blocked when limits are hit.",
    )
    def tool_risk_report() -> dict:
        return risk_report()

    @mcp.tool(
        name="assess_trade",
        description="Assess a proposed trade: lot size, risk amount, margin, risk limits.",
    )
    def tool_assess_trade(
        symbol: str, sl_pips: float, risk_percent: float | None = None
    ) -> dict:
        return assess_trade(symbol, sl_pips, risk_percent)
