"""Learning loop: journal outcomes adjust the desk's pattern trust.

Every completed trade in the journal records its setup (double_bottom,
rising_channel, ...). This module aggregates net profitability and win rate per
setup and exposes an adjustment factor the trade desk multiplies into
its pattern analyst score:

    factor = clamp(0.5 .. 1.5)
             win_rate > 55% and >= min_trades  -> >1.0 (trusted)
             win_rate < 40% and >= min_trades  -> <1.0 (distrusted)

History is cached for 15 seconds and invalidated by SQLite/WAL changes.
Setups below the sample minimum return the neutral factor 1.0. Entry checks
pause a symbol/setup/direction after negative recent edge or a loss streak,
only with eight completed outcomes. Pauses expire from the last close.

The desk logs the active factors each cycle via `learning_snapshot()`
so the effects are auditable in desk_events.jsonl.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mt5_desk.learning")

MODULE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = MODULE_DIR / "memory"

_NEUTRAL = 1.0
_MIN_TRADES = 8           # below this sample size: no adjustment
_WIN_TRUST = 55.0         # win% at or above: boost
_WIN_DISTRUST = 40.0     # win% at or below: cut
_FLOOR, _CAP = 0.5, 1.5   # factor clamp

_CACHE_TTL = 15
LOOKBACK_DAYS = 30
LOSS_STREAK = 3
LOSS_COOLDOWN_HOURS = 6
EDGE_COOLDOWN_HOURS = 24
MIN_PROFIT_FACTOR = 0.8
_history_cache: dict[str, Any] = {}
_history_lock = threading.Lock()


def normalize_setup(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key.startswith(("desk:", "desk-")):
        return "desk:" + key[5:][:18]
    if key.startswith("confirm_exec"):
        return "confirm_exec"
    if "spike-drift" in key:
        return "boom_crash_spike_drift"
    if "range breakout" in key:
        return "forex_zone_breakout"
    return key


def _stamp(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _closed_rows(force: bool = False) -> list[dict[str, Any]]:
    """Read completed outcomes only; reload immediately when SQLite/WAL changes."""
    from mt5_mcp.config import get_settings

    path = Path(get_settings().memory.db_path).resolve()
    signature = [str(path)]
    for file in (path, Path(str(path) + "-wal")):
        try:
            stat = file.stat()
            signature.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            signature.append(None)
    now = time.monotonic()
    with _history_lock:
        if not force and signature == _history_cache.get("signature") and now - _history_cache.get("ts", 0) < _CACHE_TTL:
            return _history_cache["rows"]
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(
                "SELECT ticket,symbol,side,setup,open_time,close_time,pnl,outcome,reason "
                "FROM trades WHERE pnl IS NOT NULL AND close_time IS NOT NULL "
                "AND outcome IN ('win','loss','breakeven') "
                "ORDER BY julianday(close_time) DESC, id DESC LIMIT 5000"
            )]
        _history_cache.update(signature=signature, ts=now, rows=rows)
        return rows


def _completed(rows: list[dict[str, Any]], now: datetime, days: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    since = now - timedelta(days=days)
    completed = []
    seen = set()
    for row in rows:
        opened, closed = _stamp(row.get("open_time")), _stamp(row.get("close_time"))
        setup = normalize_setup(row.get("setup"))
        try:
            pnl = float(row.get("pnl"))
        except (TypeError, ValueError):
            continue
        if not opened or not closed or not since <= closed <= now or closed < opened or not math.isfinite(pnl):
            continue
        if not setup or setup in {"manual", "reconcile", "unknown"}:
            continue
        outcome = str(row.get("outcome") or "").lower()
        expected = "win" if pnl > 0.01 else "loss" if pnl < -0.01 else "breakeven"
        if outcome != expected or "pnl from last tick" in str(row.get("reason") or "").lower():
            continue
        ticket = row.get("ticket")
        if ticket and ticket in seen:
            continue
        if ticket:
            seen.add(ticket)
        completed.append({**row, "setup": setup, "pnl": pnl, "closed": closed,
                          "symbol": str(row.get("symbol") or "").upper(),
                          "side": str(row.get("side") or "").upper()})
    return sorted(completed, key=lambda row: row["closed"], reverse=True)


def _evidence(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    pnl = [row["pnl"] for row in rows]
    gross_win = sum(max(value, 0.0) for value in pnl)
    gross_loss = sum(max(-value, 0.0) for value in pnl)
    net = sum(pnl)
    streak = 0
    for value in pnl:
        if value >= -0.01:
            break
        streak += 1
    pf = gross_win / gross_loss if gross_loss else None
    reasons = []
    pauses = []
    if rows:
        latest = rows[0]["closed"]
        if len(rows) >= _MIN_TRADES and streak >= LOSS_STREAK:
            until = latest + timedelta(hours=LOSS_COOLDOWN_HOURS)
            if now < until:
                reasons.append(f"{streak} consecutive losses; cooling down")
                pauses.append(until)
        if len(rows) >= _MIN_TRADES and gross_loss > 0 and net < 0 and pf < MIN_PROFIT_FACTOR:
            until = latest + timedelta(hours=EDGE_COOLDOWN_HOURS)
            if now < until:
                reasons.append(f"negative recent edge: {len(rows)} trades, net {net:+.2f}, PF {pf:.2f}")
                pauses.append(until)
    return {"allowed": not reasons, "reasons": reasons, "trades": len(rows),
            "wins": sum(value > 0.01 for value in pnl), "net_pnl": round(net, 2),
            "profit_factor": round(pf, 3) if pf is not None else None,
            "expectancy": round(net / len(rows), 2) if rows else None,
            "consecutive_losses": streak,
            "paused_until": max(pauses).isoformat() if pauses else None}


def check_entry(symbol: str, side: str, setup: str = "", *, now: datetime | None = None,
                rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Learned abstention for one cohort; never used to gate exits or raise risk."""
    now = now or datetime.now(timezone.utc)
    key = normalize_setup(setup)
    try:
        history = _completed(_closed_rows() if rows is None else rows, now)
    except Exception as exc:
        logger.warning("learning evidence unavailable: %s", exc)
        return {"allowed": False, "violations": ["learning: completed-trade evidence unavailable"],
                "status": "unavailable", "trades": 0, "setup": key}
    scoped = [row for row in history if row["symbol"] == symbol.upper() and row["side"] == side.upper()
              and row["setup"] == key]
    result = _evidence(scoped, now)
    from mt5_mcp.entry_quality import enabled as quality_enabled
    if quality_enabled():
        try:
            from mt5_mcp.paper_learning import requalification
            paper = requalification(symbol.upper(), side.upper(), key, result,
                                    scoped[0]["closed"] if scoped else None, now)
            result["paper"] = paper
            if paper["required"]:
                result["allowed"] = False
                result["reasons"] = ["paper requalification required"]
                result["paused_until"] = paper.get("paused_until")
            elif paper.get("paper_ready"):
                result["allowed"] = True
                result["reasons"] = []
        except Exception as exc:
            logger.warning("paper qualification unavailable: %s", exc)
            result["allowed"] = False
            result["reasons"] = ["paper qualification evidence unavailable"]
    result.update(symbol=symbol.upper(), side=side.upper(), setup=key,
                  status="paper_only" if result.get("paper", {}).get("required") else "paused" if not result["allowed"] else "insufficient_sample" if len(scoped) < _MIN_TRADES else "eligible",
                  violations=[f"learning: {symbol.upper()} {side.upper()} {key}: {reason}; until {result['paused_until']}"
                              for reason in result["reasons"]])
    return result


def _journal_setups(symbol: str = "", force: bool = False) -> dict[str, dict[str, float]]:
    """setup -> {trades, wins, pnl} from the closed-trade journal."""
    try:
        rows = _completed(_closed_rows(force=force), datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("journal read failed: %s", exc)
        return {}

    stats: dict[str, dict[str, float]] = {}
    for row in rows or []:
        if symbol and row["symbol"] != symbol.upper():
            continue
        setup = normalize_setup(row.get("setup"))
        if setup.startswith("desk:"):
            setup = setup[5:]
        pnl = row.get("pnl")
        if not setup or setup in ("manual", "") or pnl is None:
            continue
        rec = stats.setdefault(setup, {"trades": 0, "wins": 0, "pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0})
        rec["trades"] += 1
        rec["pnl"] += float(pnl or 0.0)
        if float(pnl or 0.0) > 0:
            rec["wins"] += 1
            rec["gross_win"] += float(pnl)
        else:
            rec["gross_loss"] += abs(float(pnl))
    return stats


def _factor(trades: int, win_rate: float, net_pnl: float = 0.0) -> float:
    """Never boost a losing setup or penalize a profitable low-win-rate setup."""
    if trades < _MIN_TRADES:
        return _NEUTRAL
    if net_pnl < 0:
        return max(_FLOOR, min(0.95, 1.0 - max(0.0, _WIN_DISTRUST - win_rate) / 15.0 * 0.45))
    if net_pnl == 0:
        return _NEUTRAL
    if win_rate >= _WIN_TRUST:
        # scale 55%->1.05, 75%+->1.5
        raw = 1.0 + (win_rate - _WIN_TRUST) / 20.0 * 0.45
    else:
        return _NEUTRAL
    return max(_FLOOR, min(_CAP, raw))


def compute_factors(force: bool = False, symbol: str = "") -> dict[str, float]:
    """setup -> trust factor (cached)."""
    factors: dict[str, float] = {}
    for setup, rec in _journal_setups(symbol, force=force).items():
        trades = int(rec["trades"])
        win_rate = (rec["wins"] / trades * 100.0) if trades else 0.0
        factors[setup] = round(_factor(trades, win_rate, rec["pnl"]), 3)
    return factors


def adjust_pattern_score(setup: str, score: float, symbol: str = "") -> tuple[float, float]:
    """Apply the learned factor to a pattern score.

    Returns (adjusted_score, factor_applied). The setup name is matched
    case-insensitively; desk setups like "desk:double_bottom" (journal
    format) map to the same key as the desk's "double_bottom".
    """
    key = normalize_setup(setup)
    if key.startswith("desk:"):
        key = key[5:]
    factors = compute_factors(symbol=symbol)
    factor = factors.get(key, _NEUTRAL)
    adjusted = max(0.0, min(100.0, score * factor))
    return round(adjusted, 2), factor


def learning_snapshot() -> dict[str, Any]:
    """Auditable view of current learning state (for desk events)."""
    stats = _journal_setups()
    factors = compute_factors()
    view = {}
    for setup, rec in stats.items():
        trades = int(rec["trades"])
        view[setup] = {
            "trades": trades,
            "win_rate": round(rec["wins"] / trades * 100, 1) if trades else 0.0,
            "net_pnl": round(rec["pnl"], 2),
            "factor": factors.get(setup, _NEUTRAL),
            "sample_ok": trades >= _MIN_TRADES,
        }
    now = datetime.now(timezone.utc)
    try:
        history = _completed(_closed_rows(), now)
    except Exception:
        return {"available": False, "min_trades": _MIN_TRADES, "setups": view, "cohorts": []}
    groups: dict[tuple, list[dict]] = {}
    for row in history:
        groups.setdefault((row["symbol"], row["side"], row["setup"]), []).append(row)
    cohorts = [{"symbol": symbol, "side": side, "setup": setup, **_evidence(rows, now)}
               for (symbol, side, setup), rows in sorted(groups.items())]
    return {"available": True, "min_trades": _MIN_TRADES, "lookback_days": LOOKBACK_DAYS,
            "loss_streak": LOSS_STREAK, "loss_cooldown_hours": LOSS_COOLDOWN_HOURS,
            "edge_cooldown_hours": EDGE_COOLDOWN_HOURS, "setups": view, "cohorts": cohorts}
