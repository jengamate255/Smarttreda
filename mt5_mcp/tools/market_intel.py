"""Market Intelligence Agent tools."""

from __future__ import annotations

from typing import Any

from market_intel import health_commentary, market_health, scan_markets


def register(mcp) -> None:
    @mcp.tool(
        name="market_intelligence",
        description="Market Intelligence Agent report for a symbol: Market Health Score (0-100), regime "
        "(trending/ranging/volatile), abnormal volatility (ATR percentile), active session, spread, news "
        "and liquidity - each component scored and explained with evidence.",
    )
    def tool_market_intelligence(
        symbol: str, timeframe: str = "H1", lookback: int = 400
    ) -> dict[str, Any]:
        return market_health(symbol, timeframe, lookback)

    @mcp.tool(
        name="market_scan",
        description="Rank multiple symbols by Market Health Score. Pass explicit symbols or let the agent "
        "scan the major FX/metals/indices universe. Best conditions first.",
    )
    def tool_market_scan(
        symbols: list[str] | None = None,
        timeframe: str = "H1",
        lookback: int = 300,
        min_score: float = 0.0,
        max_results: int = 12,
    ) -> dict[str, Any]:
        return scan_markets(symbols, timeframe, lookback, min_score, max_results)

    @mcp.tool(
        name="market_health_note",
        description="One-line Market Intelligence verdict for a symbol (for logs and the trade journal).",
    )
    def tool_market_health_note(symbol: str, timeframe: str = "H1") -> str:
        return health_commentary(market_health(symbol, timeframe))
