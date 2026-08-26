"""MCP tools for explainable Autochartist-style chart detection."""

from __future__ import annotations

from typing import Any

from mt5_mcp.chart_patterns import analyze_chart
from mt5_mcp.client import get_client
from mt5_mcp.common import Timeframe


DEFAULT_SCAN_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]


def detect(
    symbol: str,
    timeframe: Timeframe = "H1",
    lookback: int = 500,
) -> dict[str, Any]:
    client = get_client()
    client.ensure_connected()
    frame = client.rates(symbol, timeframe, lookback)
    return analyze_chart(frame, symbol=symbol, timeframe=timeframe, lookback=lookback)


def scan(
    symbols: list[str] | None = None,
    timeframe: Timeframe = "H1",
    lookback: int = 500,
    min_quality: float = 60.0,
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for symbol in symbols or DEFAULT_SCAN_SYMBOLS:
        try:
            report = detect(symbol, timeframe, lookback)
            patterns = [
                pattern
                for pattern in report.get("patterns", [])
                if float(pattern.get("ai_score", 0.0)) >= min_quality
            ]
            if patterns:
                found.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "verdict": report.get("verdict"),
                        "bias": report.get("bias"),
                        "top_pattern": patterns[0],
                        "pattern_count": len(patterns),
                        "current_price": report.get("current_price"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            failed.append({"symbol": symbol, "error": str(exc)})
    found.sort(
        key=lambda item: float(item["top_pattern"].get("ai_score", 0.0)),
        reverse=True,
    )
    return {
        "timeframe": timeframe,
        "min_quality": min_quality,
        "scanned": len(symbols or DEFAULT_SCAN_SYMBOLS),
        "signals": found,
        "failed": failed,
    }


def register(mcp) -> None:
    @mcp.tool(
        name="ai_chart_detection",
        description=(
            "Explainable Autochartist-style detection for double tops/bottoms, "
            "head-and-shoulders, triangles, breakouts, support/resistance, "
            "candlestick confluence, targets, invalidation and AI quality scores."
        ),
    )
    def tool_ai_chart_detection(
        symbol: str,
        timeframe: Timeframe = "H1",
        lookback: int = 500,
    ) -> dict[str, Any]:
        return detect(symbol, timeframe, lookback)

    @mcp.tool(
        name="ai_chart_scan",
        description="Rank multiple symbols by Autochartist-style chart pattern quality and confluence.",
    )
    def tool_ai_chart_scan(
        symbols: list[str] | None = None,
        timeframe: Timeframe = "H1",
        lookback: int = 500,
        min_quality: float = 60.0,
    ) -> dict[str, Any]:
        return scan(symbols, timeframe, lookback, min_quality)
