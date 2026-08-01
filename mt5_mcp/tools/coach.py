"""AI Coach tools."""

from __future__ import annotations

from typing import Any

from coach import coach_review


def register(mcp) -> None:
    @mcp.tool(
        name="coach_review",
        description="AI Coach: full post-trade review of a closed trade (by ticket, or the most recent "
        "one). Explains why it was taken, evidence for and against, what to improve, and how similar "
        "historical trades performed.",
    )
    def tool_coach_review(ticket: int | None = None) -> dict[str, Any]:
        return coach_review(ticket)
