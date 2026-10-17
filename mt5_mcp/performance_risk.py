"""Outcome-based risk budgets, scoped to the current broker account.

Only fully closed, attributable positions count. Entry and exit costs are
included. Unavailable, ambiguous or insufficient evidence uses the low budget.
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from mt5_mcp.config import get_settings

_cache = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(getattr(getattr(get_settings(), "performance_risk", None), "enabled", False))


def recognized(deal: dict) -> bool:
    from mt5_mcp.tools.trades import MAGIC
    comment = str(deal.get("comment") or "").lower()
    magics = {MAGIC, MAGIC + 7, int(os.getenv("MT5_DESK_MAGIC", str(MAGIC + 7)))}
    magics.discard(0)
    return int(deal.get("magic") or 0) in magics or comment.startswith(
        ("desk-", "desk:", "confirm_exec", "at:", "btc-trend", "btc_trend")) or any(
        label in comment for label in ("range breakout", "spike-drift"))


def completed_outcomes(deals: list[dict], open_tickets: set[int], offset: int, now: float) -> list[dict]:
    """Aggregate complete position lifecycles, excluding partial/reversal ambiguity."""
    positions, seen = {}, set()
    for deal in deals:
        ticket = int(deal.get("ticket") or 0)
        if ticket and ticket in seen:
            continue
        if ticket:
            seen.add(ticket)
        position = int(deal.get("position_id") or 0)
        if position <= 0 or int(deal.get("type", -1)) not in (0, 1):
            continue
        group = positions.setdefault(position, {"position":position,"net_pnl":0.0,"volume_in":0.0,
            "volume_out":0.0,"closed_at":0.0,"attributable":False,"ambiguous":False})
        entry = int(deal.get("entry", -1))
        value = sum(float(deal.get(key) or 0) for key in ("profit", "commission", "swap", "fee"))
        volume = float(deal.get("volume") or 0)
        if not math.isfinite(value) or not math.isfinite(volume) or volume <= 0:
            raise ValueError("Invalid broker outcome values")
        group["net_pnl"] += value
        group["attributable"] |= recognized(deal)
        if entry == 0:
            group["volume_in"] += volume
        elif entry in (1, 3):
            group["volume_out"] += volume
            stamp = float(deal.get("time_msc") or 0) / 1000 or float(deal.get("time") or 0)
            group["closed_at"] = max(group["closed_at"], stamp - offset)
        else:
            group["ambiguous"] = True
    result = []
    for group in positions.values():
        if not group["attributable"] or group["ambiguous"] or group["position"] in open_tickets:
            continue
        if group["volume_in"] <= 0 or not math.isclose(group["volume_in"], group["volume_out"], rel_tol=0, abs_tol=1e-8):
            continue
        if not group["closed_at"] or group["closed_at"] > now + 5:
            raise ValueError("Broker close timestamps are not verified")
        result.append({"position":group["position"],"closed_at":group["closed_at"],"net_pnl":group["net_pnl"]})
    return sorted(result, key=lambda row:(row["closed_at"],row["position"]))


def decide(outcomes: list[dict], cfg) -> dict:
    recent = outcomes[-(1 if cfg.mode == "last_trade" else cfg.window):]
    values = [float(row["net_pnl"]) for row in recent]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite outcome evidence")
    net = sum(values)
    wins, losses = sum(max(v,0) for v in values), sum(max(-v,0) for v in values)
    pf = wins / losses if losses else None
    streak = 0
    for value in reversed(values):
        if value >= 0:
            break
        streak += 1
    minimum = 1 if cfg.mode == "last_trade" else cfg.min_trades
    sufficient = len(values) >= minimum
    winning = sufficient and net > 0 and streak < cfg.loss_streak_limit and (
        cfg.mode == "last_trade" or losses == 0 or pf >= cfg.winning_profit_factor)
    state = "winning" if winning else "insufficient" if not sufficient else "losing" if net < 0 or streak >= cfg.loss_streak_limit else "unproven"
    return {"enabled":True,"state":state,
            "risk_percent":cfg.winning_percent if winning else cfg.losing_percent,
            "mode":cfg.mode,"trades":len(values),"minimum":minimum,"net_pnl":round(net,2),
            "profit_factor":round(pf,3) if pf is not None else None,"consecutive_losses":streak}


def snapshot(client=None) -> dict:
    if not enabled():
        return {"enabled":False,"state":"disabled","risk_percent":None}
    cfg = get_settings().performance_risk
    try:
        if client is None:
            from mt5_mcp.client import get_client
            client = get_client()
        account = client.account_info()
        equity = float((account or {}).get("equity") or 0)
        if not account or not account.get("login") or not math.isfinite(equity) or equity <= 0:
            raise ValueError("Account unavailable")
        key = (account["login"],account.get("server"),round(float(account.get("balance") or 0),2),
               cfg.mode,cfg.window,cfg.min_trades,cfg.losing_percent,cfg.winning_percent,cfg.winning_profit_factor,
               cfg.lookback_days,cfg.loss_streak_limit)
        with _lock:
            cached = _cache.get(key)
        if cfg.mode != "last_trade" and cached and time.monotonic()-cached[0] < 30:
            return dict(cached[1])
        now = datetime.now(timezone.utc)
        deals = client.deals_since(int((now-timedelta(days=cfg.lookback_days)).timestamp()))
        if deals is None:
            raise ValueError("History unavailable")
        positions = client.positions()
        if positions is None:
            raise ValueError("Open-position evidence unavailable")
        offsets = getattr(getattr(get_settings(),"quality",None),"broker_time_offsets",{})
        offset = offsets.get(account.get("server"),0)
        rows = completed_outcomes(deals,{int(p["ticket"]) for p in positions},offset,now.timestamp())
        current = client.account_info()
        if any(current.get(key) != account.get(key) for key in ("login", "server", "balance")):
            raise ValueError("Account changed during outcome collection")
        result = decide(rows,cfg)
        with _lock:
            _cache.clear()
            _cache[key] = (time.monotonic(),result)
        return dict(result)
    except Exception:
        return {"enabled":True,"state":"unavailable","risk_percent":cfg.losing_percent,
                "mode":cfg.mode,"trades":0,"minimum":1 if cfg.mode=="last_trade" else cfg.min_trades,
                "reason":"Verified completed-account outcomes unavailable; low risk budget applies"}


def effective_percent(client, fallback: float) -> float:
    result = snapshot(client)
    return float(result["risk_percent"]) if result["enabled"] else fallback


def limit_budget(client, amount: float) -> float:
    cfg = getattr(get_settings(),"prop_risk",None)
    if cfg and cfg.enabled:
        from mt5_mcp.prop_risk import snapshot as prop_snapshot
        amount = min(amount,prop_snapshot(client)["new_trade_budget"])
    if not enabled():
        return amount
    try:
        equity = float(client.account_info().get("equity") or 0)
        return min(amount, max(0,equity) * effective_percent(client,0.1) / 100)
    except Exception:
        return 0.0
