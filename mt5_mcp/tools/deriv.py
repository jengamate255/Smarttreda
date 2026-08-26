"""Deriv market tools: symbols, ticks, candles, trading times.

Note: With the default shared app_id (1089), only synthetic indices are available.
Forex and other markets require a registered Deriv app_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from mt5_mcp.deriv_client import get_deriv_client
from mt5_mcp.config import get_settings
from mt5_mcp.common import BOOM_CRASH_SYMBOLS


# Known working symbols with default app_id (synthetic indices)
DEFAULT_SYMBOLS = [
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    *BOOM_CRASH_SYMBOLS,
]


class DerivTickInfo(BaseModel):
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
    quote_time: datetime | None = None


class DerivSymbolInfo(BaseModel):
    symbol: str
    display_name: str
    market: str
    market_display_name: str
    submarket: str
    submarket_display_name: str
    pip: float
    tick_size: float
    tick_value: float
    min_volume: float
    max_volume: float
    volume_step: float
    margin_rate: float
    swap_long: float
    swap_short: float
    trading_sessions: list[dict[str, Any]]
    is_open: bool


def _pip_size_from_tick(tick_size: float) -> float:
    """Convert tick size to pip size."""
    if tick_size <= 0.0001:
        return tick_size * 10  # 5-digit forex
    return tick_size  # indices, commodities


def deriv_symbols(product_type: str = "basic", max_results: int = 200) -> list[str]:
    """Get list of available symbols from Deriv."""
    # With shared app_id, return known working synthetic indices
    return DEFAULT_SYMBOLS[:max_results]


def deriv_search(query: str, max_results: int = 50) -> list[str]:
    """Search Deriv symbols by name fragment."""
    all_symbols = deriv_symbols()
    query_upper = query.upper()
    matches = [s for s in all_symbols if query_upper in s.upper()]
    return matches[:max_results]


def deriv_tick(symbol: str) -> DerivTickInfo:
    """Get current tick info for a Deriv symbol using ticks_history (latest)."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        # Use ticks_history with count=1 to get latest price
        data = client.ticks_history(symbol, count=1, end="latest", style="ticks")
        history = data.get("history", {})
        prices = history.get("prices", [])
        times = history.get("times", [])
        pip_size = data.get("pip_size", 4)

        if not prices:
            raise RuntimeError(f"No price data for {symbol}")

        price = float(prices[-1])
        quote_time = times[-1] if times else None
        quote_dt = datetime.fromtimestamp(quote_time, tz=timezone.utc) if quote_time else None

        # For synthetic indices, typical tick size is 0.01 to 1 depending on symbol
        # Estimate tick_size from pip_size
        tick_size = 10 ** (-pip_size)
        digits = pip_size
        pip = _pip_size_from_tick(tick_size)

        # Synthetic indices don't have bid/ask spread in the same way
        # Use price as both bid and ask
        return DerivTickInfo(
            symbol=symbol,
            bid=price,
            ask=price,
            spread_points=0,
            point=tick_size,
            digits=digits,
            pip=pip,
            pip_value_per_lot=1.0,  # Approximate
            tick_value=1.0,
            last=price,
            volume=0,
            quote_time=quote_dt,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv tick for {symbol}: {exc}")


def deriv_symbol_info(symbol: str) -> DerivSymbolInfo:
    """Get symbol information (limited with shared app_id)."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        # Try to get basic info from ticks_history
        data = client.ticks_history(symbol, count=1, end="latest", style="ticks")
        pip_size = data.get("pip_size", 4)
        tick_size = 10 ** (-pip_size)

        return DerivSymbolInfo(
            symbol=symbol,
            display_name=symbol,
            market="synthetic",
            market_display_name="Synthetic Indices",
            submarket="random",
            submarket_display_name="Random",
            pip=_pip_size_from_tick(tick_size),
            tick_size=tick_size,
            tick_value=1.0,
            min_volume=0.01,
            max_volume=100.0,
            volume_step=0.01,
            margin_rate=0.0,
            swap_long=0.0,
            swap_short=0.0,
            trading_sessions=[],
            is_open=True,  # Synthetic indices trade 24/7
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv symbol info for {symbol}: {exc}")


def deriv_market_status(symbol: str) -> dict:
    """Get market open status, trading session, and spread health."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        sym_info = deriv_symbol_info(symbol)
        tick = deriv_tick(symbol)
        utc_hour = datetime.now(timezone.utc).hour

        # Determine session
        if 22 <= utc_hour or utc_hour < 6:
            session = "Sydney/Tokyo"
        elif 6 <= utc_hour < 13:
            session = "London"
        else:
            session = "New York"

        spread_limit = get_settings().risk.max_spread_points

        return {
            "symbol": symbol,
            "market_open": sym_info.is_open,
            "session": session,
            "utc_time": datetime.now(timezone.utc).isoformat(),
            "spread_points": tick.spread_points,
            "max_spread_points": spread_limit,
            "spread_ok": tick.spread_points <= spread_limit,
            "trade_mode": "open" if sym_info.is_open else "closed",
            "digits": tick.digits,
            "point": tick.point,
            "tick_size": tick.point,
            "tick_value": tick.tick_value,
            "volume_min": sym_info.min_volume,
            "volume_max": sym_info.max_volume,
            "volume_step": sym_info.volume_step,
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv market status for {symbol}: {exc}")


def deriv_candles(
    symbol: str, count: int = 100, granularity: int = 60, end: str | int = "latest"
) -> list[dict[str, Any]]:
    """Get historical candles from Deriv."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        data = client.ticks_history(symbol, count=count, end=end, style="candles", granularity=granularity)
        candles = data.get("candles", [])
        result = []
        for c in candles:
            result.append({
                "time": c.get("epoch", 0),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
            })
        return result
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv candles for {symbol}: {exc}")


def deriv_trading_times(symbols: list[str]) -> dict[str, Any]:
    """Get trading times for symbols."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        return client.trading_times(symbols)
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv trading times: {exc}")


def deriv_time() -> datetime:
    """Get Deriv server time."""
    client = get_deriv_client()
    client.ensure_connected()
    try:
        data = client.time()
        epoch = data.get("time", 0)  # time is directly in response, not nested
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except Exception as exc:
        raise RuntimeError(f"Failed to get Deriv time: {exc}")


def register(mcp) -> None:
    @mcp.tool(
        name="deriv_symbols",
        description="List available Deriv synthetic indices (default app_id). For forex/commodities, use your own app_id.",
    )
    def tool_deriv_symbols(product_type: str = "basic", max_results: int = 200) -> dict:
        found = deriv_symbols(product_type, max_results)
        return {"symbols": found, "count": len(found)}

    @mcp.tool(
        name="deriv_search",
        description="Search Deriv synthetic indices by name fragment (e.g. 'R_50', '1HZ').",
    )
    def tool_deriv_search(query: str, max_results: int = 50) -> dict:
        found = deriv_search(query, max_results)
        return {"symbols": found, "count": len(found)}

    @mcp.tool(
        name="deriv_tick",
        description="Current price for a Deriv synthetic index (via ticks_history).",
    )
    def tool_deriv_tick(symbol: str) -> DerivTickInfo:
        return deriv_tick(symbol)

    @mcp.tool(
        name="deriv_symbol_info",
        description="Basic symbol information for Deriv synthetic indices.",
    )
    def tool_deriv_symbol_info(symbol: str) -> DerivSymbolInfo:
        return deriv_symbol_info(symbol)

    @mcp.tool(
        name="deriv_market_status",
        description="Deriv market status for synthetic indices (24/7 trading).",
    )
    def tool_deriv_market_status(symbol: str) -> dict:
        return deriv_market_status(symbol)

    @mcp.tool(
        name="deriv_candles",
        description="Get historical candles from Deriv (granularity in seconds: 60=M1, 300=M5, 900=M15, 3600=H1, 86400=D1).",
    )
    def tool_deriv_candles(
        symbol: str, count: int = 100, granularity: int = 60, end: str | int = "latest"
    ) -> dict:
        candles = deriv_candles(symbol, count, granularity, end)
        return {"candles": candles, "count": len(candles)}

    @mcp.tool(
        name="deriv_trading_times",
        description="Get trading times for Deriv symbols.",
    )
    def tool_deriv_trading_times(symbols: list[str]) -> dict:
        return deriv_trading_times(symbols)

    @mcp.tool(
        name="deriv_time",
        description="Get Deriv server time.",
    )
    def tool_deriv_time() -> dict:
        dt = deriv_time()
        return {"time": dt.isoformat(), "epoch": int(dt.timestamp())}
