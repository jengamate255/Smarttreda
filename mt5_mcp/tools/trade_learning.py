"""MCP access to the journal-backed pre-trade learning guard."""

from __future__ import annotations

from mt5_mcp.memory.trade_guard import evaluate


def register(mcp) -> None:
    @mcp.tool(
        name="trade_learning_guard",
        description="Apply learned mistake guardrails to a proposed Boom/Crash setup.",
    )
    def tool_trade_learning_guard(
        symbol: str,
        base_max_spike_age: int = 15,
        base_confidence_floor: float = 90.0,
    ) -> dict:
        return evaluate(
            symbol,
            base_max_spike_age=base_max_spike_age,
            base_confidence_floor=base_confidence_floor,
        )
