"""MT5 MCP server entry point.

Registers every tool module on a FastMCP instance and exposes server-level
tools (health, journal sync, learning queries, statistics dashboard).

Run:
    python server.py                  # stdio (default for MCP clients)
    MCP_TRANSPORT=streamable-http python server.py
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import time
from typing import Any

from fastmcp import FastMCP

from mt5_mcp.client import get_client
from mt5_mcp.common import truncate
from mt5_mcp.config import get_settings, setup_logging

logger = logging.getLogger("mt5_mcp.server")


class LoggingMiddleware:
    """Logs every MCP tool call (time, user, tool, inputs, outputs, duration, errors) to the logger and SQLite audit table."""

    async def on_call_tool(self, context, call_next):
        started = time.perf_counter()
        name, arguments, user = "unknown", {}, "mcp-client"
        try:
            fastmcp_context = context.fastmcp_context
            user = str(getattr(fastmcp_context, "client_id", None) or "mcp-client")
            request_context = getattr(fastmcp_context, "request_context", None)
            params = getattr(request_context, "params", None)
            name = str(getattr(params, "name", "unknown"))
            arguments = getattr(params, "arguments", {}) or {}
        except Exception:
            pass
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "tool=%s user=%s inputs=%s duration_ms=%.1f",
                name,
                user,
                truncate(repr(arguments), 500),
                duration_ms,
            )
            _audit(
                name, user, arguments, truncate(repr(result), 2000), duration_ms, None
            )
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception("tool=%s user=%s inputs=%s", name, user, arguments)
            _audit(name, user, arguments, "", duration_ms, str(exc))
            raise

    async def __call__(self, context, call_next):
        if getattr(context, "method", None) == "tools/call":
            return await self.on_call_tool(context, call_next)
        return await call_next(context)


def _audit(
    tool: str,
    user: str,
    inputs: Any,
    outputs: Any,
    duration_ms: float,
    error: str | None,
) -> None:
    try:
        from mt5_mcp.memory.trade_journal import get_journal

        get_journal().audit_write(tool, user, inputs, outputs, duration_ms, error)
    except Exception:
        logger.debug("audit write failed (journal unavailable)")


def register_all(mcp) -> None:
    from mt5_mcp.tools import (
        account,
        boom_crash_strategy,
        chart_patterns,
        candles,
        coach,
        compliance,
        deriv,
        history,
        indicators,
        journal_reconcile,
        market,
        market_intel,
        news,
        positions,
        price_action,
        research,
        risk,
        strategy,
        supply_demand,
        trade_learning,
        trades,
    )

    for module in (
        account,
        boom_crash_strategy,
        chart_patterns,
        market,
        candles,
        indicators,
        supply_demand,
        price_action,
        strategy,
        trades,
        positions,
        history,
        journal_reconcile,
        risk,
        news,
        compliance,
        market_intel,
        research,
        coach,
        deriv,
        trade_learning,
    ):
        module.register(mcp)
    if hasattr(mcp, "add_middleware"):
        mcp.add_middleware(LoggingMiddleware())
    _register_server_tools(mcp)


def _register_server_tools(mcp) -> None:
    from mt5_mcp.memory.learning import (
        best_pairs,
        best_sessions,
        best_setups,
        learning_report,
        repeated_mistakes,
    )
    from mt5_mcp.memory.statistics import dashboard
    from mt5_mcp.memory.trade_journal import get_journal

    @mcp.tool(
        name="health", description="MT5 connection health, uptime and last error."
    )
    def tool_health() -> dict:
        client = get_client()
        try:
            client.ensure_connected()
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "last_error": str(exc),
                "uptime_s": 0.0,
            }
        try:
            health = client.health()
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "last_error": str(exc),
                "uptime_s": 0.0,
            }
        info = {}
        try:
            info = client.account_info()
        except Exception:
            pass
        return {
            **health,
            "account": {
                "login": info.get("login"),
                "server": info.get("server"),
                "currency": info.get("currency"),
                "leverage": info.get("leverage"),
            },
        }

    @mcp.tool(
        name="system_info", description="Server version and configuration summary."
    )
    def tool_system_info() -> dict:
        settings = get_settings()
        return {
            "name": settings.server.name,
            "version": settings.server.version,
            "python": sys.version.split()[0],
            "risk": settings.risk.model_dump(),
            "strategy": settings.strategy.model_dump(),
            "logging_level": settings.logging.level,
        }

    @mcp.tool(
        name="sync_journal",
        description="Sync closed MT5 trades into the SQLite journal.",
    )
    def tool_sync_journal(days: int = 30) -> dict:
        return get_journal().sync_from_mt5(get_client(), days=days)

    @mcp.tool(
        name="journal_record",
        description="Record a trade (or notes for one) in the journal.",
    )
    def tool_journal_record(
        ticket: int | None = None,
        symbol: str = "",
        side: str = "",
        setup: str = "manual",
        pnl: float | None = None,
        reason: str = "",
        mistakes: str = "",
        lessons: str = "",
        screenshot_path: str = "",
        market_conditions: str = "",
        confidence: float | None = None,
        trade_score: float | None = None,
        zone_strength: float | None = None,
    ) -> dict:
        return {
            "id": get_journal().record_trade(
                ticket=ticket,
                symbol=symbol,
                side=side,
                setup=setup,
                pnl=pnl,
                reason=reason,
                mistakes=mistakes,
                lessons=lessons,
                screenshot_path=screenshot_path,
                market_conditions=market_conditions,
                confidence=confidence,
                trade_score=trade_score,
                zone_strength=zone_strength,
            )
        }

    @mcp.tool(
        name="journal_update_notes",
        description="Attach reason/mistakes/lessons/screenshot to a closed trade.",
    )
    def tool_journal_update_notes(
        ticket: int,
        mistakes: str = "",
        lessons: str = "",
        reason: str = "",
        screenshot_path: str = "",
        market_conditions: str = "",
    ) -> dict:
        return {
            "updated": get_journal().update_notes(
                ticket,
                mistakes=mistakes,
                lessons=lessons,
                reason=reason,
                screenshot_path=screenshot_path,
                market_conditions=market_conditions,
            )
        }

    @mcp.tool(
        name="journal_query",
        description="List journal trades with filters (symbol, setup, outcome).",
    )
    def tool_journal_query(
        limit: int = 100, symbol: str = "", setup: str = "", outcome: str = ""
    ) -> dict:
        found = get_journal().list_trades(
            limit=limit, symbol=symbol, setup=setup, outcome=outcome
        )
        return {"trades": found, "count": len(found)}

    @mcp.tool(
        name="repeated_mistakes",
        description="What mistakes am I repeating? Counts, PnL impact, last seen.",
    )
    def tool_repeated_mistakes(limit: int = 10) -> dict:
        found = repeated_mistakes(limit=limit)
        return {"mistakes": found, "count": len(found)}

    @mcp.tool(
        name="best_setups",
        description="Which setups win the most? Win rate and net PnL per setup type.",
    )
    def tool_best_setups(min_trades: int = 1) -> dict:
        found = best_setups(min_trades=min_trades)
        return {"setups": found, "count": len(found)}

    @mcp.tool(
        name="best_pairs",
        description="Which pairs perform best? Win rate and net PnL per symbol.",
    )
    def tool_best_pairs(min_trades: int = 1) -> dict:
        found = best_pairs(min_trades=min_trades)
        return {"pairs": found, "count": len(found)}

    @mcp.tool(
        name="best_sessions",
        description="Which trading sessions perform best (UTC hours)?",
    )
    def tool_best_sessions() -> dict:
        found = best_sessions()
        return {"sessions": found, "count": len(found)}

    @mcp.tool(
        name="learning_report",
        description="Combined learning report: mistakes, setups, pairs, sessions.",
    )
    def tool_learning_report() -> dict:
        return learning_report()

    @mcp.tool(
        name="statistics_dashboard",
        description="Win rate, expectancy, profit factor, Sharpe, drawdown, equity curve, best setups/pairs/sessions.",
    )
    def tool_statistics_dashboard(start_balance: float = 0.0) -> dict:
        return dashboard(start_balance=start_balance)

    @mcp.tool(
        name="audit_log",
        description="Recent tool call log (time, user, tool, duration, errors).",
    )
    def tool_audit_log(limit: int = 50) -> dict:
        found = get_journal().recent_audit(limit=limit)
        return {"records": found, "count": len(found)}

    @mcp.tool(
        name="deriv_health", description="Deriv connection health, uptime and last error."
    )
    def tool_deriv_health() -> dict:
        try:
            from mt5_mcp.deriv_client import get_deriv_client
            client = get_deriv_client()
            return client.health()
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "last_error": str(exc),
                "uptime_s": 0.0,
            }


def build_server() -> FastMCP:
    from fastmcp import FastMCP

    settings = get_settings()
    mcp = FastMCP(settings.server.name, version=settings.server.version)
    register_all(mcp)
    return mcp


# Backwards-compat: if imported rather than executed, expose a build_server().
# The canonical entrypoint is now the root `server.py` which registers
# mt5_mcp tools on the same FastMCP instance.


def main() -> None:
    setup_logging()
    settings = get_settings()
    client = get_client()
    if client.connect():
        logger.info("Connected to MT5 at startup")
    else:
        logger.warning(
            "MT5 not connected at startup: %s (tools will retry)", client.last_error()
        )
    
    # Initialize Deriv client if configured
    if settings.deriv.enabled:
        from mt5_mcp.deriv_client import get_deriv_client
        deriv_client = get_deriv_client()
        if deriv_client.connect():
            logger.info("Connected to Deriv at startup")
        else:
            logger.warning(
                "Deriv not connected at startup: %s (tools will retry)", deriv_client.last_error()
            )
        atexit.register(deriv_client.shutdown)
    else:
        logger.info("Deriv not configured, skipping")
    
    atexit.register(client.shutdown)

    mcp = build_server()
    transport = os.getenv("MCP_TRANSPORT", settings.server.transport)
    logger.info(
        "Starting %s %s on transport=%s",
        settings.server.name,
        settings.server.version,
        transport,
    )
    try:
        mcp.run(transport=transport)
    except Exception:
        logger.exception("Server failed")
        raise


if __name__ == "__main__":
    main()
