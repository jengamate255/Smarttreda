"""Position manager: open positions with floating P/L, risk, pips, duration, RR, health."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from client import get_client
from common import ORDER_TYPE_BUY, PositionView, pip_size, pip_value_per_lot


def _health(progress: float, risk_used: float) -> tuple[float, str]:
    score = 50.0 * min(max(progress, 0.0), 1.0) + 50.0 * (
        1.0 - min(max(risk_used, 0.0), 1.0)
    )
    score = round(score, 1)
    if score >= 70:
        return score, "healthy"
    if score >= 40:
        return score, "cautious"
    return score, "danger"


def open_positions(symbol: str = "") -> list[PositionView]:
    client = get_client()
    client.ensure_connected()
    positions = client.positions(symbol)
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)
    views: list[PositionView] = []
    now_epoch = time.time()

    for pos in positions:
        ticket = int(pos.get("ticket", 0))
        sym = str(pos.get("symbol", ""))
        is_buy = int(pos.get("type", 0)) == ORDER_TYPE_BUY
        info = client.symbol_info(sym)
        tick = client.tick(sym)
        price = float(tick.get("bid", 0.0)) if is_buy else float(tick.get("ask", 0.0))
        open_price = float(pos.get("price_open", 0.0))
        sl = float(pos.get("sl", 0.0)) or None
        tp = float(pos.get("tp", 0.0)) or None
        volume = float(pos.get("volume", 0.0))
        pip = pip_size(info)
        pips = (price - open_price) / pip if is_buy else (open_price - price) / pip

        risk_amount = None
        risk_percent = None
        rr = None
        if sl:
            sl_dist = abs(open_price - sl)
            risk_amount = (
                sl_dist / pip * pip_value_per_lot(info) * volume if pip else 0.0
            )
            risk_percent = risk_amount / equity * 100.0 if equity else None
            if tp:
                tp_dist = abs(tp - open_price)
                rr = tp_dist / sl_dist if sl_dist else None

        progress = 0.0
        risk_used = 0.0
        if tp and abs(tp - open_price) > 0:
            progress = (
                (price - open_price) / (tp - open_price)
                if is_buy
                else (open_price - price) / (open_price - tp)
            )
        if sl and abs(open_price - sl) > 0:
            risk_used = (
                (price - open_price) / (sl - open_price)
                if is_buy
                else (open_price - price) / (open_price - sl)
            )
        if sl and tp:
            pass
        health_score, health_status = _health(progress, risk_used if sl else 0.5)

        duration = 0
        open_dt = None
        if pos.get("time"):
            duration = max(0, int(now_epoch - float(pos.get("time", 0) or 0)))
            open_dt = datetime.fromtimestamp(float(pos.get("time")), tz=timezone.utc)

        views.append(
            PositionView(
                ticket=ticket,
                symbol=sym,
                side="BUY" if is_buy else "SELL",
                volume=volume,
                open_price=open_price,
                sl=sl,
                tp=tp,
                current_price=price,
                floating_profit=round(float(pos.get("profit", 0.0) or 0.0), 2),
                pips=round(pips, 1),
                risk_amount=round(risk_amount, 2) if risk_amount is not None else None,
                risk_percent=round(risk_percent, 2)
                if risk_percent is not None
                else None,
                rr=round(rr, 2) if rr is not None else None,
                duration_seconds=duration,
                health_score=health_score,
                health_status=health_status,
                open_time=open_dt,
                comment=str(pos.get("comment", "")),
            )
        )
    views.sort(key=lambda v: -v.floating_profit)
    return views


def position_detail(ticket: int) -> PositionView | None:
    for view in open_positions():
        if view.ticket == ticket:
            return view
    return None


def register(mcp) -> None:
    @mcp.tool(
        name="open_positions",
        description="All open positions with floating profit, pips, risk, duration, R:R and health rating.",
    )
    def tool_open_positions(symbol: str = "") -> dict:
        found = open_positions(symbol)
        return {"positions": found, "count": len(found)}
