"""Challenge tracker API endpoints."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/api/challenge", tags=["challenge"])


@router.get("/dashboard", response_class=HTMLResponse)
def challenge_dashboard():
    """Serve the challenge tracker dashboard with auth token injected."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "challenge_dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    token = os.getenv("WEB_AUTH_TOKEN", "")
    if token:
        html = re.sub(
            r'<meta name="auth-token" content="[^"]*">',
            f'<meta name="auth-token" content="{token}">',
            html,
        )
    return HTMLResponse(html)


@router.get("/status")
def challenge_status():
    """Get current challenge progress."""
    from mt5_mcp.challenge_tracker import ChallengeTracker
    ct = ChallengeTracker()
    return ct.get_status()


@router.post("/init")
def challenge_init(balance: float = Query(..., description="Starting balance")):
    """Initialize challenge with starting balance."""
    from mt5_mcp.challenge_tracker import ChallengeTracker
    ct = ChallengeTracker()
    return ct.init_challenge(balance)


@router.get("/daily")
def challenge_daily():
    """Get day-by-day P&L breakdown."""
    from mt5_mcp.challenge_tracker import ChallengeTracker
    ct = ChallengeTracker()
    return ct.get_daily_breakdown()
