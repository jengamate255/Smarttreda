"""Research / AI Coach / learning-stats API routes (extracted from web_dashboard.py).

Endpoints:
    GET /api/research-report          - periodic research report (N days)
    GET /api/weekly-research-report   - standard 7-day report
    GET /api/coach-review/latest      - post-trade review of most recent trade
    GET /api/coach-review/{ticket}    - post-trade review by ticket
    GET /api/sessions                 - best sessions/setups/pairs
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from memory.learning import best_pairs, best_sessions, best_setups
from mt5_mcp.coach import coach_review
from mt5_mcp.research import report as research_report
from mt5_mcp.research import weekly_report

logger = logging.getLogger("web_dashboard")

router = APIRouter()


@router.get("/api/research-report")
def api_research_report(days: int = 7, min_trades: int = 1, save: bool = True):
    """Periodic research report: stats + data-driven suggestions over the last N days."""
    try:
        return research_report(days=days, min_trades=min_trades, save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research report failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/weekly-research-report")
def api_weekly_research_report(save: bool = True):
    """Standard weekly report (last 7 days), saved to memory/reports/."""
    try:
        return weekly_report(save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly research report failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/coach-review/latest")
def coach_review_latest():
    """Full post-trade review for the most recent closed trade."""
    try:
        return coach_review(None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review latest failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/coach-review/{ticket}")
def coach_review_by_ticket(ticket: int):
    """Full post-trade review for a specific closed trade ticket."""
    try:
        return coach_review(ticket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review failed for %s: %s", ticket, exc)
        return {"error": str(exc)}


@router.get("/api/sessions")
def sessions():
    """Best sessions/setups/pairs from the trade journal."""
    try:
        return {
            "sessions": best_sessions(),
            "setups": best_setups(),
            "pairs": best_pairs(),
        }
    except Exception as exc:  # noqa: BLE001 - learning is best-effort
        logger.warning("sessions endpoint failed: %s", exc)
        return {"sessions": [], "setups": [], "pairs": [], "error": str(exc)}
