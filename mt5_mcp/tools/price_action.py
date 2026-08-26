"""Price action tool."""

from __future__ import annotations

from mt5_mcp.client import get_client
from mt5_mcp.common import PatternResult, Timeframe
from mt5_mcp.price_action import detect_patterns


def patterns(
    symbol: str, timeframe: Timeframe = "H1", lookback: int = 200
) -> list[PatternResult]:
    client = get_client()
    client.ensure_connected()
    frame = client.rates(symbol, timeframe, lookback)
    return detect_patterns(frame, lookback=lookback)


def register(mcp) -> None:
    @mcp.tool(
        name="detect_patterns",
        description="Detect price action patterns with confidence scores: engulfing, pin bar, hammer, shooting star, "
        "morning/evening star, inside/outside bar, BOS, CHOCH, liquidity sweep, fake breakout.",
    )
    def tool_detect_patterns(
        symbol: str, timeframe: Timeframe = "H1", lookback: int = 200
    ) -> dict:
        found = patterns(symbol, timeframe, lookback)
        return {"patterns": found, "count": len(found)}
