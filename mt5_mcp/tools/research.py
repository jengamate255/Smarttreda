"""Research Agent tools."""

from __future__ import annotations

from typing import Any

from research import report, weekly_report


def register(mcp) -> None:
    @mcp.tool(
        name="research_report",
        description="Research Agent: analyse all completed trades over the last N days and produce the "
        "research report (win rate, expectancy, profit factor, average R, max drawdown, best/worst "
        "symbols and setups, sessions, mistakes) plus data-driven improvement suggestions.",
    )
    def tool_research_report(
        days: int = 7, min_trades: int = 1, save: bool = True
    ) -> dict[str, Any]:
        return report(days=days, min_trades=min_trades, save=save)

    @mcp.tool(
        name="weekly_research_report",
        description="Research Agent: the standard weekly report (last 7 days), saved to memory/reports/.",
    )
    def tool_weekly_research_report(save: bool = True) -> dict[str, Any]:
        return weekly_report(save=save)
