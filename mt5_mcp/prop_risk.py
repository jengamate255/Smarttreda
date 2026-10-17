"""Account-scoped loss floors and reservation of existing stop/order exposure.

Never sends or cancels orders. Unknown opening equity (overnight exposure without
a verified daily baseline) blocks new risk, rather than guessing the loss floor.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import uuid
from functools import wraps
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mt5_mcp.config import get_settings

STATE_PATH = Path(__file__).resolve().parent / "memory" / "prop_risk_state.json"
ADMISSION_PATH = STATE_PATH.with_suffix(".db")
_local = threading.local()


class ResetExposureError(ValueError):
    """History is complete, but reset equity cannot be inferred from deal cashflows."""


@contextmanager
def _ledger():
    conn = sqlite3.connect(str(ADMISSION_PATH),timeout=10)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS admissions (account TEXT PRIMARY KEY, owner TEXT, state TEXT)")
        with conn:
            yield conn
    finally:
        conn.close()


def _account_key(cfg):
    return f"{cfg.account_login}:{cfg.server}"


def serialized_admission(method):
    """Durable, fail-closed admission latch across all native order senders."""
    @wraps(method)
    def wrapper(client,request,*args,**kwargs):
        cfg = getattr(get_settings(),"prop_risk",None)
        opening = int(request.get("action",1)) in (1,5) and not request.get("position")
        if not cfg or not cfg.enabled or not opening:
            return method(client,request,*args,**kwargs)
        owner = uuid.uuid4().hex
        key = _account_key(cfg)
        ADMISSION_PATH.parent.mkdir(parents=True,exist_ok=True)
        with _ledger() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT owner FROM admissions WHERE account=?",(key,)).fetchone():
                raise ValueError("Another or uncertain prop order requires reconciliation; new risk blocked")
            conn.execute("INSERT INTO admissions VALUES (?,?,?)",(key,owner,"inflight"))
        _local.owner,_local.sent,_local.resolved = owner,False,False
        try:
            return method(client,request,*args,**kwargs)
        finally:
            with _ledger() as conn:
                if not _local.sent or _local.resolved:
                    conn.execute("DELETE FROM admissions WHERE account=? AND owner=?",(key,owner))
                else:
                    conn.execute("UPDATE admissions SET state='uncertain' WHERE account=? AND owner=?",(key,owner))
            _local.owner = None
    return wrapper


def mark_sending():
    if getattr(_local,"owner",None):
        _local.sent,_local.resolved = True,False


def record_result(result):
    if getattr(_local,"owner",None):
        code = result.get("retcode")
        _local.resolved = code in (10004,10006,10007,10008,10009,10010) or (
            isinstance(code,int) and 10013 <= code <= 10046 and code not in (10028,10031))


def modification_violations(request,client):
    cfg = getattr(get_settings(),"prop_risk",None)
    if not cfg or not cfg.enabled or "sl" not in request and not request.get("order"):
        return []
    try:
        position_id,order_id = request.get("position"),request.get("order")
        rows = client.positions() if position_id else client.pending_orders()
        old = next(row for row in rows if int(row["ticket"])==int(position_id or order_id))
        side = int(old["type"]) % 2
        stop = _number(request.get("sl",old.get("sl")))
        old_stop = _number(old.get("sl") or 0)
        if stop <= 0 or old_stop and (stop < old_stop if side==0 else stop > old_stop):
            return ["Prop policy blocks removing or widening protective stops"]
        if order_id and (request.get("volume",old.get("volume_current")) != old.get("volume_current") or
                         request.get("price",old.get("price_open")) != old.get("price_open")):
            return ["Prop policy blocks pending entry/volume changes; cancel and re-evaluate"]
        return []
    except Exception:
        return ["Cannot verify that modification preserves or reduces prop risk"]


def _number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite account evidence")
    return value


def stop_risk(info, entry, sl, volume, side, *, spread=True):
    tick_size = _number(info.get("trade_tick_size") or 0)
    tick_value = _number(info.get("trade_tick_value_loss") or info.get("trade_tick_value") or 0)
    entry, sl, volume = map(_number, (entry, sl, volume))
    if min(tick_size, tick_value, entry, sl, volume) <= 0 or side not in (0, 1):
        raise ValueError("Unbounded or unpriced exposure")
    distance = max(0, entry-sl if side == 0 else sl-entry)
    padding = max(0, _number(info.get("spread") or 0) * _number(info.get("point") or 0)) if spread else 0
    return (distance+padding) / tick_size * tick_value * volume


def opening_balance(account, deals, positions, day_start, offset):
    """Infer opening equity only when no position straddled the reset boundary."""
    today, seen = [], set()
    for deal in deals:
        ticket = int(deal.get("ticket") or 0)
        if not ticket or ticket in seen:
            continue
        seen.add(ticket)
        stamp = _number(deal.get("time_msc") or 0)/1000 or _number(deal.get("time") or 0)
        if stamp-offset >= day_start:
            today.append(deal)
    if any(int(p.get("time") or 0)-offset < day_start for p in positions):
        raise ValueError("Opening equity needs an overnight baseline")
    legs = {}
    for deal in today:
        kind, entry = int(deal.get("type", -1)), int(deal.get("entry", -1))
        if kind in (0, 1):
            if entry not in (0, 1, 3):
                raise ValueError("Reversal history is ambiguous")
            key = int(deal.get("position_id") or 0)
            legs[key] = legs.get(key, 0) + _number(deal.get("volume") or 0)*(1 if entry == 0 else -1)
        elif kind in (2, 3) and _number(deal.get("profit") or 0):
            raise ValueError("Cash transfer requires baseline verification")
    for position in positions:
        key = int(position.get("identifier") or position["ticket"])
        legs[key] = legs.get(key, 0) - _number(position["volume"])
    if any(abs(volume) > 1e-8 for volume in legs.values()):
        raise ValueError("Incomplete or overnight position history")
    net = sum(sum(_number(d.get(k) or 0) for k in ("profit", "commission", "swap", "fee")) for d in today)
    baseline = _number(account["balance"]) - net
    if baseline <= 0:
        raise ValueError("Invalid opening balance")
    return baseline


def verified_flat_baseline(account, deals, day_start, offset, now, initial_balance):
    """Verify the complete funding/deal ledger, then reverse today's booked net.

    A closed overnight trade still cannot reveal its reset-time floating equity.
    opening_balance therefore continues to reject a position crossing the reset.
    """
    unique = {}
    for deal in deals:
        ticket = int(deal.get("ticket") or 0)
        if ticket <= 0:
            raise ValueError("Broker history contains a missing deal ticket")
        if ticket in unique and unique[ticket] != deal:
            raise ValueError("Broker history contains conflicting duplicate deals")
        unique[ticket] = deal
    rows = sorted(unique.values(), key=lambda d: _number(d.get("time_msc") or 0)/1000 or _number(d.get("time") or 0))
    if not rows or int(rows[0].get("type", -1)) != 2 or abs(_number(rows[0].get("profit")) - initial_balance) > .01:
        raise ValueError("Complete broker history must include the verified initial funding")
    legs, reset_legs, net = {}, {}, 0.0
    for deal in rows:
        stamp = (_number(deal.get("time_msc") or 0)/1000 or _number(deal.get("time") or 0)) - offset
        if stamp <= 0 or stamp > now:
            raise ValueError("Broker history timestamps are unavailable or outside the verified window")
        kind = int(deal.get("type", -1))
        if kind in (0, 1):
            entry, key = int(deal.get("entry", -1)), int(deal.get("position_id") or 0)
            volume = _number(deal.get("volume") or 0)
            if entry not in (0, 1, 3) or key <= 0 or volume <= 0:
                raise ValueError("Broker position history is ambiguous")
            legs[key] = legs.get(key, 0) + volume*(1 if entry == 0 else -1)
            if stamp < day_start:
                reset_legs[key] = reset_legs.get(key, 0) + volume*(1 if entry == 0 else -1)
            if legs[key] < -1e-8:
                raise ValueError("Broker position history is missing opening volume")
        elif kind not in (2, 3):
            raise ValueError("Unsupported broker balance operation requires baseline verification")
        net += sum(_number(deal.get(k) or 0) for k in ("profit", "commission", "swap", "fee"))
    if any(abs(volume) > 1e-8 for volume in legs.values()):
        raise ValueError("Broker history is incomplete: position volumes do not close")
    if abs(net - _number(account["balance"])) > .01:
        raise ValueError("Complete broker history does not reconcile to current balance")
    if any(abs(volume) > 1e-8 for volume in reset_legs.values()):
        raise ResetExposureError("Verified history shows overnight exposure at today's reset; opening equity remains unknown")
    return opening_balance(account, rows, [], day_start, offset), len(rows)


def snapshot(client, *, state_path=None, now=None):
    cfg = getattr(get_settings(), "prop_risk", None)
    if not cfg or not cfg.enabled:
        return {"enabled": False}
    risk = get_settings().risk
    state_path = Path(state_path or STATE_PATH)
    live_clock = now is None
    evidence = {"baseline_status":"unverified", "recovery_status":"evidence_unavailable",
                "recovery_action":"Refresh to retry verification. No risk limits are changed."}
    try:
        account = client.account_info()
        if account.get("login") != cfg.account_login or account.get("server") != cfg.server or not cfg.initial_balance:
            raise ValueError("Prop account identity/starting balance not verified")
        if ADMISSION_PATH.exists():
            with _ledger() as conn:
                active = conn.execute("SELECT owner FROM admissions WHERE account=?",(_account_key(cfg),)).fetchone()
            if active and active[0] != getattr(_local,"owner",None):
                raise ValueError("Unreconciled or concurrent prop order; no new risk")
        equity, balance = _number(account["equity"]), _number(account["balance"])
        if min(equity, balance) <= 0:
            raise ValueError("Account values unavailable")
        offset = get_settings().quality.broker_time_offsets.get(account["server"])
        if offset is None:
            raise ValueError("Broker history clock not verified")
        now = time.time() if now is None else now
        reset = cfg.reset_utc_offset_seconds
        day_start = int((now+reset)//86400)*86400-reset
        evidence.update(reset_utc=datetime.fromtimestamp(day_start,timezone.utc).isoformat(),
                        next_reset_utc=datetime.fromtimestamp(day_start+86400,timezone.utc).isoformat())
        key = f"{cfg.account_login}:{cfg.server}:{day_start}:{cfg.initial_balance}"
        positions, pendings = client.positions(), client.pending_orders()
        if positions is None or pendings is None:
            raise ValueError("Exposure evidence unavailable")
        evidence.update(open_positions=len(positions), pending_orders=len(pendings),
                        overnight_positions=sum(int(p.get("time") or 0)-offset < day_start for p in positions))
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {}
        baseline = saved.get("opening_baseline") if saved.get("key") == key else None
        if baseline is None:
            evidence.update(recovery_status="waiting_for_flat", recovery_action=
                "Wait for all positions to close and pending entries to clear; refresh to verify broker history. Position management remains available.")
            if positions or pendings:
                raise ValueError("Today's opening baseline is unverified; entry admission paused until flat")
            evidence.update(recovery_status="history_unverified", recovery_action=
                "Refresh to retry complete broker history verification. If exposure crossed today's reset, wait for a later flat reset; closing it today cannot verify reset equity.")
            # Serialize recovery against native admissions; never clear an uncertain latch.
            ADMISSION_PATH.parent.mkdir(parents=True,exist_ok=True)
            with _ledger() as conn:
                conn.execute("BEGIN IMMEDIATE")
                active = conn.execute("SELECT owner FROM admissions WHERE account=?",(_account_key(cfg),)).fetchone()
                if active and active[0] != getattr(_local,"owner",None):
                    raise ValueError("Unreconciled or concurrent prop order; no new risk")
                deals = client.account_deals_since(0)
                if deals is None:
                    raise ValueError("Complete broker history unavailable")
                baseline, count = verified_flat_baseline(account,deals,day_start,offset,now,cfg.initial_balance)
                current = client.account_info()
                if any(current.get(k) != account.get(k) for k in ("login", "server", "balance")):
                    raise ValueError("Account changed during daily baseline collection")
                if client.positions() != [] or client.pending_orders() != []:
                    raise ValueError("Exposure changed during daily baseline collection")
                collected_at = time.time() if live_clock else now
                if int((collected_at+reset)//86400)*86400-reset != day_start:
                    raise ValueError("Reset changed during daily baseline collection")
                state_path.parent.mkdir(parents=True,exist_ok=True)
                temporary = state_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                temporary.write_text(json.dumps({"key":key,"opening_baseline":baseline,
                    "source":"verified_flat_broker_history", "history_deals":count,
                    "verified_at_utc":datetime.fromtimestamp(collected_at,timezone.utc).isoformat()}),encoding="utf-8")
                os.replace(temporary,state_path)
                saved = {"source":"verified_flat_broker_history"}
        baseline = _number(baseline)
        if baseline <= 0:
            raise ValueError("Invalid saved opening baseline")
        evidence.update(baseline_status="verified",opening_baseline=baseline,
                        baseline_source=saved.get("source", "saved_daily_baseline"),
                        recovery_status="verified", recovery_action="Baseline verified for this reset. All entry gates and risk limits still apply.")
        reserved = 0.0
        for position in positions:
            info = client.symbol_info(position["symbol"])
            reserved += stop_risk(info,position.get("price_current"),position.get("sl"),position.get("volume"),int(position["type"]))
        for order in pendings:
            order_type = int(order["type"])
            if order_type not in (2,3,4,5,6,7):
                raise ValueError("Unknown pending order")
            info = client.symbol_info(order["symbol"])
            reserved += stop_risk(info,order.get("price_open"),order.get("sl"),order.get("volume_current"),order_type % 2)
        daily_floor = baseline*(1-risk.max_daily_loss_percent/100)
        overall_floor = cfg.initial_balance*(1-risk.max_drawdown_percent/100)
        margin = cfg.initial_balance*cfg.safety_buffer_percent/100
        daily_buffer = min(equity,balance)-daily_floor-reserved-margin
        overall_buffer = min(equity,balance)-overall_floor-reserved-margin
        budget = max(0,min(daily_buffer,overall_buffer))
        return {**evidence,"enabled":True,"available":True,"state":"ready" if budget>0 else "blocked",
            "entry_admission":"eligible" if budget>0 else "paused",
            "opening_baseline":baseline,"initial_balance":cfg.initial_balance,
            "daily_floor":daily_floor,"overall_floor":overall_floor,"reserved_stop_risk":reserved,
            "safety_buffer":margin,"remaining_daily_risk":max(0,daily_buffer),
            "remaining_overall_risk":max(0,overall_buffer),"new_trade_budget":budget,
            "pending_orders":len(pendings),"open_positions":len(positions),
            "reset_utc":datetime.fromtimestamp(day_start,timezone.utc).isoformat()}
    except Exception as exc:
        if isinstance(exc, ResetExposureError):
            evidence.update(recovery_status="waiting_for_flat_reset", recovery_action=
                "History reconciles, but closing overnight positions today cannot reveal today's reset equity. Remain flat through a later reset, then refresh to reconstruct that day's opening baseline.")
        return {**evidence,"enabled":True,"available":False,"state":"unavailable","new_trade_budget":0.0,
                "entry_admission":"paused",
                "reason":str(exc) if isinstance(exc,ValueError) else "Verified prop account evidence unavailable"}
