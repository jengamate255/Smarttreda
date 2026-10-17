"""Journal reconciliation tool: journal vs broker history."""

from __future__ import annotations

from mt5_mcp.execution_gate import live_stats
from mt5_mcp.journal_reconcile import RECONCILE_DAYS, live_stats_from, reconcile


def journal_reconcile(days: int = RECONCILE_DAYS) -> dict:
    """Reconcile memory/journal.db with MT5 deal history and refresh live stats."""
    return reconcile(days=days)


def journal_reconcile_status() -> dict:
    """Live per-symbol stats the execution gate uses for suspension."""
    return {"symbols": live_stats()}


def register(mcp) -> None:
    @mcp.tool(
        name="journal_reconcile",
        description=(
            "Reconcile the trade journal against broker deal history: insert "
            "missing closed trades, correct P&L/outcome (commission and swap "
            "included) and refresh the live per-symbol stats the execution "
            "gate uses to suspend losing symbols."
        ),
    )
    def tool_journal_reconcile(days: int = RECONCILE_DAYS) -> dict:
        return journal_reconcile(days=days)

    @mcp.tool(
        name="live_symbol_stats",
        description=(
            "Per-symbol live win rate, net P&L and PF from reconciled broker "
            "history (symbols with 20+ trades and negative net are suspended "
            "by the execution gate)."
        ),
    )
    def tool_live_symbol_stats() -> dict:
        return journal_reconcile_status()


__all__ = [
    "RECONCILE_DAYS",
    "journal_reconcile",
    "journal_reconcile_status",
    "live_stats_from",
    "register",
]
