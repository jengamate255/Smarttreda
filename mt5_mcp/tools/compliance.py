"""Compliance tools: status dashboard and pre-trade veto check."""

from __future__ import annotations

from mt5_mcp.compliance import evaluate, gate
from mt5_mcp.memory.trade_journal import get_journal


def compliance_status() -> dict:
    status = evaluate()
    status["events"] = get_journal().recent_events(limit=5)
    return status


def compliance_check(
    symbol: str,
    side: str,
    lots: float,
    sl_price: float | None = None,
    sl_pips: float | None = None,
    confidence: float | None = None,
) -> dict:
    return gate(
        symbol,
        side,
        lots,
        sl_price=sl_price,
        sl_pips=sl_pips,
        confidence=confidence,
    )


def register(mcp) -> None:
    @mcp.tool(
        name="compliance_status",
        description="Prop firm compliance dashboard: mode, daily/drawdown buffers, open risk, correlation exposure, status (GREEN/YELLOW/RED).",
    )
    def tool_compliance_status() -> dict:
        return compliance_status()

    @mcp.tool(
        name="compliance_check",
        description="Pre-trade compliance veto: check a proposed trade against all prop firm rules before opening. Trade only if allowed is true.",
    )
    def tool_compliance_check(
        symbol: str,
        side: str,
        lots: float,
        sl_price: float | None = None,
        sl_pips: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        return compliance_check(
            symbol, side, lots, sl_price=sl_price, sl_pips=sl_pips, confidence=confidence
        )
