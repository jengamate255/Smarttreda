"""Market tools: symbols, search, ticks, spreads, sessions, open status."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from mt5_mcp.client import get_client
from mt5_mcp.common import pip_size, pip_value_per_lot
from mt5_mcp.config import get_settings


class TickInfo(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread_points: int
    point: float
    digits: int
    pip: float
    pip_value_per_lot: float
    tick_value: float
    last: float = 0.0
    volume: int = 0


def _session_name(utc_hour: int) -> str:
    if 22 <= utc_hour or utc_hour < 6:
        return "Sydney/Tokyo"
    if 6 <= utc_hour < 13:
        return "London"
    return "New York"


def market_symbols(group: str = "", max_results: int = 200) -> list[str]:
    client = get_client()
    client.ensure_connected()
    symbols = client.symbols(group)
    return symbols[:max_results]


def market_search(query: str, max_results: int = 50) -> list[str]:
    client = get_client()
    client.ensure_connected()
    symbols = client.symbols()
    query_upper = query.upper()
    matches = [s for s in symbols if query_upper in s.upper()]
    return matches[:max_results]


def market_tick(symbol: str) -> TickInfo:
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(symbol)
    tick = client.tick(symbol)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))
    point = float(info.get("point", 0.00001))
    digits = int(info.get("digits", 5))
    return TickInfo(
        symbol=symbol,
        bid=bid,
        ask=ask,
        spread_points=round((ask - bid) / point) if point else 0,
        point=point,
        digits=digits,
        pip=pip_size(info),
        pip_value_per_lot=round(pip_value_per_lot(info), 6),
        tick_value=float(info.get("tick_value", 0.0)),
        last=float(tick.get("last", bid)),
        volume=int(tick.get("volume", 0) or 0),
    )


def market_status(symbol: str) -> dict:
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(symbol)
    open_ok, reason = client.market_open(symbol)
    tick = market_tick(symbol)
    utc_hour = datetime.now(timezone.utc).hour
    spread_limit = get_settings().risk.max_spread_points
    spread_relative = (
        (tick.ask - tick.bid) / ((tick.ask + tick.bid) / 2)
        if tick.ask + tick.bid
        else 0.0
    )
    relative_limit = get_settings().risk.max_spread_relative
    spread_ok = (
        not spread_limit or tick.spread_points <= spread_limit
    ) or (relative_limit and spread_relative <= relative_limit)
    return {
        "symbol": symbol,
        "market_open": open_ok,
        "reason": reason,
        "session": _session_name(utc_hour),
        "utc_time": datetime.now(timezone.utc).isoformat(),
        "spread_points": tick.spread_points,
        "max_spread_points": spread_limit,
        "spread_relative": round(spread_relative * 100, 4),
        "max_spread_relative": relative_limit,
        "spread_ok": spread_ok,
        "trade_mode": info.get("trade_mode", "?"),
        "digits": info.get("digits"),
        "point": info.get("point"),
        "tick_size": info.get("tick_size"),
        "tick_value": info.get("tick_value"),
        "volume_min": info.get("volume_min"),
        "volume_max": info.get("volume_max"),
        "volume_step": info.get("volume_step"),
    }


def register(mcp) -> None:
    @mcp.tool(
        name="market_symbols",
        description="List available symbols, optionally filtered by group.",
    )
    def tool_market_symbols(group: str = "", max_results: int = 200) -> dict:
        found = market_symbols(group, max_results)
        return {"symbols": found, "count": len(found)}

    @mcp.tool(
        name="market_search",
        description="Search symbols by name fragment (e.g. 'EUR', 'XAU').",
    )
    def tool_market_search(query: str, max_results: int = 50) -> dict:
        found = market_search(query, max_results)
        return {"symbols": found, "count": len(found)}

    @mcp.tool(
        name="market_tick",
        description="Current bid/ask, spread, point size, pip value and tick value.",
    )
    def tool_market_tick(symbol: str) -> TickInfo:
        return market_tick(symbol)

    @mcp.tool(
        name="market_status",
        description="Market open status, trading session, spread health and symbol details.",
    )
    def tool_market_status(symbol: str) -> dict:
        return market_status(symbol)
