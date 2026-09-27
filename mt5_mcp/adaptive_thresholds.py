"""Adaptive thresholds — adjusts strategy parameters based on recent performance.

The system learns from its own trading history:
- No recent trades → relax thresholds to encourage entries
- Good win rate → maintain or slightly tighten
- Poor performance → tighten thresholds to reduce risk
- Consecutive losses → significant tightening (something's wrong)

Usage:
    from mt5_mcp.adaptive_thresholds import get_adaptive_thresholds
    thresholds = get_adaptive_thresholds()
    # Returns adjusted min_zone_score, min_trade_score, min_confidence, etc.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.config import get_settings

logger = logging.getLogger("adaptive_thresholds")

JOURNAL_PATH = Path(__file__).parent / "memory" / "journal.db"
STATE_PATH = Path(__file__).parent / "memory" / "adaptive_state.json"


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("cannot save adaptive state: %s", exc)


def _query_journal(days: int = 7) -> list[dict]:
    """Get recent trades from journal."""
    if not JOURNAL_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(JOURNAL_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor.execute(
            """
            SELECT symbol, side, pnl, open_time, close_time, setup
            FROM trades
            WHERE close_time >= ?
            ORDER BY close_time DESC
            """,
            (since,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("journal query failed: %s", exc)
        return []


def _analyze_performance(trades: list[dict]) -> dict[str, Any]:
    """Calculate performance metrics from trade history."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "consecutive_losses": 0,
            "hours_since_last_trade": 999,
            "avg_pnl": 0.0,
            "profit_factor": 0.0,
        }

    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) < 0]

    win_rate = len(wins) / len(trades) if trades else 0.0

    consecutive_losses = 0
    for t in trades:
        if (t.get("pnl") or 0) < 0:
            consecutive_losses += 1
        else:
            break

    last_trade_time = trades[0].get("close_time")
    hours_since_last = 999
    if last_trade_time:
        try:
            last_dt = datetime.fromisoformat(last_trade_time.replace("Z", "+00:00"))
            hours_since_last = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        except Exception:
            pass

    gross_profit = sum(t.get("pnl") or 0 for t in wins)
    gross_loss = abs(sum(t.get("pnl") or 0 for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    avg_pnl = sum(t.get("pnl") or 0 for t in trades) / len(trades)

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate * 100, 1),
        "consecutive_losses": consecutive_losses,
        "hours_since_last_trade": round(hours_since_last, 1),
        "avg_pnl": round(avg_pnl, 2),
        "profit_factor": round(profit_factor, 2),
    }


def _calculate_adjustments(perf: dict[str, Any]) -> dict[str, float]:
    """Calculate threshold adjustments based on performance.

    Returns multipliers (1.0 = no change, <1.0 = relax, >1.0 = tighten).
    """
    adjustments = {
        "zone_score": 1.0,
        "trade_score": 1.0,
        "confidence": 1.0,
        "risk_reward": 1.0,
    }

    total = perf.get("total_trades", 0)
    win_rate = perf.get("win_rate", 0.0)
    consec_losses = perf.get("consecutive_losses", 0)
    hours_since = perf.get("hours_since_last_trade", 999)

    # No recent trades: relax thresholds to encourage entries
    if total == 0 or hours_since > 48:
        adjustments["zone_score"] = 0.85  # relax by 15%
        adjustments["trade_score"] = 0.85
        adjustments["confidence"] = 0.80  # relax by 20%
        adjustments["risk_reward"] = 0.80
        logger.info("adaptive: no recent trades, relaxing thresholds 15-20%%")

    # Good performance: maintain or slightly tighten
    elif win_rate >= 60 and consec_losses == 0:
        adjustments["zone_score"] = 1.0  # maintain
        adjustments["trade_score"] = 1.0
        adjustments["confidence"] = 0.95  # slight tighten
        adjustments["risk_reward"] = 1.0
        logger.info("adaptive: good win rate %.1f%%, maintaining thresholds", win_rate)

    # Poor performance: tighten thresholds
    elif win_rate < 40 or consec_losses >= 3:
        adjustments["zone_score"] = 1.15  # tighten by 15%
        adjustments["trade_score"] = 1.15
        adjustments["confidence"] = 1.20  # tighten by 20%
        adjustments["risk_reward"] = 1.10
        logger.warning(
            "adaptive: poor performance (win %.1f%%, %d consec losses), tightening thresholds",
            win_rate,
            consec_losses,
        )

    # Mediocre performance: slight relaxation
    elif win_rate < 55:
        adjustments["zone_score"] = 0.95
        adjustments["trade_score"] = 0.95
        adjustments["confidence"] = 0.95
        adjustments["risk_reward"] = 0.95
        logger.info("adaptive: mediocre win rate %.1f%%, slight relaxation", win_rate)

    return adjustments


def get_adaptive_thresholds(days: int = 7) -> dict[str, Any]:
    """Get adaptive thresholds based on recent trading performance.

    Returns dict with adjusted threshold values ready to use in evaluate_strategy().
    """
    settings = get_settings()
    strat_cfg = settings.strategy
    risk_cfg = settings.risk

    # Base thresholds from config
    base_zone = strat_cfg.min_zone_score
    base_trade = strat_cfg.min_trade_score
    base_conf = strat_cfg.min_confidence_percent
    base_rr = risk_cfg.min_risk_reward

    # Analyze recent performance
    trades = _query_journal(days=days)
    perf = _analyze_performance(trades)

    # Calculate adjustments
    adj = _calculate_adjustments(perf)

    # Apply adjustments
    adapted = {
        "min_zone_score": round(base_zone * adj["zone_score"], 1),
        "min_trade_score": round(base_trade * adj["trade_score"], 1),
        "min_confidence_percent": round(base_conf * adj["confidence"], 1),
        "min_risk_reward": round(base_rr * adj["risk_reward"], 2),
    }

    # Clamp to reasonable bounds
    adapted["min_zone_score"] = max(40, min(90, adapted["min_zone_score"]))
    adapted["min_trade_score"] = max(40, min(90, adapted["min_trade_score"]))
    adapted["min_confidence_percent"] = max(30, min(95, adapted["min_confidence_percent"]))
    adapted["min_risk_reward"] = max(1.5, min(5.0, adapted["min_risk_reward"]))

    # Save state for diagnostics
    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "performance": perf,
        "adjustments": adj,
        "base": {
            "zone_score": base_zone,
            "trade_score": base_trade,
            "confidence": base_conf,
            "risk_reward": base_rr,
        },
        "adapted": adapted,
    }
    _save_state(state)

    return adapted


def get_adaptive_state() -> dict[str, Any]:
    """Get the last computed adaptive state (for diagnostics)."""
    return _load_state()
