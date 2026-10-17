"""Prospective rejected-entry tracking and paper-only requalification.

Paper outcomes live in a separate database and never train on the live journal.
Entries start at the next full bar open. Ambiguous stop/target bars are losses
for reporting and cannot qualify a setup for live use.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mt5_mcp.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
 setup TEXT NOT NULL, created_at TEXT NOT NULL, signal_bar INTEGER, target_bar INTEGER,
 timeframe TEXT NOT NULL, entry REAL, sl REAL, tp REAL, spread REAL DEFAULT 0, entry_atr REAL,
 reasons TEXT NOT NULL, qualified INTEGER NOT NULL, status TEXT NOT NULL,
 entered_bar INTEGER, closed_at TEXT, r_multiple REAL, ambiguous INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS paper_pending ON candidates(account,status,symbol);
CREATE UNIQUE INDEX IF NOT EXISTS paper_independent ON candidates(account,symbol,side,setup)
 WHERE status IN ('waiting','open');
CREATE TABLE IF NOT EXISTS quarantines (
 account TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, setup TEXT NOT NULL,
 trigger_close TEXT NOT NULL, since TEXT NOT NULL, until_time TEXT,
 state TEXT NOT NULL, released_at TEXT,
 PRIMARY KEY(account,symbol,side,setup)
);
CREATE TABLE IF NOT EXISTS feed_status (
 account TEXT PRIMARY KEY, data TEXT NOT NULL
);
"""
_advance_lock = threading.Lock()
_advance_status = {"running":False,"last_completed_at":None,"last_error":None}
logger = logging.getLogger("mt5_mcp.paper_learning")


def account_scope() -> str:
    return os.getenv("MT5_MCP_ACCOUNT", "PROP").upper() + ":" + str(Path(get_settings().memory.db_path).resolve())


@contextmanager
def _connect():
    path = Path(get_settings().quality.shadow_db_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(candidates)")}
    if "entry_atr" not in columns:
        try:
            conn.execute("ALTER TABLE candidates ADD COLUMN entry_atr REAL")
            conn.commit()
        except sqlite3.OperationalError:
            if "entry_atr" not in {row[1] for row in conn.execute("PRAGMA table_info(candidates)")}:
                conn.close()
                raise
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seconds(tf):
    if tf.startswith("M") and tf[1:].isdigit():
        return int(tf[1:]) * 60
    if tf.startswith("H") and tf[1:].isdigit():
        return int(tf[1:]) * 3600
    raise ValueError("paper timeframe unsupported")


def record_candidate(request: dict, quality: dict, reasons: list[str]) -> str:
    from mt5_mcp.learning_loop import normalize_setup

    context = quality.get("context", {})
    scope = account_scope()
    symbol = str(request.get("symbol") or "").upper()
    side = context.get("side") or ("BUY" if int(request.get("type", 0)) in (0, 2, 4) else "SELL")
    setup = normalize_setup(request.get("comment"))
    reasons = list(reasons)
    risk_cfg = get_settings().risk
    hours = getattr(risk_cfg, "allowed_hours_utc", []) or []
    if hours and datetime.now(timezone.utc).hour not in hours:
        reasons.append("paper: outside allowed trading hours")
    confidence = request.get("confidence")
    if confidence is not None and confidence < getattr(risk_cfg, "confidence_floor_percent", 0):
        reasons.append("paper: confidence below floor")
    if getattr(risk_cfg, "news_blackout_minutes", 0) > 0:
        try:
            from mt5_mcp.compliance import is_synthetic_symbol
            from mt5_mcp.tools.news import get_provider, news_status
            if not is_synthetic_symbol(symbol):
                events = get_provider().upcoming(hours=24)
                if news_status().get("news_stale"):
                    reasons.append("paper: news evidence stale")
                now = datetime.now(timezone.utc)
                if any(event.impact == "high" and abs((event.time - now).total_seconds()) <=
                       risk_cfg.news_blackout_minutes * 90 for event in events or []):
                    reasons.append("paper: news blackout")
        except Exception:
            reasons.append("paper: news evidence unavailable")
    bar = context.get("signal_bar")
    identity = f"{scope}|{symbol}|{side}|{setup}|{bar if bar is not None else _now()[:16]}"
    identifier = hashlib.sha256(identity.encode()).hexdigest()[:24]
    entry, sl, tp = quality.get("entry"), quality.get("sl"), quality.get("tp")
    priceable = all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in (entry, sl, tp))
    tf = context.get("timeframe", get_settings().quality.entry_timeframe)
    target = context.get("next_bar")
    if target is not None:
        target += _seconds(tf)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("SELECT 1 FROM candidates WHERE account=? AND symbol=? AND side=? AND setup=? "
                              "AND status IN ('waiting','open') LIMIT 1", (scope,symbol,side,setup)).fetchone()
        status = "waiting" if priceable and target and not active else "observed"
        qualified = bool(quality.get("allowed")) and bool(context.get("atr", 0) > 0) and not any(
            "outside allowed trading hours" in reason.lower() or "news blackout" in reason.lower()
            or "news evidence" in reason.lower() or "confidence" in reason.lower()
            or reason.startswith("desk: below") for reason in reasons)
        existing = conn.execute("SELECT reasons,qualified FROM candidates WHERE id=?", (identifier,)).fetchone()
        if existing:
            merged = list(dict.fromkeys(json.loads(existing["reasons"]) + reasons))
            conn.execute("UPDATE candidates SET reasons=?,qualified=? WHERE id=?",
                         (json.dumps(merged),int(bool(existing["qualified"]) and qualified),identifier))
            return identifier
        conn.execute("INSERT OR IGNORE INTO candidates "
                     "(id,account,symbol,side,setup,created_at,signal_bar,target_bar,timeframe,entry,sl,tp,spread,entry_atr,reasons,qualified,status) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (identifier,scope,symbol,side,setup,_now(),bar,target,tf,entry,sl,tp,quality.get("spread",0),context.get("atr"),
                      json.dumps(reasons),int(qualified),status))
    return identifier


def settle_candidate(candidate: dict, bars: list[dict], point: float, cfg, *, include_exit_bar: bool = False) -> dict | None:
    """Pure OHLC replay of bars observed after candidate registration."""
    relevant = [bar for bar in bars if bar["time"] >= candidate["target_bar"]]
    if not relevant:
        return None
    if candidate["status"] == "open" and relevant[0]["time"] > candidate["entered_bar"]:
        return {"status":"missed","closed_at":_now(),"r_multiple":None,"ambiguous":0}
    if candidate["status"] == "waiting":
        if relevant[0]["time"] != candidate["target_bar"]:
            return {"status": "missed", "closed_at": _now(), "r_multiple": None, "ambiguous": 0}
        first = relevant[0]
        spread = float(first.get("spread", 0) or 0) * point or candidate["spread"]
        entry = first["open"] + spread if candidate["side"] == "BUY" else first["open"]
        risk = abs(entry - candidate["sl"])
        entry_atr = float(candidate.get("entry_atr") or 0)
        geometry = candidate["sl"] < entry < candidate["tp"] if candidate["side"] == "BUY" else candidate["tp"] < entry < candidate["sl"]
        rr = abs(candidate["tp"] - entry) / risk if risk else 0
        if not geometry or not math.isfinite(entry_atr) or entry_atr <= 0 or abs(entry - candidate["entry"]) > cfg.max_entry_drift_atr * entry_atr or rr < get_settings().risk.min_risk_reward:
            return {"status": "missed", "closed_at": _now(), "r_multiple": None, "ambiguous": 0}
        candidate = {**candidate, "entry": entry, "entered_bar": first["time"]}
    risk = abs(candidate["entry"] - candidate["sl"])
    for index, bar in enumerate(relevant):
        if candidate.get("entered_bar") and bar["time"] < candidate["entered_bar"]:
            continue
        spread = float(bar.get("spread", 0) or 0) * point or candidate["spread"]
        low = bar["low"] if candidate["side"] == "BUY" else bar["low"] + spread
        high = bar["high"] if candidate["side"] == "BUY" else bar["high"] + spread
        stopped = low <= candidate["sl"] if candidate["side"] == "BUY" else high >= candidate["sl"]
        targeted = high >= candidate["tp"] if candidate["side"] == "BUY" else low <= candidate["tp"]
        if stopped or targeted or index + 1 >= cfg.shadow_horizon_bars:
            opening = bar["open"] + (spread if candidate["side"] == "SELL" else 0)
            stop_fill = min(candidate["sl"], opening) if candidate["side"] == "BUY" else max(candidate["sl"], opening)
            exit_price = stop_fill if stopped else candidate["tp"] if targeted else bar["close"] + (spread if candidate["side"] == "SELL" else 0)
            sign = 1 if candidate["side"] == "BUY" else -1
            result = sign * (exit_price - candidate["entry"]) / risk - cfg.shadow_cost_r
            return {"status": "closed", "entry": candidate["entry"], "entered_bar": candidate["entered_bar"],
                    "closed_at": _now(), "r_multiple": result, "ambiguous": int(stopped and targeted),
                    **({"exit_bar":bar["time"]} if include_exit_bar else {})}
    return {"status": "open", "entry": candidate["entry"], "entered_bar": candidate["entered_bar"]}


def advance(client) -> int:
    from mt5_mcp.entry_quality import closed_frame, _epoch

    cfg = get_settings().quality
    if not cfg.enabled:
        return 0
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM candidates WHERE account=? "
                                                "AND status IN ('waiting','open') ORDER BY created_at LIMIT 100", (account_scope(),))]
    frames = {}
    completed = 0
    for row in rows:
        key = (row["symbol"], row["timeframe"])
        try:
            if key not in frames:
                frame = closed_frame(client.rates(*key, 500))
                bars = [{**bar, "time": _epoch(bar["time"])} for bar in frame.to_dict("records")]
                point = float(client.symbol_info(row["symbol"]).get("point") or 0)
                if point <= 0:
                    raise ValueError("paper symbol point unavailable")
                frames[key] = (bars, point)
            result = settle_candidate(row, *frames[key], cfg)
            if result:
                if result["status"] in ("closed", "missed"):
                    completed += 1
                fields = list(result)
                with _connect() as conn:
                    conn.execute("UPDATE candidates SET " + ",".join(field + "=?" for field in fields) + " WHERE id=?",
                                 [result[field] for field in fields] + [row["id"]])
        except Exception as exc:
            _advance_status["last_error"] = str(exc)
            logger.debug("paper update unavailable for %s: %s", row["symbol"], exc)
    return completed


def schedule_advance(client) -> None:
    if not get_settings().quality.enabled or not _advance_lock.acquire(blocking=False):
        return
    def worker():
        _advance_status.update(running=True,last_error=None)
        try:
            _save_feed_status()
            advance(client)
            _advance_status["last_completed_at"] = _now()
        except Exception as exc:
            _advance_status["last_error"] = str(exc)
        finally:
            _advance_status["running"] = False
            try:
                _save_feed_status()
            except Exception as exc:
                logger.warning("Cannot persist paper feed status: %s", exc)
            finally:
                _advance_lock.release()
    threading.Thread(target=worker, name="paper-outcomes", daemon=True).start()


def _save_feed_status() -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO feed_status VALUES (?,?)",
                     (account_scope(),json.dumps(_advance_status)))


def paper_evidence(conn, key, since: str, now: datetime | None = None) -> dict:
    cfg = get_settings().quality
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT r_multiple FROM candidates WHERE account=? AND symbol=? AND side=? AND setup=? "
                        "AND qualified=1 AND ambiguous=0 AND status='closed' AND created_at>=? "
                        "AND julianday(closed_at)<=julianday(?) AND julianday(closed_at)>=julianday(created_at) "
                        "ORDER BY closed_at DESC LIMIT 100", (*key, since, now.isoformat())).fetchall()
    values = [float(row[0]) for row in rows if row[0] is not None and math.isfinite(float(row[0]))]
    win, loss = sum(max(v,0) for v in values), sum(max(-v,0) for v in values)
    expectancy = sum(values) / len(values) if values else 0
    variance = sum((v - expectancy) ** 2 for v in values) / max(1,len(values)-1)
    lower = expectancy - 1.64 * math.sqrt(variance / len(values)) if values else 0
    pf = win / loss if loss else None
    ready = len(values) >= cfg.shadow_min_trades and expectancy >= cfg.shadow_min_expectancy_r and lower > 0 and (pf is None and win > 0 or pf is not None and pf >= cfg.shadow_min_profit_factor)
    return {"paper_trades":len(values), "paper_expectancy_r":round(expectancy,3), "paper_profit_factor":round(pf,3) if pf is not None else None,
            "paper_lower_bound_r":round(lower,3), "paper_ready":ready}


def requalification(symbol, side, setup, evidence, latest_close, now: datetime) -> dict:
    key = (account_scope(),symbol,side,setup)
    trigger = latest_close.isoformat() if latest_close else None
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM quarantines WHERE account=? AND symbol=? AND side=? AND setup=?", key).fetchone()
        bad = evidence["trades"] >= 8 and (evidence["consecutive_losses"] >= 3 or
              evidence["net_pnl"] < 0 and evidence["profit_factor"] is not None and evidence["profit_factor"] < 0.8)
        if bad and (state is None or state["trigger_close"] != trigger):
            conn.execute("INSERT OR REPLACE INTO quarantines VALUES (?,?,?,?,?,?,?,?,?)",
                         (*key,trigger,now.isoformat(),evidence.get("paused_until"),"paper",None))
            state = conn.execute("SELECT * FROM quarantines WHERE account=? AND symbol=? AND side=? AND setup=?", key).fetchone()
        if not state or state["state"] == "released":
            return {"required":False}
        paper = paper_evidence(conn,key,state["since"],now)
        until = datetime.fromisoformat(state["until_time"]) if state["until_time"] else now
        if paper["paper_ready"] and now >= until:
            conn.execute("UPDATE quarantines SET state='released',released_at=? WHERE account=? AND symbol=? AND side=? AND setup=?",
                         (now.isoformat(),*key))
            return {"required":False, **paper}
        return {"required":True,"since":state["since"],"paused_until":state["until_time"],**paper}


def snapshot() -> dict:
    with _connect() as conn:
        scope = account_scope()
        counts = {row[0]:row[1] for row in conn.execute("SELECT status,COUNT(*) FROM candidates WHERE account=? GROUP BY status", (scope,))}
        states = []
        for row in conn.execute("SELECT * FROM quarantines WHERE account=? AND state='paper'", (scope,)):
            key = (scope,row["symbol"],row["side"],row["setup"])
            states.append({"symbol":row["symbol"],"side":row["side"],"setup":row["setup"],"since":row["since"],
                           **paper_evidence(conn,key,row["since"])})
        recent = [dict(row) for row in conn.execute("SELECT symbol,side,setup,created_at,reasons,status,r_multiple,qualified,ambiguous "
                                                  "FROM candidates WHERE account=? ORDER BY created_at DESC LIMIT 30", (scope,))]
        status = conn.execute("SELECT data FROM feed_status WHERE account=?", (scope,)).fetchone()
        feed = json.loads(status[0]) if status else dict(_advance_status)
    return {"counts":counts,"requalification":states,"recent":recent,"feed":feed,"assumptions":
            "Next full-bar entry; bid/ask spread included; configured additional cost in R; ambiguous bars cannot qualify."}
