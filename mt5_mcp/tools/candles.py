"""Candles tool: OHLCV retrieval for any supported timeframe."""

from __future__ import annotations

from client import get_client
from common import Candle, Timeframe


def get_candles(
    symbol: str, timeframe: Timeframe = "H1", count: int = 100
) -> list[Candle]:
    client = get_client()
    client.ensure_connected()
    frame = client.rates(symbol, timeframe, count)
    candles = []
    for row in frame.itertuples(index=False):
        candles.append(
            Candle(
                time=row.time,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                tick_volume=int(row.tick_volume),
                spread=int(row.spread),
                real_volume=int(row.real_volume),
            )
        )
    return candles


def register(mcp) -> None:
    @mcp.tool(
        name="get_candles",
        description="Retrieve OHLCV candles. Timeframes: M1, M5, M15, M30, H1, H4, D1, W1, MN1. Any number of candles up to 1,000,000.",
    )
    def tool_get_candles(
        symbol: str, timeframe: Timeframe = "H1", count: int = 100
    ) -> list[Candle]:
        return get_candles(symbol, timeframe, count)
