"""Supply & Demand tool."""

from __future__ import annotations

from mt5_mcp.client import get_client
from mt5_mcp.common import Timeframe, Zone
from mt5_mcp.supply_demand import detect_zones


def zones(
    symbol: str,
    timeframe: Timeframe = "H1",
    lookback: int = 400,
    min_strength: float = 0.0,
) -> list[Zone]:
    client = get_client()
    client.ensure_connected()
    frame = client.rates(symbol, timeframe, lookback)
    return detect_zones(frame, lookback=lookback, min_strength=min_strength)


def register(mcp) -> None:
    @mcp.tool(
        name="detect_zones",
        description="Detect supply/demand zones with strength scores (1-100), touches, freshness, broken/retested state. "
        "Demand zones are 'drop-base-rally' areas, supply zones are 'rally-base-drop' areas.",
    )
    def tool_detect_zones(
        symbol: str,
        timeframe: Timeframe = "H1",
        lookback: int = 400,
        min_strength: float = 0.0,
    ) -> dict:
        found = zones(symbol, timeframe, lookback, min_strength)
        return {"zones": found, "count": len(found)}
