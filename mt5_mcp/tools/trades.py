"""Trade execution with full safety preflight.

Every order passes checks before sending:

* symbol exists
* market is open
* SL/TP valid side, beyond broker stop level
* lot size within broker min/max/step
* spread within configured limit
* free margin covers the order
* max open positions not exceeded
"""

from __future__ import annotations

import os
from typing import Any

from mt5_mcp.client import get_client
from mt5_mcp.compliance import is_synthetic_symbol
from mt5_mcp.common import (
    ORDER_TIME_DAY,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_SLTP,
    TradeResult,
    pip_size,
    pip_value_per_lot,
    round_to_step,
)
from mt5_mcp.config import get_settings, rejections_enabled

MAGIC = int(os.getenv("MT5_MCP_MAGIC", "20260601"))

# Slippage allowance, in points, sent as ``deviation`` on market orders. The
# broker's ``trade_stops_level`` is the *minimum stop distance*, not a slippage
# budget -- for XAUUSD it is 0, and sending deviation=0 means "fill at this
# exact price or reject", which surfaces as retcode 10004 (requote) whenever
# price ticks between our quote and the broker's fill.
MIN_DEVIATION_POINTS = int(os.getenv("MT5_MCP_MIN_DEVIATION", "20"))


def _deviation(info: dict) -> int:
    """Slippage tolerance for a market order on ``info``."""
    return max(MIN_DEVIATION_POINTS, int(info.get("trade_stops_level", 0) or 0))


class OrderRejected(Exception):
    pass


def _preflight(
    symbol: str,
    entry: float,
    side: str,
    volume: float,
    sl: float | None,
    tp: float | None,
) -> tuple[dict, list[str]]:
    client = get_client()
    checks: list[str] = []
    settings = get_settings().risk

    info = client.symbol_info(symbol)
    checks.append(f"symbol '{symbol}' exists")

    open_ok, reason = client.market_open(symbol)
    if not open_ok:
        raise OrderRejected(f"market closed: {reason}")
    checks.append("market open")

    point = float(info.get("point", 0.0))
    digits = int(info.get("digits", 5))
    stop_level = float(info.get("trade_stops_level", 0.0) or 0.0)
    pip = pip_size(info)

    tick = client.tick(symbol)
    tick_bid = float(tick.get("bid", 0.0))
    bid, ask = tick_bid, float(tick.get("ask", tick_bid))
    spread_points = (ask - bid) / point if point else 0
    mid = (ask + bid) / 2 if (ask + bid) else 0.0
    relative_spread = (ask - bid) / mid if mid else 0.0
    if is_synthetic_symbol(symbol):
        spread_ok = (
            not settings.max_spread_relative
            or relative_spread <= settings.max_spread_relative
        )
    elif settings.max_spread_points:
        spread_ok = spread_points <= settings.max_spread_points
    else:
        spread_ok = (
            not settings.max_spread_relative
            or relative_spread <= settings.max_spread_relative
        )
    if not spread_ok:
        msg = (
            f"spread {spread_points:.0f} points "
            f"({relative_spread * 100:.3f}%) exceeds limit "
            f"{settings.max_spread_points:.0f} points"
        )
        if rejections_enabled():
            raise OrderRejected(msg)
        # Forward-test log mode: record and send anyway (broker decides).
        checks.append(f"WARNING: {msg} (log-mode, not enforced)")
    else:
        checks.append(f"spread {spread_points:.0f} points within limit")

    if side == "BUY":
        if ask > entry * 1.0000001:
            entry = ask
        if sl is not None and sl >= entry - stop_level * point:
            raise OrderRejected(
                f"SL too close to entry (stop level {stop_level:.0f} points)"
            )
        if tp is not None and tp <= entry + stop_level * point:
            raise OrderRejected(
                f"TP too close to entry (stop level {stop_level:.0f} points)"
            )
    else:
        if bid < entry * 0.9999999:
            entry = bid
        if sl is not None and sl <= entry + stop_level * point:
            raise OrderRejected(
                f"SL too close to entry (stop level {stop_level:.0f} points)"
            )
        if tp is not None and tp >= entry - stop_level * point:
            raise OrderRejected(
                f"TP too close to entry (stop level {stop_level:.0f} points)"
            )
    checks.append("SL/TP validated against stop level")

    if sl is not None and tp is not None:
        sl_dist = abs(entry - sl) / pip if pip else 0
        tp_dist = abs(tp - entry) / pip if pip else 0
        if sl_dist > 0 and tp_dist / sl_dist < settings.min_risk_reward:
            checks.append(
                f"WARNING: RR {tp_dist / sl_dist:.2f} below minimum {settings.min_risk_reward:.1f}"
            )

    volume_min = float(info.get("volume_min", settings.min_volume or 0.01) or 0.01)
    volume_max = float(info.get("volume_max", settings.max_volume or 100.0) or 100.0)
    volume_step = float(info.get("volume_step", 0.01) or 0.01)
    if volume < volume_min or volume > volume_max:
        raise OrderRejected(
            f"lot {volume} outside broker range [{volume_min}, {volume_max}]"
        )
    volume = round_to_step(volume, volume_step, floor=volume_min, ceil=volume_max)
    checks.append(f"lot {volume} within broker range")

    positions = client.positions()
    if settings.max_positions and len(positions) >= settings.max_positions:
        if not is_synthetic_symbol(symbol):
            raise OrderRejected(f"max open positions ({settings.max_positions}) reached")
        else:
            checks.append(f"position count {len(positions)} (Deriv synthetic, limit bypassed)")
    else:
        checks.append(f"position count {len(positions)} < {settings.max_positions}")

    action = TRADE_ACTION_DEAL
    try:
        margin = client.order_calc_margin(action, symbol, volume, entry)
        account = client.account_info()
        free = float(account.get("margin_free", 0.0) or 0.0)
        if margin > free:
            raise OrderRejected(
                f"insufficient free margin: need {margin:.2f}, free {free:.2f}"
            )
        checks.append(f"margin {margin:.2f} within free margin {free:.2f}")
    except OrderRejected:
        raise
    except Exception:
        checks.append(
            "WARNING: margin verification skipped (order_calc_margin unavailable)"
        )

    return {
        "info": info,
        "point": point,
        "digits": digits,
        "pip": pip,
        "entry": entry,
        "volume": volume,
    }, checks


def _send(request: dict[str, Any]) -> dict[str, Any]:
    client = get_client()
    result = client.order_send(request)
    return result


def open_market(
    symbol: str,
    side: str,
    lots: float,
    sl: float | None = None,
    tp: float | None = None,
    comment: str = "",
    confidence: float | None = None,
) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        from mt5_mcp.compliance import gate

        verdict = gate(
            symbol,
            side.upper(),
            lots,
            sl_price=sl,
            confidence=confidence,
        )
        if not verdict["allowed"]:
            return TradeResult(
                accepted=False,
                error=f"compliance: {verdict['violations'][0]}",
                checks=[],
            )
        tick = client.tick(symbol)
        entry = (
            float(tick.get("ask", 0.0))
            if side == "BUY"
            else float(tick.get("bid", 0.0))
        )
        pre, checks = _preflight(symbol, entry, side, lots, sl, tp)
        order_type = ORDER_TYPE_BUY if side == "BUY" else ORDER_TYPE_SELL
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pre["volume"],
            "type": order_type,
            "price": pre["entry"],
            "sl": sl if sl is not None else 0.0,
            "tp": tp if tp is not None else 0.0,
            "deviation": _deviation(pre.get("info", {})),
            "magic": MAGIC,
            "comment": comment[:30] or "mt5-mcp",
            "type_time": ORDER_TIME_DAY,
        }
        result = _send(request)
        return TradeResult(
            accepted=True,
            ticket=int(result.get("order", 0) or result.get("deal", 0) or 0),
            retcode=int(result.get("retcode", 0)),
            checks=checks,
            request=request,
            details={
                "comment": result.get("comment", ""),
                "volume": pre["volume"],
                "price": pre["entry"],
            },
        )
    except Exception as exc:
        return TradeResult(
            accepted=False,
            error=str(exc),
            checks=checks if "checks" in locals() else [],
        )


PENDING_TYPES = {
    "buy_limit": ORDER_TYPE_BUY_LIMIT,
    "sell_limit": ORDER_TYPE_SELL_LIMIT,
    "buy_stop": ORDER_TYPE_BUY_STOP,
    "sell_stop": ORDER_TYPE_SELL_STOP,
}


def pending_order(
    symbol: str,
    order_type: str,
    lots: float,
    price: float,
    sl: float | None = None,
    tp: float | None = None,
    comment: str = "",
) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        kind = PENDING_TYPES.get(order_type.lower())
        if kind is None:
            return TradeResult(
                accepted=False,
                error=f"invalid pending type '{order_type}' (use buy_limit, sell_limit, buy_stop, sell_stop)",
            )
        side = "BUY" if kind in (ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_BUY_STOP) else "SELL"
        info = client.symbol_info(symbol)
        point = float(info.get("point", 0.0))
        tick = client.tick(symbol)
        tick_bid = float(tick.get("bid", 0.0))
        bid, ask = tick_bid, float(tick.get("ask", tick_bid))
        if kind == ORDER_TYPE_BUY_LIMIT and price >= ask:
            return TradeResult(
                accepted=False,
                error=f"{order_type} must be placed below current ask ({ask:.5f})",
            )
        if kind == ORDER_TYPE_SELL_LIMIT and price <= bid:
            return TradeResult(
                accepted=False,
                error=f"{order_type} must be placed above current bid ({bid:.5f})",
            )
        if kind == ORDER_TYPE_SELL_STOP and price >= bid:
            return TradeResult(
                accepted=False,
                error=f"{order_type} must be placed below current bid ({bid:.5f})",
            )
        if kind == ORDER_TYPE_BUY_STOP and price <= ask:
            return TradeResult(
                accepted=False,
                error=f"{order_type} must be placed above current ask ({ask:.5f})",
            )
        from mt5_mcp.compliance import gate

        verdict = gate(symbol, side, lots, sl_price=sl, entry_price=price)
        if not verdict["allowed"]:
            return TradeResult(
                accepted=False,
                error=f"compliance: {verdict['violations'][0]}",
                checks=[],
            )
        pre, checks = _preflight(symbol, price, side, lots, sl, tp)
        request = {
            "action": TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": pre["volume"],
            "type": kind,
            "price": round(price, int(info.get("digits", 5))),
            "sl": sl if sl is not None else 0.0,
            "tp": tp if tp is not None else 0.0,
            "magic": MAGIC,
            "comment": comment[:30] or "mt5-mcp",
            "type_time": ORDER_TIME_DAY,
        }
        result = _send(request)
        return TradeResult(
            accepted=True,
            ticket=int(result.get("order", 0) or 0),
            retcode=int(result.get("retcode", 0)),
            checks=checks,
            request=request,
            details={"comment": result.get("comment", "")},
        )
    except Exception as exc:
        return TradeResult(
            accepted=False,
            error=str(exc),
            checks=checks if "checks" in locals() else [],
        )


def _find_position(ticket: int) -> dict:
    client = get_client()
    positions = client.positions()
    for pos in positions:
        if int(pos.get("ticket", 0)) == ticket:
            return pos
    raise OrderRejected(f"position {ticket} not found")


def close_position(ticket: int, volume: float | None = None) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        pos = _find_position(ticket)
        symbol = str(pos.get("symbol", ""))
        volume = volume or float(pos.get("volume", 0.0))
        info = client.symbol_info(symbol)
        tick = client.tick(symbol)
        tick_bid = float(tick.get("bid", 0.0))
        bid, ask = tick_bid, float(tick.get("ask", tick_bid))
        close_type = (
            ORDER_TYPE_SELL
            if int(pos.get("type", 0)) == ORDER_TYPE_BUY
            else ORDER_TYPE_BUY
        )
        price = bid if close_type == ORDER_TYPE_SELL else ask
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": round(volume, 2),
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": _deviation(info),
            "magic": MAGIC,
            "comment": "close",
            "type_time": ORDER_TIME_DAY,
        }
        result = _send(request)
        return TradeResult(
            accepted=True,
            ticket=ticket,
            retcode=int(result.get("retcode", 0)),
            request=request,
            details={"comment": result.get("comment", "")},
        )
    except Exception as exc:
        return TradeResult(accepted=False, error=str(exc))


def partial_close(ticket: int, volume: float) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        pos = _find_position(ticket)
        open_volume = float(pos.get("volume", 0.0))
        if volume >= open_volume:
            return close_position(ticket)
        return close_position(ticket, volume=volume)
    except Exception as exc:
        return TradeResult(accepted=False, error=str(exc))


def modify_sl_tp(
    ticket: int, sl: float | None = None, tp: float | None = None
) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        pos = _find_position(ticket)
        symbol = str(pos.get("symbol", ""))
        if sl is None and tp is None:
            return TradeResult(accepted=False, error="provide at least one of sl/tp")
        info = client.symbol_info(symbol)
        point = float(info.get("point", 0.0))
        stop_level = float(info.get("trade_stops_level", 0.0) or 0.0)
        sl = sl if sl is not None else float(pos.get("sl", 0.0)) or 0.0
        tp = tp if tp is not None else float(pos.get("tp", 0.0)) or 0.0
        is_buy = int(pos.get("type", 0)) == ORDER_TYPE_BUY
        tick = client.tick(symbol)
        tick_bid = float(tick.get("bid", 0.0))
        tick_ask = float(tick.get("ask", tick_bid))
        if is_buy and sl and sl >= tick_bid - stop_level * point:
            return TradeResult(
                accepted=False,
                error=f"SL too close to current price (stop level {stop_level:.0f} points)",
            )
        if not is_buy and sl and sl <= tick_ask + stop_level * point:
            return TradeResult(
                accepted=False,
                error=f"SL too close to current price (stop level {stop_level:.0f} points)",
            )
        request = {
            "action": TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": sl or 0.0,
            "tp": tp or 0.0,
            "magic": MAGIC,
            "type_time": ORDER_TIME_DAY,
        }
        result = _send(request)
        return TradeResult(
            accepted=True,
            ticket=ticket,
            retcode=int(result.get("retcode", 0)),
            request=request,
        )
    except Exception as exc:
        return TradeResult(accepted=False, error=str(exc))


def trail_stop(ticket: int, trail_pips: float) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        pos = _find_position(ticket)
        symbol = str(pos.get("symbol", ""))
        info = client.symbol_info(symbol)
        pip = pip_size(info)
        tick = client.tick(symbol)
        tick_bid = float(tick.get("bid", 0.0))
        bid, ask = tick_bid, float(tick.get("ask", tick_bid))
        is_buy = int(pos.get("type", 0)) == ORDER_TYPE_BUY
        current_sl = float(pos.get("sl", 0.0)) or None
        trail = trail_pips * pip
        if is_buy:
            candidate = bid - trail
            if current_sl is not None and candidate <= current_sl:
                return TradeResult(
                    accepted=False,
                    error=f"trail would move SL backwards (keep {current_sl:.5f})",
                )
            return modify_sl_tp(ticket, sl=candidate)
        candidate = ask + trail
        if current_sl is not None and candidate >= current_sl:
            return TradeResult(
                accepted=False,
                error=f"trail would move SL backwards (keep {current_sl:.5f})",
            )
        return modify_sl_tp(ticket, sl=candidate)
    except Exception as exc:
        return TradeResult(accepted=False, error=str(exc))


def break_even(ticket: int, offset_pips: float = 0.0) -> TradeResult:
    client = get_client()
    client.ensure_connected()
    try:
        pos = _find_position(ticket)
        symbol = str(pos.get("symbol", ""))
        info = client.symbol_info(symbol)
        pip = pip_size(info)
        tick = client.tick(symbol)
        tick_bid = float(tick.get("bid", 0.0))
        bid, ask = tick_bid, float(tick.get("ask", tick_bid))
        entry = float(pos.get("price_open", 0.0))
        is_buy = int(pos.get("type", 0)) == ORDER_TYPE_BUY
        offset = offset_pips * pip
        if is_buy:
            if bid <= entry + offset:
                return TradeResult(
                    accepted=False, error="price not yet beyond entry + offset"
                )
            return modify_sl_tp(
                ticket, sl=round(entry + offset, int(info.get("digits", 5)))
            )
        if ask >= entry - offset:
            return TradeResult(
                accepted=False, error="price not yet below entry - offset"
            )
        return modify_sl_tp(
            ticket, sl=round(entry - offset, int(info.get("digits", 5)))
        )
    except Exception as exc:
        return TradeResult(accepted=False, error=str(exc))


def calc_lots(symbol: str, sl_pips: float, risk_percent: float | None = None) -> dict:
    client = get_client()
    client.ensure_connected()
    settings = get_settings().risk
    risk_percent = (
        risk_percent if risk_percent is not None else settings.default_risk_percent
    )
    if risk_percent > settings.max_risk_percent:
        return {
            "error": f"risk {risk_percent}% exceeds max {settings.max_risk_percent}%"
        }
    info = client.symbol_info(symbol)
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)
    pip_value = pip_value_per_lot(info)
    if sl_pips <= 0 or pip_value <= 0:
        return {"error": "invalid sl_pips or pip value"}
    risk_amount = equity * risk_percent / 100.0
    lots = risk_amount / (sl_pips * pip_value)
    volume_min = float(info.get("volume_min", settings.min_volume or 0.01) or 0.01)
    volume_max = float(info.get("volume_max", settings.max_volume or 100.0) or 100.0)
    volume_step = float(info.get("volume_step", 0.01) or 0.01)
    lots = round_to_step(lots, volume_step, floor=volume_min, ceil=volume_max)
    price = float(client.tick(symbol).get("bid", 0.0) or equity / 100.0)
    margin = client.order_calc_margin(TRADE_ACTION_DEAL, symbol, lots, price)
    return {
        "symbol": symbol,
        "lots": lots,
        "risk_percent": risk_percent,
        "risk_amount": round(risk_amount, 2),
        "sl_pips": sl_pips,
        "equity": equity,
        "pip_value_per_lot": round(pip_value, 6),
        "estimated_margin": round(margin, 2),
        "warnings": []
        if lots >= volume_min
        else ["lot size below broker minimum; use a smaller risk % or wider SL"],
    }


def register(mcp) -> None:
    @mcp.tool(
        name="open_trade",
        description="Open a market BUY or SELL trade with optional SL/TP. All safety checks are enforced.",
    )
    def tool_open_trade(
        symbol: str,
        side: str,
        lots: float,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "",
    ) -> TradeResult:
        return open_market(symbol, side.upper(), lots, sl, tp, comment)

    @mcp.tool(
        name="pending_order",
        description="Place a pending order: buy_limit, sell_limit, buy_stop, sell_stop.",
    )
    def tool_pending_order(
        symbol: str,
        order_type: str,
        lots: float,
        price: float,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "",
    ) -> TradeResult:
        return pending_order(symbol, order_type, lots, price, sl, tp, comment)

    @mcp.tool(
        name="close_position",
        description="Close a position by ticket (full or partial close with volume).",
    )
    def tool_close_position(ticket: int, volume: float | None = None) -> TradeResult:
        return close_position(ticket, volume)

    @mcp.tool(
        name="modify_sl_tp",
        description="Modify stop loss and/or take profit of an open position.",
    )
    def tool_modify_sl_tp(
        ticket: int, sl: float | None = None, tp: float | None = None
    ) -> TradeResult:
        return modify_sl_tp(ticket, sl, tp)

    @mcp.tool(
        name="trail_stop",
        description="Move the stop loss to trail the price by a number of pips.",
    )
    def tool_trail_stop(ticket: int, trail_pips: float) -> TradeResult:
        return trail_stop(ticket, trail_pips)

    @mcp.tool(
        name="break_even",
        description="Move the stop loss to entry plus an offset once the trade is profitable.",
    )
    def tool_break_even(ticket: int, offset_pips: float = 0.0) -> TradeResult:
        return break_even(ticket, offset_pips)

    @mcp.tool(
        name="calc_lots",
        description="Risk-based lot sizing: how many lots for a given SL distance and risk %.",
    )
    def tool_calc_lots(
        symbol: str, sl_pips: float, risk_percent: float | None = None
    ) -> dict:
        return calc_lots(symbol, sl_pips, risk_percent)
