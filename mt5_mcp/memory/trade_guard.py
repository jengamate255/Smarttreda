"""Data-driven pre-trade guardrails learned from journaled outcomes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from mt5_mcp.memory.trade_journal import get_journal


SETUP = "boom_crash_spike_drift"


def _tokens(value: Any) -> set[str]:
    return {
        token.strip().lower()
        for token in re.split(r"[,;|]", str(value or ""))
        if token.strip()
    }


def _is_watcher_trade(trade: dict[str, Any]) -> bool:
    setup = str(trade.get("setup") or "").lower()
    reason = str(trade.get("reason") or "").lower()
    symbol = str(trade.get("symbol") or "").upper()
    return setup == SETUP or (
        symbol.startswith(("BOOM", "CRASH"))
        and "spike" in reason
    )


def _closed_after(trade: dict[str, Any], since: datetime) -> bool:
    raw = trade.get("close_time") or trade.get("created_at")
    if not raw:
        return False
    try:
        value = datetime.fromisoformat(str(raw))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value >= since
    except ValueError:
        return False


def evaluate(
    symbol: str,
    *,
    base_max_spike_age: int = 15,
    base_confidence_floor: float = 90.0,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Return learned limits for a proposed Boom/Crash watcher signal.

    A single loss tightens the relevant rule. Repeated losses on one symbol
    temporarily block that symbol. This avoids overfitting to one outcome
    while still making a repeated mistake harder to repeat.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    trades = [
        trade
        for trade in get_journal().list_trades(limit=5000)
        if _is_watcher_trade(trade) and _closed_after(trade, since)
    ]
    losses = [trade for trade in trades if float(trade.get("pnl") or 0.0) < 0.0]
    symbol_losses = [
        trade for trade in losses if str(trade.get("symbol") or "").upper() == symbol.upper()
    ]
    scoped_losses = symbol_losses or (
        losses
        if sum("stale_spike_age" in _tokens(trade.get("mistakes")) for trade in losses) >= 2
        else []
    )
    stale = sum("stale_spike_age" in _tokens(trade.get("mistakes")) for trade in scoped_losses)
    early = sum("entered_before_confirmation" in _tokens(trade.get("mistakes")) for trade in scoped_losses)
    cluster = sum("ignored_spike_clustering" in _tokens(trade.get("mistakes")) for trade in scoped_losses)

    max_age = int(base_max_spike_age)
    confidence_floor = float(base_confidence_floor)
    violations: list[str] = []
    if stale:
        max_age = min(max_age, 10 if stale < 2 else 8)
        violations.append(f"learning: {stale} stale-spike loss(es), age cap is {max_age} bars")
    if early:
        confidence_floor = min(95.0, max(confidence_floor, base_confidence_floor + 5.0))
        violations.append(f"learning: {early} early-entry loss(es), confidence floor is {confidence_floor:.0f}%")
    if cluster:
        max_age = min(max_age, 10)
        violations.append(f"learning: {cluster} spike-cluster loss(es), age cap is {max_age} bars")

    blocked = len(symbol_losses) >= 2
    if blocked:
        violations.append(
            f"learning: {len(symbol_losses)} recent losses on {symbol.upper()}, symbol paused for review"
        )

    try:
        from mt5_mcp.self_tune import current_symbol

        learned = current_symbol(symbol)
        if learned.get("enabled") is False:
            blocked = True
            violations.append(
                f"learning: {symbol.upper()} disabled by self-tuning (negative edge)"
            )
        else:
            max_age = min(max_age, int(learned["max_spike_age"]))
            confidence_floor = max(
                confidence_floor, float(learned["confidence_floor"])
            )
        self_tuned = True
    except Exception:  # noqa: BLE001 - tuning must never break the gate
        self_tuned = False
    return {
        "allowed": not blocked,
        "max_spike_age": max_age,
        "confidence_floor": confidence_floor,
        "violations": violations,
        "evidence": {
            "trades": len(trades),
            "losses": len(losses),
            "symbol_losses": len(symbol_losses),
            "stale_spike_losses": stale,
            "early_entry_losses": early,
            "cluster_losses": cluster,
            "self_tuned": self_tuned,
        },
    }


def annotate_closed_trades(max_spike_age: int = 15) -> int:
    """Add structured lessons to unannotated watcher losses."""
    journal = get_journal()
    updated = 0
    for trade in journal.list_trades(limit=5000):
        if not _is_watcher_trade(trade) or float(trade.get("pnl") or 0.0) >= 0.0:
            continue
        mistakes = _tokens(trade.get("mistakes"))
        lessons = _tokens(trade.get("lessons"))
        if mistakes and lessons:
            continue
        conditions = str(trade.get("market_conditions") or "")
        age_match = re.search(r"spike_age(?:_candles|_bars)?=(\d+)", conditions)
        age = int(age_match.group(1)) if age_match else None
        if age is None or age > max_spike_age:
            mistakes.add("stale_spike_age")
            lessons.add(f"wait for a fresh spike no older than {max_spike_age} M1 bars")
        else:
            mistakes.add("entered_before_confirmation")
            lessons.add("wait for pullback confirmation before placing the stop")
        if trade.get("duration_seconds") and int(trade["duration_seconds"]) < 900:
            mistakes.add("ignored_spike_clustering")
            lessons.add("avoid entries immediately before a likely continuation spike")
        journal.update_notes(
            int(trade["ticket"]),
            mistakes=";".join(sorted(mistakes)),
            lessons=";".join(sorted(lessons)),
        )
        updated += 1
    return updated
