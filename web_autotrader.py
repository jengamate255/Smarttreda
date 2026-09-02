"""Auto Trader monitor API — endpoints for the deployed unattended trader.

Read-only telemetry plus the kill-switch (an explicit, confirmable
control surfaced in the dashboard). Reads:
  - auto_trader.log tail (last cycles)
  - auto_trader_state.json (cycle count, last plans, positions)
  - kill-switch file existence
  - scheduled task state
  - JP225 monthly validation reports

The kill switch creates memory/AUTO_TRADER_KILL (pauses trading at the
next cycle); un-kill removes it. The auto trader never sends orders
while the file exists.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

PROJECT_DIR = Path(__file__).resolve().parent
MEMORY_DIR = PROJECT_DIR / "mt5_mcp" / "memory"
LOG_PATH = PROJECT_DIR / "logs" / "auto_trader.log"
STATE_PATH = MEMORY_DIR / "auto_trader_state.json"
KILL_PATH = MEMORY_DIR / "AUTO_TRADER_KILL"
RESEARCH_DIR = MEMORY_DIR / "research_lab"
TASK_NAME = "MT5AutoTraderSIM"

router = APIRouter(prefix="/api/autotrader", tags=["autotrader"])
logger = logging.getLogger("web_autotrader")

LOG_TAIL_LINES = 40


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _log_tail(n: int = LOG_TAIL_LINES) -> list[str]:
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def _task_state() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue).State"],
            capture_output=True, text=True, timeout=15,
        )
        return (out.stdout or "").strip() or "Unknown"
    except Exception:  # noqa: BLE001
        return "Unknown"


def _last_validation() -> dict:
    reports = sorted(RESEARCH_DIR.glob("jp225_monthly_*.json"))
    if not reports:
        return {"available": False}
    try:
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
        data["available"] = True
        data["file"] = reports[-1].name
        # Age of the report for the overdue badge (re-run monthly per policy)
        try:
            run_at = datetime.fromisoformat(data.get("run_at", ""))
            data["age_days"] = round(
                (datetime.now(timezone.utc) - run_at).total_seconds() / 86400.0, 1
            )
        except (ValueError, TypeError):
            data["age_days"] = None
        data["overdue"] = bool(data.get("age_days") is not None and data["age_days"] > 35)
        return data
    except (OSError, ValueError):
        return {"available": False}


@router.get("/status")
def autotrader_status():
    """Full auto-trader telemetry snapshot for the dashboard."""
    state = _read_state()
    last_plans = state.get("last_plan") or {}
    plans = {}
    for sym, entry in last_plans.items():
        p = entry.get("plan") or {}
        plans[sym] = {
            "bar": entry.get("bar"),
            "action": p.get("action"),
            "valid": p.get("valid"),
            "confidence": p.get("confidence"),
            "reason": p.get("reason"),
            "zone_strength": p.get("zone_strength"),
        }
    return {
        "task_state": _task_state(),
        "kill_switch": KILL_PATH.exists(),
        "cycles": state.get("runs", 0),
        "last_orders": state.get("last_order") or {},
        "managed": state.get("managed") or {},
        "plans": plans,
        "log_tail": _log_tail(),
        "last_validation": _last_validation(),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/log")
def autotrader_log(lines: int = 100):
    """Raw log tail (capped)."""
    n = max(10, min(lines, 500))
    return {"lines": _log_tail(n)}


@router.get("/validation")
def validation_status():
    """Latest JP225 monthly validation verdict."""
    v = _last_validation()
    if not v.get("available"):
        raise HTTPException(404, "No validation report found — run validate_jp225_monthly.py")
    return v


@router.post("/kill")
def engage_kill_switch():
    """Pause the auto trader (takes effect at the next cycle)."""
    try:
        KILL_PATH.write_text(
            f"Kill switch engaged from dashboard at {datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
        return {"status": "engaged", "note": "Auto trader will pause at its next cycle"}
    except OSError as exc:
        raise HTTPException(500, f"Could not write kill switch: {exc}") from exc


@router.delete("/kill")
def release_kill_switch():
    """Resume the auto trader."""
    try:
        if KILL_PATH.exists():
            KILL_PATH.unlink()
        return {"status": "released"}
    except OSError as exc:
        raise HTTPException(500, f"Could not remove kill switch: {exc}") from exc
