"""Closed-candle, location and execution checks shared by automated entry paths."""
from __future__ import annotations

import math
import threading
import time
from typing import Any

import pandas as pd

from mt5_mcp.config import get_settings

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def enabled() -> bool:
    return bool(getattr(get_settings(), "quality", None) and get_settings().quality.enabled)


def closed_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or len(frame) < 2:
        raise ValueError("closed candles unavailable")
    return frame.iloc[:-1].copy().reset_index(drop=True)


def _epoch(value: Any) -> int:
    return int(value.timestamp()) if hasattr(value, "timestamp") else int(value)


def _side(value: str) -> str | None:
    return {"bullish": "BUY", "bearish": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(value)


def pattern_entry_check(pattern: dict, side: str, entry: float, *,
                        sl: float | None = None, tp: float | None = None,
                        min_rr: float | None = None) -> dict:
    """Check that a confirmed pattern still supports this executable entry.

    A generated SL/TP cannot turn an invalidated or already-completed pattern
    into fresh evidence. Remaining reward is measured against the proposed stop.
    """
    reasons = []
    target = invalidation = remaining_rr = None
    if side not in ("BUY", "SELL") or _side(pattern.get("direction")) != side:
        reasons.append("pattern direction does not agree with entry")
    if pattern.get("status") != "confirmed":
        reasons.append("pattern is not confirmed")
    try:
        entry, target, invalidation = map(float, (entry, pattern.get("target"), pattern.get("invalidation")))
        if not all(math.isfinite(value) and value > 0 for value in (entry, target, invalidation)):
            raise ValueError("invalid structure")
        sign = 1 if side == "BUY" else -1
        reward = sign * (target - entry)
        if sign * (entry - invalidation) <= 0:
            reasons.append("pattern invalidation has been crossed")
        if reward <= 0:
            reasons.append("pattern target already reached at entry")
        if sl is not None:
            stop = float(sl)
            risk = sign * (entry - stop)
            floor = float(get_settings().risk.min_risk_reward if min_rr is None else min_rr)
            if not math.isfinite(stop) or stop <= 0 or risk <= 0 or not math.isfinite(floor) or floor <= 0:
                raise ValueError("invalid stop or reward floor")
            remaining_rr = max(0.0, reward) / risk
            if remaining_rr + 1e-9 < floor:
                reasons.append(f"remaining pattern RR {remaining_rr:.2f} below {floor:.2f}")
        if tp is not None:
            proposed_target = float(tp)
            if not math.isfinite(proposed_target) or proposed_target <= 0 or sign * (proposed_target - entry) <= 0:
                raise ValueError("invalid target")
            if sign * (proposed_target - target) > max(abs(target), 1.0) * 1e-10:
                reasons.append("planned target extends beyond pattern target")
    except (ValueError, TypeError, OverflowError):
        reasons.append("pattern price evidence unavailable")
    return {"allowed": not reasons, "reasons": reasons, "remaining_rr": remaining_rr,
            "target": target, "invalidation": invalidation}


def market_context(client, symbol: str, side: str) -> dict[str, Any]:
    """Only indicator data are cached; execution prices are always fetched fresh."""
    from mt5_mcp.chart_patterns import analyze_chart
    from mt5_mcp.indicators import atr
    from mt5_mcp.price_action import detect_patterns
    from mt5_mcp.strategy import analyze_trend
    from mt5_mcp.supply_demand import detect_zones

    cfg = get_settings().quality
    account = client.account_info()
    if not account or not account.get("login"):
        raise ValueError("account unavailable")
    key = (account["login"], account.get("server"), symbol.upper(), side, cfg.entry_timeframe)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
    h1 = closed_frame(client.rates(symbol, "H1", 400))
    h4 = closed_frame(client.rates(symbol, "H4", 250))
    raw_entry = client.rates(symbol, cfg.entry_timeframe, 120)
    entry = closed_frame(raw_entry)
    patterns = analyze_chart(h1, symbol=symbol, timeframe="H1", lookback=350).get("patterns", [])
    confirmations = detect_patterns(entry, lookback=len(entry), check_last=1)
    result = {
        "symbol": symbol, "side": side, "account_login": account["login"],
        "broker_time_offset_seconds": cfg.broker_time_offsets.get(account.get("server"), 0),
        "trends": {"H1": _side(analyze_trend(h1).direction), "H4": _side(analyze_trend(h4).direction)},
        "patterns": [p for p in patterns if p.get("status") == "confirmed" and _side(p.get("direction")) == side],
        "confirmation": any(_side(p.direction) == side and p.confidence >= 0.6 for p in confirmations),
        "zones": [{"lower": float(z.lower), "upper": float(z.upper), "fresh": bool(z.fresh),
                   "broken": bool(getattr(z, "broken", False)), "side": "BUY" if z.type == "demand" else "SELL"}
                  for z in detect_zones(h1, lookback=300)],
        "atr": float(atr(entry).iloc[-1]),
        "signal_bar": _epoch(entry.iloc[-1]["time"]),
        "next_bar": _epoch(raw_entry.iloc[-1]["time"]),
        "timeframe": cfg.entry_timeframe,
    }
    with _cache_lock:
        if len(_cache) > 128:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
    return result


def evaluate(request: dict, context: dict, tick: dict, *, min_rr: float | None = None) -> dict:
    cfg = get_settings().quality
    side = context["side"]
    violations = []
    if context["trends"].get("H1") != side or context["trends"].get("H4") != side:
        violations.append("H1/H4 direction does not agree with entry")
    patterns = context.get("patterns", [])
    if not patterns:
        violations.append("no confirmed closed-H1 setup")
    from mt5_mcp.learning_loop import normalize_setup
    expected = normalize_setup(request.get("comment"))
    if expected.startswith("desk:"):
        patterns = [p for p in patterns if normalize_setup("desk:" + str(p.get("pattern"))) == expected]
        if not patterns:
            violations.append("selected desk setup is not confirmed")
    if not context.get("confirmation"):
        violations.append("no closed-entry-candle confirmation")
    bid, ask = float(tick.get("bid") or 0), float(tick.get("ask") or 0)
    atr_value = context["atr"]
    if not all(math.isfinite(value) and value > 0 for value in (bid, ask, atr_value)):
        raise ValueError("fresh quote/ATR unavailable")
    age = time.time() - (float(tick.get("time") or 0) - context.get("broker_time_offset_seconds", 0))
    if ask < bid or not tick.get("time") or age < -5 or age > 120:
        raise ValueError("fresh quote unavailable")
    from mt5_mcp.paper_learning import _seconds
    candle_age = float(tick["time"]) - context["next_bar"]
    if candle_age < -5 or candle_age > _seconds(context["timeframe"]) * 2:
        violations.append("entry candles are stale relative to broker quote")
    location_ok = any(z["side"] == side and z["fresh"] and not z["broken"] and
                      max(z["lower"] - bid, bid - z["upper"], 0) <= cfg.max_zone_distance_atr * atr_value
                      for z in context.get("zones", []))
    if not location_ok:
        violations.append("price outside a fresh entry zone")
    order_type = int(request.get("type", 0))
    market = int(request.get("action", 1)) == 1
    live_entry = ask if side == "BUY" else bid
    entry = live_entry if market else float(request.get("price") or 0)
    planned = float(request.get("price") or entry)
    if market and abs(live_entry - planned) > cfg.max_entry_drift_atr * atr_value:
        violations.append("entry moved beyond permitted ATR drift")
    sl, tp = float(request.get("sl") or 0), float(request.get("tp") or 0)
    geometry = sl < entry < tp if side == "BUY" else tp < entry < sl
    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
    floor = get_settings().risk.min_risk_reward if min_rr is None else min_rr
    if not geometry or not all(math.isfinite(v) and v > 0 for v in (entry, sl, tp)):
        violations.append("invalid current entry/stop/target geometry")
    elif rr < floor:
        violations.append(f"current RR {rr:.2f} below {floor:.2f}")
    structure = [pattern_entry_check(p, side, entry, sl=sl, tp=tp, min_rr=floor) for p in patterns]
    if structure and not any(item["allowed"] for item in structure):
        violations.extend(dict.fromkeys(reason for item in structure for reason in item["reasons"]))
    return {"allowed": not violations, "violations": [f"quality: {v}" for v in violations],
            "context": context, "entry": entry, "sl": sl, "tp": tp,
            "spread": max(0.0, ask - bid), "rr": rr, "order_type": order_type,
            "pattern_checks": structure}


def check(request: dict, client=None) -> dict:
    if not enabled():
        return {"allowed": True, "violations": []}
    try:
        if client is None:
            from mt5_mcp.client import get_client
            client = get_client()
        symbol = str(request.get("symbol") or "")
        market_open, reason = client.market_open(symbol)
        if not market_open:
            raise ValueError(reason)
        side = "BUY" if int(request.get("type", 0)) in (0, 2, 4) else "SELL"
        context = market_context(client, symbol, side)
        return evaluate(request, context, client.tick(symbol))
    except Exception as exc:
        return {"allowed": False, "violations": [f"quality: evidence unavailable ({exc})"]}


def capture(request: dict, result: dict, violations: list[str]) -> None:
    if not enabled():
        return
    from mt5_mcp.paper_learning import record_candidate
    record_candidate(request, result, violations)
