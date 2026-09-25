"""Prop firm compliance agent: veto gate, mode tiers, dashboard.

The compliance agent has veto power over every trade. Rules enforced here:

* per-trade risk cap (0.5% normal / 0.25% conservative)
* total floating risk cap (1% of equity)
* correlation groups - correlated exposure counted as one combined risk
* daily loss protection (80% -> conservative mode, 100% -> halt until
  the next trading day)
* maximum drawdown protection (tiered -> halt before breach)
* news blackout around high-impact events
* spread protection
* liquidity blackout (rollover/thin-market window)
* revenge trading protection (consecutive-loss pause + cooldown)
* trade frequency cap per day
* confidence floor for strategy-grade setups

Mode is persisted to memory/compliance_state.json and resets each new day.
On halt, all pending orders are cancelled and the event is journaled.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mt5_mcp.client import get_client
from mt5_mcp.common import TRADE_ACTION_REMOVE, pip_size, pip_value_per_lot
from mt5_mcp.config import get_settings
from mt5_mcp.tools.news import get_provider

logger = logging.getLogger("mt5_mcp")

MAGIC = int(os.getenv("MT5_MCP_MAGIC", "20260601"))
STATE_PATH = Path(__file__).resolve().parent / "memory" / "compliance_state.json"

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1


def is_synthetic_symbol(symbol: str) -> bool:
    """Economic news does not affect Deriv synthetic-index price feeds."""
    name = str(symbol or "").upper().replace(" ", "")
    return any(
        marker in name
        for marker in (
            "BOOM",
            "CRASH",
            "VOLATILITY",
            "1HZ",
            "R_",
            "STEP",
            "JUMP",
            "DRIFT",
            "RANGE",
            "BULL",
            "BEAR",
        )
    )

CORRELATION_GROUPS: dict[str, str] = {
    "EURUSD": "euro",
    "GBPUSD": "euro",
    "XAUUSD": "metals",
    "XAGUSD": "metals",
    "USDJPY": "jpy",
    "EURJPY": "jpy",
    "GBPJPY": "jpy",
    "CHFJPY": "jpy",
    "JP225": "jpy",
    "US30": "us-indices",
    "NAS100": "us-indices",
    "SPX500": "us-indices",
    "US500": "us-indices",
    "US100": "us-indices",
    "USTEC": "us-indices",
    "DJI30": "us-indices",
    "NDX100": "us-indices",
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
    "AUDUSD": "aussie",
    "NZDUSD": "aussie",
    "USDCHF": "swiss",
    "EURCHF": "swiss",
    "USDCAD": "loonie",
    "EURCAD": "loonie",
    "GBPCAD": "loonie",
    "GER40": "eu-indices",
    "FTSE100": "eu-indices",
    "STX50": "eu-indices",
    "UKOIL": "energy",
    "USOIL": "energy",
}

# Hard USD-correlation caps used by the trade desk's portfolio constructor.
# Each desk position is treated as one unit of DESK_RISK_FIXED dollars, so
# these caps are expressed in the same dollar units (default: 3 x $50).
MAX_GROUP_RISK_USD = float(os.getenv("MT5_MAX_GROUP_RISK_USD", "150"))
MAX_USD_EXPOSURE_RISK = float(os.getenv("MT5_MAX_USD_EXPOSURE_RISK", "150"))

# Symbols whose P&L is primarily a USD-strength/weakness bet. Stacking
# same-direction trades across these is one macro bet, not diversification.
USD_EXPOSURE_MEMBERS = frozenset({
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD",
    "USDCAD", "USDCHF", "XAUUSD", "XAGUSD",
    "USOIL", "UKOIL", "SPX500", "US500", "NAS100", "US100",
})


def _day_stamp() -> str:
    return time.strftime("%Y-%m-%d")


def _day_start_epoch() -> int:
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))


def _default_state() -> dict:
    return {
        "day_stamp": _day_stamp(),
        "peak_equity": 0.0,
        "mode": "normal",
        "halt_reason": "",
        "revenge_paused_until": 0.0,
    }


def _load_state(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _default_state()
                base.update({k: v for k, v in data.items() if k in base})
                return base
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read compliance state at %s", path)
    return _default_state()


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("could not write compliance state at %s", path)


def _roll_day(state: dict) -> bool:
    """Reset day-scoped controls when the trading day changes."""
    today = _day_stamp()
    if state["day_stamp"] == today:
        return False
    state["day_stamp"] = today
    state["mode"] = "normal"
    state["halt_reason"] = ""
    state["revenge_paused_until"] = 0.0
    return True


def _record_event(kind: str, message: str) -> None:
    try:
        from memory.trade_journal import get_journal

        get_journal().record_event(kind, message)
    except Exception:
        logger.debug("compliance event not journaled (%s: %s)", kind, message)


def _cancel_pendings(client) -> int:
    cancelled = 0
    try:
        pendings = client.pending_orders() or []
    except Exception:
        return 0
    for order in pendings:
        if int(order.get("magic") or 0) != MAGIC:
            continue
        try:
            client.order_send(
                {"action": TRADE_ACTION_REMOVE, "order": int(order.get("ticket") or 0)}
            )
            cancelled += 1
        except Exception as exc:
            logger.warning("failed to cancel pending %s: %s", order.get("ticket"), exc)
    return cancelled


def _daily_pnl(client, day_start: int) -> float:
    total = 0.0
    for deal in client.deals_since(day_start) or []:
        if int(deal.get("type") or 0) in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
            total += float(deal.get("profit") or 0.0) + float(
                deal.get("swap") or 0.0
            ) + float(deal.get("fee") or 0.0)
    return total


def _trades_today(client, day_start: int) -> int:
    return sum(
        1
        for deal in client.deals_since(day_start) or []
        if int(deal.get("entry") or 0) == DEAL_ENTRY_IN
        and int(deal.get("type") or 0) in (DEAL_TYPE_BUY, DEAL_TYPE_SELL)
    )


def _consecutive_losses(client, day_start: int) -> int:
    closes: dict[int, list[float]] = {}
    times: dict[int, int] = {}
    for deal in client.deals_since(day_start) or []:
        if int(deal.get("entry") or 0) != DEAL_ENTRY_OUT:
            continue
        if int(deal.get("type") or 0) not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
            continue
        position = int(deal.get("position_id") or 0)
        pnl = float(deal.get("profit") or 0.0) + float(
            deal.get("swap") or 0.0
        ) + float(deal.get("fee") or 0.0)
        closes.setdefault(position, []).append(pnl)
        times[position] = max(times.get(position, 0), int(deal.get("time") or 0))
    ordered = sorted(closes.items(), key=lambda item: times[item[0]])
    streak = 0
    for _, pnls in reversed(ordered):
        if sum(pnls) < 0:
            streak += 1
        else:
            break
    return streak


def _position_risk(info: dict, position: dict) -> float:
    pip = pip_size(info)
    volume = float(position.get("volume") or 0.0)
    sl = float(position.get("sl") or 0.0)
    price = float(position.get("price_open") or 0.0)
    if not sl or not pip:
        return 0.0
    return abs(price - sl) / pip * pip_value_per_lot(info) * volume


def _open_risk(client) -> float:
    total = 0.0
    for position in client.positions() or []:
        try:
            info = client.symbol_info(str(position.get("symbol", "")))
        except Exception:
            continue
        if info:
            total += _position_risk(info, position)
    return total


def _group_risks(client) -> dict[str, float]:
    groups: dict[str, float] = {}
    for position in client.positions() or []:
        symbol = str(position.get("symbol", ""))
        group = CORRELATION_GROUPS.get(symbol)
        if group is None:
            continue
        try:
            info = client.symbol_info(symbol)
        except Exception:
            continue
        if info:
            groups[group] = groups.get(group, 0.0) + _position_risk(info, position)
    return groups


def _peak_equity(state: dict, equity: float) -> float:
    if equity > state["peak_equity"]:
        state["peak_equity"] = equity
    return state["peak_equity"]


def _in_news_blackout(settings) -> bool:
    window = timedelta(minutes=settings.news_blackout_minutes)
    now = datetime.now(timezone.utc)
    for event in get_provider().upcoming(hours=24) or []:
        if event.impact != "high":
            continue
        event_time = getattr(event, "time", None)
        if event_time is None:
            continue
        if abs(event_time - now) <= window:
            return True
    return False


def _server_now(client) -> datetime:
    if hasattr(client, "server_time"):
        try:
            now = client.server_time()
            if isinstance(now, datetime) and now.year >= 2020:
                return now
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _in_liquidity_blackout(client, settings) -> bool:
    start = settings.liquidity_blackout_start
    end = settings.liquidity_blackout_end
    if not start or not end:
        return False
    now = _server_now(client).strftime("%H:%M")
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _resolve_mode(
    state: dict, settings, daily_used: float, dd_pct: float
) -> tuple[str, str]:
    if daily_used >= 1.0:
        return "halted", "daily loss limit reached"
    if dd_pct >= settings.max_drawdown_percent * settings.drawdown_halt_ratio:
        return "halted", "maximum drawdown approached"
    if daily_used >= settings.daily_conservative_ratio:
        return "conservative", "daily loss at 80% of limit"
    if dd_pct >= settings.max_drawdown_percent * settings.drawdown_conservative_ratio:
        return "conservative", "drawdown at 80% of limit"
    return "normal", ""


def evaluate(state_path: Path | None = None) -> dict:
    """Full compliance dashboard. Detects mode transitions and halts."""
    state_path = state_path or STATE_PATH
    client = get_client()
    settings = get_settings().risk
    state = _load_state(state_path)
    rolled = _roll_day(state)
    account = client.account_info()
    equity = float(account.get("equity") or 0.0)
    balance = float(account.get("balance") or 0.0)
    day_start = _day_start_epoch()

    floating = sum(
        float(p.get("profit") or 0.0) for p in (client.positions() or [])
    )
    daily_pnl = _daily_pnl(client, day_start) + floating
    max_daily = equity * settings.max_daily_loss_percent / 100.0
    daily_used = max(0.0, -daily_pnl) / max_daily if max_daily else 0.0

    peak = _peak_equity(state, equity)
    dd_pct = (peak - equity) / peak * 100.0 if peak else 0.0

    prev_mode = state["mode"]
    if not rolled and state["mode"] == "halted":
        mode, halt_reason = "halted", state["halt_reason"]
    else:
        mode, halt_reason = _resolve_mode(state, settings, daily_used, dd_pct)
    newly_halted = mode == "halted" and prev_mode != "halted"
    state["mode"] = mode
    if mode == "halted":
        state["halt_reason"] = halt_reason
    elif mode != "halted":
        state["halt_reason"] = ""

    open_risk = _open_risk(client)
    groups = _group_risks(client)
    max_open = equity * settings.max_open_risk_percent / 100.0
    max_dd_allowed = equity * settings.max_drawdown_percent / 100.0
    dd_amount = peak - equity

    trades_today = _trades_today(client, day_start)
    losses = _consecutive_losses(client, day_start)
    if losses < settings.max_consecutive_losses and state["revenge_paused_until"]:
        state["revenge_paused_until"] = 0.0
    elif (
        losses >= settings.max_consecutive_losses
        and not state["revenge_paused_until"]
        and mode != "halted"
    ):
        state["revenge_paused_until"] = time.time() + settings.revenge_cooldown_minutes * 60
        _record_event(
            "revenge_pause",
            f"{losses} consecutive losses; trading paused for "
            f"{settings.revenge_cooldown_minutes:.0f} minutes",
        )

    # Flexible daily trade limit: the base limit is raised while the day is
    # green (positive realized+floating P&L). No bonus in conservative or
    # halted modes, so a losing day never unlocks extra trades.
    green_bonus = (
        settings.max_trades_per_day_green_bonus
        if daily_pnl > 0 and mode == "normal"
        else 0
    )
    effective_max_trades = settings.max_trades_per_day + green_bonus

    newly_halted = mode == "halted" and prev_mode != "halted"
    _save_state(state_path, state)

    if newly_halted:
        cancelled = _cancel_pendings(client)
        _record_event("halt", f"trading halted ({halt_reason}); cancelled {cancelled} pending")

    news_blocked = _in_news_blackout(settings)
    liquidity_blocked = _in_liquidity_blackout(client, settings)

    warnings: list[str] = []
    if daily_used >= settings.daily_conservative_ratio:
        warnings.append(
            f"daily loss buffer: {daily_used * 100:.0f}% of limit used - "
            "conservative mode (0.25% risk, A+ only)"
        )
    if dd_pct >= settings.max_drawdown_percent * settings.drawdown_conservative_ratio:
        warnings.append(
            f"drawdown buffer: {dd_pct:.1f}% of {settings.max_drawdown_percent:.1f}% "
            "limit used - reduce size and frequency"
        )
    if losses >= settings.max_consecutive_losses:
        warnings.append(f"{losses} consecutive losses - revenge pause active")

    color = "RED" if mode == "halted" else "YELLOW" if mode == "conservative" else "GREEN"
    if mode == "normal" and (news_blocked or liquidity_blocked):
        color = "YELLOW"

    per_trade_cap = (
        settings.conservative_risk_percent
        if mode == "conservative"
        else settings.max_risk_percent
    )

    return {
        "mode": mode,
        "halt_reason": halt_reason,
        "status": color,
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "floating_pnl": round(floating, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit": round(max_daily, 2),
        "daily_loss_percent_used": round(daily_used * 100.0, 1),
        "remaining_daily_buffer": round(max(0.0, max_daily + daily_pnl), 2),
        "peak_equity": round(peak, 2),
        "overall_drawdown_percent": round(dd_pct, 2),
        "max_drawdown_percent": settings.max_drawdown_percent,
        "remaining_drawdown_buffer": round(max(0.0, max_dd_allowed - dd_amount), 2),
        "open_risk": round(open_risk, 2),
        "max_open_risk": round(max_open, 2),
        "open_positions": len(client.positions() or []),
        "max_positions": settings.max_positions,
        "trades_today": trades_today,
        "max_trades_per_day": effective_max_trades,
        "max_trades_per_day_base": settings.max_trades_per_day,
        "max_trades_per_day_green_bonus": green_bonus,
        "consecutive_losses": losses,
        "max_consecutive_losses": settings.max_consecutive_losses,
        "revenge_paused_until": (
            datetime.fromtimestamp(state["revenge_paused_until"], timezone.utc)
            .isoformat()
            if state["revenge_paused_until"] > 0
            else None
        ),
        "revenge_paused_until_epoch": state["revenge_paused_until"],
        "correlation_exposure": {
            group: round(risk, 2) for group, risk in sorted(groups.items())
        },
        "per_trade_risk_cap_percent": per_trade_cap,
        "confidence_floor_percent": settings.confidence_floor_percent,
        "news_blocked": news_blocked,
        "liquidity_blocked": liquidity_blocked,
        "warnings": warnings,
        "trading_allowed": mode != "halted",
    }


def gate(
    symbol: str,
    side: str,
    lots: float,
    sl_price: float | None = None,
    sl_pips: float | None = None,
    confidence: float | None = None,
    entry_price: float | None = None,
    state_path: Path | None = None,
) -> dict:
    """Pre-trade veto gate. Returns verdict with violations; trade only if allowed."""
    state_path = state_path or STATE_PATH
    client = get_client()
    settings = get_settings().risk
    violations: list[str] = []
    warnings: list[str] = []

    try:
        info = client.symbol_info(symbol)
    except Exception:
        info = None
    if not info:
        violations.append(f"symbol '{symbol}' not found")
        return _verdict(violations, warnings, None)

    account = client.account_info()
    equity = float(account.get("equity") or 0.0)
    status = evaluate(state_path)

    # Blocked symbols check
    blocked = [s.upper() for s in settings.blocked_symbols]
    if symbol.upper() in blocked:
        violations.append(f"symbol '{symbol}' is on the blocked list")

    if status["mode"] == "halted":
        violations.append(f"trading halted: {status['halt_reason'] or 'compliance halt'}")
    if not status["trading_allowed"]:
        violations.append("compliance mode blocks trading")

    open_ok, reason = client.market_open(symbol)
    if not open_ok:
        violations.append(f"market closed: {reason}")
    if status["liquidity_blocked"]:
        violations.append(
            f"liquidity blackout ({settings.liquidity_blackout_start}-"
            f"{settings.liquidity_blackout_end} server time)"
        )
    if status["news_blocked"] and not is_synthetic_symbol(symbol):
        violations.append(
            f"high-impact news within {settings.news_blackout_minutes:.0f} minutes"
        )

    tick = client.tick(symbol)
    if entry_price is not None:
        entry = float(entry_price)
    else:
        entry = (
            float(tick.get("ask") or 0.0)
            if side == "BUY"
            else float(tick.get("bid") or 0.0)
        )
    if sl_price is None and sl_pips is None:
        violations.append("stop-loss required to bound risk")
    elif sl_pips is None:
        pip = pip_size(info)
        sl_pips = abs(entry - sl_price) / pip if pip else 0.0

    pip_value = pip_value_per_lot(info)
    risk_amount = lots * (sl_pips or 0.0) * pip_value if sl_pips else 0.0

    cap_percent = (
        settings.conservative_risk_percent
        if status["mode"] == "conservative"
        else settings.max_risk_percent
    )
    cap = equity * cap_percent / 100.0
    if risk_amount - cap > 1e-6:
        violations.append(
            f"risk {risk_amount:.2f} exceeds cap {cap_percent:.2f}% "
            f"({cap:.2f}) in {status['mode']} mode"
        )

    open_risk = status["open_risk"]
    max_open = equity * settings.max_open_risk_percent / 100.0
    if open_risk + risk_amount > max_open:
        violations.append(
            f"total floating risk {open_risk + risk_amount:.2f} exceeds "
            f"{settings.max_open_risk_percent:.1f}% cap ({max_open:.2f})"
        )

    group = CORRELATION_GROUPS.get(symbol)
    if group:
        group_risk = status["correlation_exposure"].get(group, 0.0)
        if group_risk + risk_amount - cap > 1e-6:
            violations.append(
                f"correlated exposure in '{group}' ({group_risk + risk_amount:.2f}) "
                f"exceeds combined cap ({cap:.2f})"
            )

    if status["trades_today"] >= status["max_trades_per_day"]:
        violations.append(
            f"daily trade limit reached ({status['max_trades_per_day']})"
        )

    losses = status["consecutive_losses"]
    if losses >= settings.max_consecutive_losses:
        until = status["revenge_paused_until_epoch"]
        if until and time.time() < until:
            violations.append(
                f"revenge pause active after {losses} consecutive losses "
                f"until {datetime.fromtimestamp(until, timezone.utc).isoformat()}"
            )

    if confidence is not None and confidence < settings.confidence_floor_percent:
        violations.append(
            f"confidence {confidence:.0f}% below floor {settings.confidence_floor_percent:.0f}%"
        )

    point = float(info.get("point") or 0.0)
    if point:
        bid = float(tick.get("bid") or 0.0)
        ask = float(tick.get("ask") or 0.0)
        spread_points = (ask - bid) / point
        mid = (ask + bid) / 2 if (ask + bid) else 0.0
        relative_spread = (ask - bid) / mid if mid else 0.0
        if is_synthetic_symbol(symbol):
            spread_ok = (
                not settings.max_spread_relative
                or relative_spread <= settings.max_spread_relative
            )
        elif settings.max_spread_points:
            spread_ok = spread_points <= settings.max_spread_points
        else:
            spread_ok = (
                not settings.max_spread_relative
                or relative_spread <= settings.max_spread_relative
            )
        if not spread_ok:
            violations.append(
                f"spread {spread_points:.0f} points ({relative_spread * 100:.3f}%) exceeds configured limit"
            )

    return _verdict(violations, warnings, status)


def _verdict(violations: list[str], warnings: list[str], status: dict | None) -> dict:
    allowed = len(violations) == 0
    return {
        "allowed": allowed,
        "mode": status["mode"] if status else "unknown",
        "status": status["status"] if status else "unknown",
        "violations": violations,
        "warnings": warnings,
        "trading_allowed": allowed,
    }
