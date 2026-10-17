"""Drawdown recovery mode.

Monitors account drawdown from peak equity and automatically adjusts risk:
- When drawdown exceeds 3%: reduce risk from max_risk_percent to reduced_risk_percent
- When drawdown recovers below 1%: restore risk to max_risk_percent

State persisted to memory/drawdown_recovery_state.json.
Alerts sent via Telegram on risk changes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("mt5_mcp.drawdown_recovery")

MEMORY_DIR = Path(__file__).parent / "memory"
STATE_FILE = MEMORY_DIR / "drawdown_recovery_state.json"

# Default thresholds (overridden by settings.json when available)
DEFAULT_REDUCE_THRESHOLD = 3.0  # drawdown % to trigger risk reduction
DEFAULT_RESTORE_THRESHOLD = 1.0  # drawdown % to restore full risk
DEFAULT_MAX_RISK_PERCENT = 1.0
DEFAULT_REDUCED_RISK_PERCENT = 0.5


def _default_state() -> dict:
    return {
        "peak_equity": 0.0,
        "mode": "normal",  # "normal" or "reduced"
        "last_risk_percent": DEFAULT_MAX_RISK_PERCENT,
        "last_change_time": 0.0,
        "last_change_reason": "",
    }


def _load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _default_state()
                base.update({k: v for k, v in data.items() if k in base})
                return base
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read drawdown recovery state at %s", path)
    return _default_state()


def _save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("could not write drawdown recovery state at %s", path)


def _update_peak_equity(state: dict, equity: float) -> float:
    """Update peak equity if current equity exceeds it. Returns peak."""
    if equity > state["peak_equity"]:
        state["peak_equity"] = equity
    return state["peak_equity"]


def _current_drawdown_percent(peak: float, equity: float) -> float:
    """Calculate drawdown as a percentage from peak."""
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def _alert_risk_change(mode: str, risk_percent: float, drawdown_pct: float) -> None:
    """Send Telegram alert about risk change. Best-effort, never raises."""
    try:
        from mt5_mcp.notify import notify
        if mode == "reduced":
            msg = (
                f"DRAWDOWN RECOVERY: Risk reduced to {risk_percent:.1f}% "
                f"(drawdown {drawdown_pct:.1f}% from peak)"
            )
        else:
            msg = (
                f"DRAWDOWN RECOVERY: Risk restored to {risk_percent:.1f}% "
                f"(drawdown recovered to {drawdown_pct:.1f}%)"
            )
        notify("drawdown_recovery", msg, force=True)
    except Exception as exc:
        logger.debug("drawdown recovery alert failed: %s", exc)


def _log_risk_change(state: dict, mode: str, risk_percent: float, drawdown_pct: float) -> None:
    """Log the risk change to journal events."""
    try:
        from mt5_mcp.memory.trade_journal import get_journal
        journal = get_journal()
        reason = (
            f"drawdown {drawdown_pct:.1f}% -> mode={mode}, risk={risk_percent:.1f}%"
        )
        journal.record_event("drawdown_recovery", reason)
    except Exception as exc:
        logger.debug("drawdown recovery journal log failed: %s", exc)


def drawdown_recovery_risk_percent(
    equity: float,
    max_risk_percent: float | None = None,
    reduced_risk_percent: float | None = None,
    reduce_threshold: float | None = None,
    restore_threshold: float | None = None,
    state_path: Path | None = None,
    enabled: bool = True,
) -> dict:
    """Determine the effective risk percent based on drawdown recovery mode.

    Args:
        equity: Current account equity.
        max_risk_percent: Normal risk percent (e.g. 1.0).
        reduced_risk_percent: Reduced risk percent during drawdown (e.g. 0.5).
        reduce_threshold: Drawdown % to trigger reduction (default 3.0).
        restore_threshold: Drawdown % to trigger restoration (default 1.0).
        state_path: Override state file path (for testing).
        enabled: If False, always returns max_risk_percent with mode="disabled".

    Returns:
        dict with keys:
            risk_percent: The effective risk percent to use.
            mode: "normal", "reduced", or "disabled".
            drawdown_percent: Current drawdown from peak.
            peak_equity: Tracked peak equity.
            changed: Whether the mode changed on this call.
    """
    if not enabled:
        return {
            "risk_percent": max_risk_percent or DEFAULT_MAX_RISK_PERCENT,
            "mode": "disabled",
            "drawdown_percent": 0.0,
            "peak_equity": equity,
            "changed": False,
        }

    max_risk = max_risk_percent if max_risk_percent is not None else DEFAULT_MAX_RISK_PERCENT
    reduced_risk = reduced_risk_percent if reduced_risk_percent is not None else DEFAULT_REDUCED_RISK_PERCENT
    reduce_thresh = reduce_threshold if reduce_threshold is not None else DEFAULT_REDUCE_THRESHOLD
    restore_thresh = restore_threshold if restore_threshold is not None else DEFAULT_RESTORE_THRESHOLD

    state = _load_state(state_path)
    peak = _update_peak_equity(state, equity)
    dd_pct = _current_drawdown_percent(peak, equity)

    prev_mode = state["mode"]
    changed = False

    if state["mode"] == "normal" and dd_pct >= reduce_thresh:
        # Transition to reduced risk
        state["mode"] = "reduced"
        state["last_risk_percent"] = reduced_risk
        state["last_change_time"] = time.time()
        state["last_change_reason"] = f"drawdown {dd_pct:.1f}% >= {reduce_thresh}%"
        changed = True
        logger.info(
            "drawdown recovery: REDUCING risk %.1f%% -> %.1f%% (drawdown %.1f%%)",
            max_risk, reduced_risk, dd_pct,
        )
        _alert_risk_change("reduced", reduced_risk, dd_pct)
        _log_risk_change(state, "reduced", reduced_risk, dd_pct)

    elif state["mode"] == "reduced" and dd_pct < restore_thresh:
        # Transition back to normal risk
        state["mode"] = "normal"
        state["last_risk_percent"] = max_risk
        state["last_change_time"] = time.time()
        state["last_change_reason"] = f"drawdown {dd_pct:.1f}% < {restore_thresh}%"
        changed = True
        logger.info(
            "drawdown recovery: RESTORING risk %.1f%% -> %.1f%% (drawdown %.1f%%)",
            reduced_risk, max_risk, dd_pct,
        )
        _alert_risk_change("normal", max_risk, dd_pct)
        _log_risk_change(state, "normal", max_risk, dd_pct)

    _save_state(state, state_path)

    effective_risk = reduced_risk if state["mode"] == "reduced" else max_risk

    return {
        "risk_percent": effective_risk,
        "mode": state["mode"],
        "drawdown_percent": round(dd_pct, 2),
        "peak_equity": round(peak, 2),
        "changed": changed,
    }


def get_drawdown_recovery_status(state_path: Path | None = None) -> dict:
    """Get current drawdown recovery status without modifying state.

    Useful for dashboards and diagnostic endpoints.
    """
    state = _load_state(state_path)
    return {
        "peak_equity": round(state["peak_equity"], 2),
        "mode": state["mode"],
        "current_risk_percent": state["last_risk_percent"],
        "last_change_time": state["last_change_time"],
        "last_change_reason": state["last_change_reason"],
    }


def reset_drawdown_recovery(equity: float, state_path: Path | None = None) -> dict:
    """Reset peak equity to current value and restore normal mode.

    Use when manually resetting or reinitializing.
    """
    state = _default_state()
    state["peak_equity"] = equity
    state["last_risk_percent"] = DEFAULT_MAX_RISK_PERCENT
    _save_state(state, state_path)
    logger.info("drawdown recovery reset: peak=%.2f", equity)
    return {
        "peak_equity": equity,
        "mode": "normal",
        "risk_percent": DEFAULT_MAX_RISK_PERCENT,
    }
