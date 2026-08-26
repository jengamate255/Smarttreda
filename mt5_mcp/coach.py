"""AI Coach.

Post-trade reviews for every closed trade. For a ticket (or the most recent
closed trade) the coach explains:

* why the trade was taken - the journaled reason, setup, confidence and score
* evidence that supported it - score/confidence vs thresholds, session, notes
* evidence that argued against it - sub-threshold scores, missing reason,
  journaled mistakes, weak session, loss outcome
* what could have been improved - journaled lessons/mistakes plus structural
  flags derived from the entry/SL/TP/exit geometry
* how similar historical trades performed - matched on symbol, side, setup,
  session hour, RR and score, with win rate and expectancy

The coach never executes anything; it is the learning layer on top of the
journal that turns every trade into a reviewable lesson.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from mt5_mcp.config import get_settings
from mt5_mcp.memory.trade_journal import get_journal

logger = logging.getLogger("mt5_mcp.coach")

MATCH_WEIGHTS = {
    "symbol": 25.0,
    "side": 20.0,
    "setup": 15.0,
    "session": 10.0,
    "rr": 10.0,
    "score": 10.0,
    "zone": 10.0,
}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hour(trade: dict) -> int | None:
    raw = trade.get("close_time") or trade.get("created_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.hour
    except ValueError:
        return None


def similarity(target: dict, other: dict) -> float:
    """How closely `other` resembles `target`, 0-100."""
    score = 0.0
    if str(target.get("symbol") or "").upper() == str(other.get("symbol") or "").upper():
        score += MATCH_WEIGHTS["symbol"]
    if str(target.get("side") or "").upper() == str(other.get("side") or "").upper():
        score += MATCH_WEIGHTS["side"]
    t_setup = str(target.get("setup") or "").strip()
    o_setup = str(other.get("setup") or "").strip()
    if t_setup and t_setup == o_setup:
        score += MATCH_WEIGHTS["setup"]

    t_hour, o_hour = _hour(target), _hour(other)
    if t_hour is not None and o_hour is not None and abs(t_hour - o_hour) <= 1:
        score += MATCH_WEIGHTS["session"]

    t_rr, o_rr = _num(target.get("rr")), _num(other.get("rr"))
    if t_rr is not None and o_rr is not None and abs(t_rr - o_rr) <= 0.75:
        score += MATCH_WEIGHTS["rr"]

    t_score, o_score = _num(target.get("trade_score")), _num(other.get("trade_score"))
    if t_score is not None and o_score is not None and abs(t_score - o_score) <= 15.0:
        score += MATCH_WEIGHTS["score"]

    t_zone, o_zone = _num(target.get("zone_strength")), _num(other.get("zone_strength"))
    if t_zone is not None and o_zone is not None and abs(t_zone - o_zone) <= 15.0:
        score += MATCH_WEIGHTS["zone"]

    return round(score, 1)


def similar_trades(target: dict, limit: int = 8, min_similarity: float = 35.0) -> dict:
    """Prior closed trades ranked by similarity to the target, with stats."""
    journal = get_journal()
    target_close = target.get("close_time") or target.get("created_at") or "9999"
    pool = [
        t
        for t in journal.list_trades(limit=5000)
        if t.get("id") != target.get("id") and (t.get("close_time") or t.get("created_at") or "") <= target_close
    ]
    ranked = sorted(pool, key=lambda t: -similarity(target, t))
    best = [t for t in ranked if similarity(target, t) >= min_similarity][:limit]
    if len(best) < 3:
        best = ranked[:limit]

    rows = [
        {
            "ticket": t.get("ticket"),
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "setup": t.get("setup"),
            "close_time": t.get("close_time") or t.get("created_at"),
            "pnl": t.get("pnl"),
            "outcome": t.get("outcome"),
            "rr": t.get("rr"),
            "confidence": t.get("confidence"),
            "similarity": similarity(target, t),
        }
        for t in best
    ]
    pnls = [_num(t.get("pnl")) for t in best]
    pnls = [p for p in pnls if p is not None]
    wins = [p for p in pnls if p > 0]
    return {
        "count": len(rows),
        "win_rate": round(len(wins) / len(pnls) * 100.0, 1) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "trades": rows,
    }


def _thresholds() -> dict[str, float]:
    settings = get_settings()
    return {
        "min_confidence": settings.strategy.min_confidence_percent,
        "min_trade_score": settings.strategy.min_trade_score,
        "min_zone_score": settings.strategy.min_zone_score,
        "confidence_floor": settings.risk.confidence_floor_percent,
        "min_rr": settings.risk.min_risk_reward,
    }


def _evidence(trade: dict, thresholds: dict[str, float]) -> tuple[list[str], list[str]]:
    for_evidence: list[str] = []
    against: list[str] = []

    confidence = _num(trade.get("confidence"))
    score = _num(trade.get("trade_score"))
    zone = _num(trade.get("zone_strength"))
    rr = _num(trade.get("rr"))

    if confidence is not None:
        if confidence >= thresholds["confidence_floor"]:
            for_evidence.append(f"confidence {confidence:.0f}% at or above the A+ floor "
                                f"({thresholds['confidence_floor']:.0f}%)")
        if confidence < thresholds["min_confidence"]:
            against.append(f"confidence {confidence:.0f}% below the strategy minimum "
                           f"({thresholds['min_confidence']:.0f}%)")
    else:
        against.append("no confidence score journaled - enter it next time")

    if score is not None:
        if score >= thresholds["min_trade_score"]:
            for_evidence.append(f"trade score {score:.0f} passes the {thresholds['min_trade_score']:.0f} bar")
        else:
            against.append(f"trade score {score:.0f} below {thresholds['min_trade_score']:.0f}")

    if zone is not None:
        if zone >= thresholds["min_zone_score"]:
            for_evidence.append(f"zone strength {zone:.0f} passes the {thresholds['min_zone_score']:.0f} bar")
        else:
            against.append(f"zone strength {zone:.0f} below {thresholds['min_zone_score']:.0f}")

    if rr is not None and rr >= thresholds["min_rr"]:
        for_evidence.append(f"planned R:R {rr:.2f} meets the {thresholds['min_rr']:.1f} minimum")
    elif rr is not None:
        against.append(f"planned R:R {rr:.2f} below the {thresholds['min_rr']:.1f} minimum")

    if trade.get("reason"):
        for_evidence.append("entry reason journaled - the trade was a decision, not a guess")
    else:
        against.append("no entry reason journaled - the motivation behind this trade is unknown")

    if trade.get("market_conditions"):
        for_evidence.append(f"market context recorded: {trade['market_conditions']}")

    session = f"{int(trade['session_hour']):02d}:00 UTC" if trade.get("session_hour") is not None else None
    if session:
        for_evidence.append(f"entered during the {session} session")
    else:
        against.append("no session recorded")

    return for_evidence, against


def _improvement(trade: dict, similar: dict) -> list[str]:
    out: list[str] = []
    for lesson in str(trade.get("lessons") or "").replace(";", ",").replace("|", ",").split(","):
        if lesson.strip():
            out.append(f"lesson learned: {lesson.strip()}")
    for mistake in str(trade.get("mistakes") or "").replace(";", ",").replace("|", ",").split(","):
        if mistake.strip():
            out.append(f"mistake to avoid next time: {mistake.strip()}")
    if not (trade.get("mistakes") or trade.get("lessons")):
        out.append("journal the mistakes and lessons for this trade")

    if not trade.get("screenshot_path"):
        out.append("attach a screenshot so the setup can be reviewed visually")

    entry = _num(trade.get("entry"))
    exit_price = _num(trade.get("exit_price"))
    sl = _num(trade.get("sl"))
    tp = _num(trade.get("tp"))
    pnl = _num(trade.get("pnl"))
    side = str(trade.get("side") or "").upper()

    if pnl is not None and entry is not None and exit_price is not None:
        direction = 1.0 if side == "BUY" else -1.0
        if tp is not None:
            tp_r = direction * (tp - entry) / abs(exit_price - entry) if exit_price != entry else 0.0
            if pnl > 0 and tp_r < 1.0:
                out.append("exited before the take-profit - the full target was not reached; "
                           "consider letting winners run or trailing the stop")
        if sl is not None and pnl < 0:
            hit_sl = direction * (exit_price - entry) < 0 and abs(direction * (exit_price - sl)) <= 0.0005
            if hit_sl:
                out.append("stopped out: check whether the stop was inside normal noise or the "
                           "entry was too early - a later entry or wider stop around structure helps")

    if similar.get("count") >= 3 and similar["win_rate"] < 40.0:
        out.append(
            f"similar setups historically win only {similar['win_rate']:.0f}% - treat this "
            "family of trades with extra caution or filter them out"
        )
    if similar.get("count") >= 3 and similar["expectancy"] < 0:
        out.append(f"similar trades average {similar['expectancy']:+.2f} per trade - this "
                   "setup family is currently a losing proposition")

    return out


def coach_review(ticket: int | None = None) -> dict:
    """Full post-trade review. Without a ticket, reviews the most recent trade."""
    journal = get_journal()
    trade: dict | None = None
    if ticket is not None:
        trade = journal.get_trade(ticket)
        if trade is None:
            return {
                "error": f"trade ticket {ticket} not found in the journal - run sync_journal first",
                "ticket": ticket,
            }
    else:
        candidates = journal.list_trades(limit=1)
        trade = candidates[0] if candidates else None
        if trade is None:
            return {
                "error": "journal is empty - run sync_journal or record trades first",
            }

    similar = similar_trades(trade)
    thresholds = _thresholds()
    for_evidence, against = _evidence(trade, thresholds)
    improvement = _improvement(trade, similar)

    outcome = trade.get("outcome") or ("win" if _num(trade.get("pnl")) and _num(trade.get("pnl")) > 0 else "loss")
    session = (
        f"{int(trade['session_hour']):02d}:00 UTC"
        if trade.get("session_hour") is not None
        else None
    )

    why_taken = str(trade.get("reason") or "").strip()
    if not why_taken:
        why_taken = (
            f"{trade.get('setup') or 'manual'} trade on {trade.get('symbol')} "
            "with no journaled reason"
        )

    if outcome == "win":
        verdict = "the decision was validated - review what worked and repeat it deliberately"
    else:
        verdict = "the trade lost - separate bad luck from bad process using the evidence below"

    return {
        "ticket": trade.get("ticket"),
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "setup": trade.get("setup"),
        "timeframe": trade.get("timeframe"),
        "entry": trade.get("entry"),
        "sl": trade.get("sl"),
        "tp": trade.get("tp"),
        "exit_price": trade.get("exit_price"),
        "pnl": trade.get("pnl"),
        "outcome": outcome,
        "rr": trade.get("rr"),
        "confidence": trade.get("confidence"),
        "trade_score": trade.get("trade_score"),
        "zone_strength": trade.get("zone_strength"),
        "session": session,
        "open_time": trade.get("open_time"),
        "close_time": trade.get("close_time"),
        "duration_seconds": trade.get("duration_seconds"),
        "why_taken": why_taken,
        "verdict": verdict,
        "evidence_for": for_evidence,
        "evidence_against": against,
        "improvement": improvement,
        "similar_trades": similar,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
