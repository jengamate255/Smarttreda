"""Trade journal backed by SQLite.

Stores every trade (auto-synced from MT5 history) plus discretionary notes:
reason, mistakes, lessons, screenshot path, market conditions, setup type.
Also keeps an audit log of every MCP tool call.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.config import get_settings

logger = logging.getLogger("mt5_mcp.journal")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER UNIQUE,
    symbol TEXT,
    side TEXT,
    setup TEXT DEFAULT 'manual',
    timeframe TEXT,
    entry REAL,
    exit_price REAL,
    sl REAL,
    tp REAL,
    lots REAL,
    open_time TEXT,
    close_time TEXT,
    pnl REAL,
    outcome TEXT,
    reason TEXT,
    mistakes TEXT,
    lessons TEXT,
    screenshot_path TEXT,
    market_conditions TEXT,
    confidence REAL,
    trade_score REAL,
    zone_strength REAL,
    session_hour INTEGER,
    duration_seconds INTEGER,
    rr REAL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    user TEXT,
    tool TEXT,
    inputs TEXT,
    outputs TEXT,
    duration_ms REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    kind TEXT,
    message TEXT
);

-- Signal quality tracking: log every signal generated and its eventual outcome
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE,              -- UUID for this signal
    ticket INTEGER,                     -- MT5 ticket if order placed (nullable)
    symbol TEXT,
    side TEXT,                          -- BUY/SELL
    setup TEXT,                         -- boom_crash_spike_drift, forex_zone_breakout, gold_ndx_scalp, etc.
    timeframe TEXT,
    entry REAL,
    sl REAL,
    tp REAL,
    lots REAL,
    confidence REAL,                    -- Signal confidence at generation time
    setup_confidence REAL,              -- Setup-specific confidence (e.g., spike confidence)
    trend_confidence REAL,
    zone_strength REAL,
    confirmation_confidence REAL,
    rr REAL,                            -- Risk/reward at signal time
    sl_pips REAL,
    tp_pips REAL,
    atr REAL,
    range_high REAL,
    range_low REAL,
    atr_value REAL,
    compactness REAL,
    trend_dir TEXT,                     -- bull/bear/flat
    market_conditions TEXT,             -- JSON string with market context
    signal_time TEXT,                   -- When signal was generated
    trade_time TEXT,                    -- When order was actually placed (if filled)
    close_time TEXT,                    -- When position closed
    filled BOOLEAN DEFAULT 0,           -- Whether signal resulted in a fill
    close_price REAL,                   -- Exit price
    pnl REAL,                           -- Realized P&L
    outcome TEXT,                       -- win/loss/breakeven/open
    r_multiple REAL,                    -- PnL / risk (R multiple)
    session_hour INTEGER,               -- Hour of signal (0-23 UTC)
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_setup ON signals(setup);
CREATE INDEX IF NOT EXISTS idx_signals_signal_time ON signals(signal_time);
CREATE INDEX IF NOT EXISTS idx_signals_filled ON signals(filled);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER UNIQUE,
    symbol TEXT,
    side TEXT,
    setup TEXT DEFAULT 'manual',
    timeframe TEXT,
    entry REAL,
    exit_price REAL,
    sl REAL,
    tp REAL,
    lots REAL,
    open_time TEXT,
    close_time TEXT,
    pnl REAL,
    outcome TEXT,
    reason TEXT,
    mistakes TEXT,
    lessons TEXT,
    screenshot_path TEXT,
    market_conditions TEXT,
    confidence REAL,
    trade_score REAL,
    zone_strength REAL,
    session_hour INTEGER,
    duration_seconds INTEGER,
    rr REAL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    user TEXT,
    tool TEXT,
    inputs TEXT,
    outputs TEXT,
    duration_ms REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    kind TEXT,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_setup ON trades(setup);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(time);
"""


class Journal:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_trade(self, **fields: Any) -> int:
        ticket = fields.pop("ticket", 0)
        now = self._now()
        data = {
            "ticket": int(ticket) if ticket else None,
            "symbol": fields.get("symbol"),
            "side": fields.get("side"),
            "setup": fields.get("setup", "manual"),
            "timeframe": fields.get("timeframe"),
            "entry": fields.get("entry"),
            "exit_price": fields.get("exit_price"),
            "sl": fields.get("sl"),
            "tp": fields.get("tp"),
            "lots": fields.get("lots"),
            "open_time": fields.get("open_time"),
            "close_time": fields.get("close_time") or now,
            "pnl": fields.get("pnl"),
            "outcome": fields.get("outcome"),
            "reason": fields.get("reason"),
            "mistakes": fields.get("mistakes"),
            "lessons": fields.get("lessons"),
            "screenshot_path": fields.get("screenshot_path"),
            "market_conditions": fields.get("market_conditions"),
            "confidence": fields.get("confidence"),
            "trade_score": fields.get("trade_score"),
            "zone_strength": fields.get("zone_strength"),
            "session_hour": fields.get("session_hour"),
            "duration_seconds": fields.get("duration_seconds"),
            "rr": fields.get("rr"),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            conn = self._conn()
            if data["ticket"] is not None:
                existing = conn.execute(
                    "SELECT id FROM trades WHERE ticket = ?", (data["ticket"],)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE trades SET updated_at = ?, pnl = COALESCE(?, pnl), exit_price = COALESCE(?, exit_price), "
                        "outcome = COALESCE(?, outcome), close_time = COALESCE(?, close_time) WHERE ticket = ?",
                        (
                            now,
                            data["pnl"],
                            data["exit_price"],
                            data["outcome"],
                            data["close_time"],
                            data["ticket"],
                        ),
                    )
                    return int(existing["id"])
            cursor = conn.execute(
                """INSERT INTO trades (
                    ticket, symbol, side, setup, timeframe, entry, exit_price, sl, tp, lots,
                    open_time, close_time, pnl, outcome, reason, mistakes, lessons, screenshot_path,
                    market_conditions, confidence, trade_score, zone_strength, session_hour,
                    duration_seconds, rr, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["ticket"],
                    data["symbol"],
                    data["side"],
                    data["setup"],
                    data["timeframe"],
                    data["entry"],
                    data["exit_price"],
                    data["sl"],
                    data["tp"],
                    data["lots"],
                    data["open_time"],
                    data["close_time"],
                    data["pnl"],
                    data["outcome"],
                    data["reason"],
                    data["mistakes"],
                    data["lessons"],
                    data["screenshot_path"],
                    data["market_conditions"],
                    data["confidence"],
                    data["trade_score"],
                    data["zone_strength"],
                    data["session_hour"],
                    data["duration_seconds"],
                    data["rr"],
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_notes(self, ticket: int, **fields: Any) -> bool:
        allowed = {
            "reason",
            "mistakes",
            "lessons",
            "screenshot_path",
            "market_conditions",
            "setup",
            "timeframe",
            "confidence",
            "trade_score",
            "zone_strength",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        if not all(re.fullmatch(r"[a-z_]+", k) for k in updates):
            return False
        updates["updated_at"] = self._now()
        columns = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                f"UPDATE trades SET {columns} WHERE ticket = ?",
                (*updates.values(), ticket),
            )  # noqa: S608
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Signal Quality Tracking
    # ------------------------------------------------------------------
    def log_signal(self, **fields: Any) -> str:
        """Log a new signal generated by a strategy.
        
        Returns the signal_id (UUID) for tracking."""
        import uuid
        signal_id = fields.pop("signal_id", None) or str(uuid.uuid4())
        now = self._now()
        data = {
            "signal_id": signal_id,
            "ticket": fields.get("ticket"),
            "symbol": fields.get("symbol"),
            "side": fields.get("side"),
            "setup": fields.get("setup", "manual"),
            "timeframe": fields.get("timeframe"),
            "entry": fields.get("entry"),
            "sl": fields.get("sl"),
            "tp": fields.get("tp"),
            "lots": fields.get("lots"),
            "confidence": fields.get("confidence"),
            "setup_confidence": fields.get("setup_confidence"),
            "trend_confidence": fields.get("trend_confidence"),
            "zone_strength": fields.get("zone_strength"),
            "confirmation_confidence": fields.get("confirmation_confidence"),
            "rr": fields.get("rr"),
            "sl_pips": fields.get("sl_pips"),
            "tp_pips": fields.get("tp_pips"),
            "atr": fields.get("atr"),
            "range_high": fields.get("range_high"),
            "range_low": fields.get("range_low"),
            "atr_value": fields.get("atr_value"),
            "compactness": fields.get("compactness"),
            "trend_dir": fields.get("trend_dir"),
            "market_conditions": fields.get("market_conditions"),
            "signal_time": fields.get("signal_time") or now,
            "trade_time": fields.get("trade_time"),
            "close_time": fields.get("close_time"),
            "ticket": fields.get("ticket"),
            "filled": fields.get("filled", False),
            "close_price": fields.get("close_price"),
            "pnl": fields.get("pnl"),
            "outcome": fields.get("outcome"),
            "r_multiple": fields.get("r_multiple"),
            "session_hour": fields.get("session_hour"),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                """INSERT INTO signals (
                    signal_id, ticket, symbol, side, setup, timeframe,
                    entry, sl, tp, lots, confidence, setup_confidence,
                    trend_confidence, zone_strength, confirmation_confidence,
                    rr, sl_pips, tp_pips, atr,
                    range_high, range_low, atr_value, compactness, trend_dir,
                    market_conditions, signal_time, trade_time, close_time,
                    filled, close_price, pnl, outcome,
                    r_multiple, session_hour, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["signal_id"],
                    data["ticket"],
                    data["symbol"],
                    data["side"],
                    data["setup"],
                    data["timeframe"],
                    data["entry"],
                    data["sl"],
                    data["tp"],
                    data["lots"],
                    data["confidence"],
                    data["setup_confidence"],
                    data["trend_confidence"],
                    data["zone_strength"],
                    data["confirmation_confidence"],
                    data["rr"],
                    data["sl_pips"],
                    data["tp_pips"],
                    data["atr"],
                    data["range_high"],
                    data["range_low"],
                    data["atr_value"],
                    data["compactness"],
                    data["trend_dir"],
                    data["market_conditions"],
                    data["signal_time"],
                    data["trade_time"],
                    data["close_time"],
                    int(data["filled"]),
                    data["close_price"],
                    data["pnl"],
                    data["outcome"],
                    data["r_multiple"],
                    data["session_hour"],
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            conn.commit()
            return signal_id

    def update_signal_outcome(self, signal_id: str, **fields: Any) -> bool:
        """Update a signal with its outcome when the trade closes."""
        allowed = {
            "filled",
            "trade_time",
            "close_time",
            "close_price",
            "pnl",
            "outcome",
            "r_multiple",
            "ticket",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = self._now()
        columns = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                f"UPDATE signals SET {columns} WHERE signal_id = ?",
                (*updates.values(), signal_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_signal_stats(
        self,
        symbol: str = "",
        setup: str = "",
        min_confidence: float = 0.0,
        days: int = 30,
    ) -> dict:
        """Get signal quality statistics."""
        from datetime import datetime, timedelta, timezone as tz
        cutoff = (datetime.now(tz.utc) - timedelta(days=days)).isoformat()
        
        where = ["signal_time >= ?"]
        params = [cutoff]
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if setup:
            where.append("setup = ?")
            params.append(setup)
        if min_confidence > 0:
            where.append("confidence >= ?")
            params.append(min_confidence)
        
        where_clause = " AND ".join(where)
        
        with self._lock:
            conn = self._conn()
            
            # Overall stats
            total = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where_clause}", params
            ).fetchone()[0]
            
            filled = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND filled = 1", params
            ).fetchone()[0]
            
            closed = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND outcome IS NOT NULL", params
            ).fetchone()[0]
            
            wins = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND outcome = 'win'", params
            ).fetchone()[0]
            
            losses = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND outcome = 'loss'", params
            ).fetchone()[0]
            
            avg_r = conn.execute(
                f"SELECT AVG(r_multiple) FROM signals WHERE {where_clause} AND r_multiple IS NOT NULL", params
            ).fetchone()[0]
            
            profit_factor = 0.0
            gross_win = conn.execute(
                f"SELECT SUM(r_multiple) FROM signals WHERE {where_clause} AND r_multiple > 0", params
            ).fetchone()[0] or 0
            gross_loss = conn.execute(
                f"SELECT SUM(ABS(r_multiple)) FROM signals WHERE {where_clause} AND r_multiple < 0", params
            ).fetchone()[0] or 0
            if gross_loss > 0:
                profit_factor = gross_win / gross_loss
            
            # By confidence tier
            conf_tiers = []
            for tier_name, lo, hi in [("0-25%", 0, 25), ("25-50%", 25, 50), ("50-75%", 50, 75), ("75-100%", 75, 100)]:
                tier_params = params + [lo, hi]
                tier_total = conn.execute(
                    f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND confidence >= ? AND confidence < ?", 
                    tier_params
                ).fetchone()[0]
                tier_wins = conn.execute(
                    f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND confidence >= ? AND confidence < ? AND outcome = 'win'", 
                    tier_params
                ).fetchone()[0]
                tier_avg_r = conn.execute(
                    f"SELECT AVG(r_multiple) FROM signals WHERE {where_clause} AND confidence >= ? AND confidence < ? AND r_multiple IS NOT NULL", 
                    tier_params
                ).fetchone()[0]
                if tier_total > 0:
                    conf_tiers.append({
                        "tier": tier,
                        "total": tier_total,
                        "win_rate": round(tier_wins / tier_total * 100, 1) if tier_total else 0,
                        "avg_r": round(tier_avg_r, 2) if tier_avg_r else 0,
                    })
            
            # By setup
            setup_stats = []
            setups = conn.execute(
                f"SELECT setup, COUNT(*) as cnt FROM signals WHERE {where_clause} GROUP BY setup ORDER BY cnt DESC",
                params
            ).fetchall()
            for row in setups:
                s_params = params + [row["setup"]]
                s_wins = conn.execute(
                    f"SELECT COUNT(*) FROM signals WHERE {where_clause} AND setup = ? AND outcome = 'win'", 
                    s_params
                ).fetchone()[0]
                s_avg_r = conn.execute(
                    f"SELECT AVG(r_multiple) FROM signals WHERE {where_clause} AND setup = ? AND r_multiple IS NOT NULL", 
                    s_params
                ).fetchone()[0]
                setup_stats.append({
                    "setup": row["setup"],
                    "total": row["cnt"],
                    "win_rate": round(s_wins / row["cnt"] * 100, 1) if row["cnt"] else 0,
                    "avg_r": round(s_avg_r, 2) if s_avg_r else 0,
                })
            
            return {
                "total_signals": total,
                "filled": filled,
                "closed": closed,
                "fill_rate": round(filled / total * 100, 1) if total else 0,
                "close_rate": round(closed / total * 100, 1) if total else 0,
                "win_rate": round(wins / closed * 100, 1) if closed else 0,
                "profit_factor": round(profit_factor, 2),
                "avg_r": round(avg_r, 2) if avg_r else 0,
                "wins": wins,
                "losses": losses,
                "by_confidence_tier": conf_tiers,
                "by_setup": setup_stats,
            }

    def sync_from_mt5(self, client: Any, days: int = 30) -> dict:
        """Sync closed MT5 positions into the journal.

        DEAL entries (entry=IN/OUT) are the canonical round-trip record --
        on Deriv the opening ORDER has price_open=0.0, so entry price, exit
        price, volume and P&L are taken from the deals. Positions without
        deals fall back to closed orders grouped by position_id."""
        added = 0
        updated = 0

        def record(
            symbol: str,
            side: str,
            entry_price: float,
            exit_price: float,
            lots: float,
            open_ts: int,
            close_ts: int,
            profit: float,
            ticket: int,
        ) -> None:
            nonlocal added, updated
            outcome = "win" if profit > 0 else "loss" if profit < 0 else "breakeven"
            duration = max(0, close_ts - open_ts) if close_ts and open_ts else None

            existing = self.get_trade(ticket)
            if existing:
                if existing.get("pnl") != profit:
                    self.record_trade(
                        ticket=ticket,
                        pnl=profit,
                        exit_price=exit_price or None,
                        outcome=outcome,
                        close_time=datetime.fromtimestamp(
                            close_ts, tz=timezone.utc
                        ).isoformat()
                        if close_ts
                        else None,
                    )
                    updated += 1
            else:
                self.record_trade(
                    ticket=ticket,
                    symbol=symbol,
                    side=side,
                    entry=entry_price or None,
                    exit_price=exit_price or None,
                    lots=lots or None,
                    open_time=datetime.fromtimestamp(
                        open_ts, tz=timezone.utc
                    ).isoformat()
                    if open_ts
                    else None,
                    close_time=datetime.fromtimestamp(
                        close_ts, tz=timezone.utc
                    ).isoformat()
                    if close_ts
                    else None,
                    pnl=profit,
                    outcome=outcome,
                    session_hour=datetime.fromtimestamp(open_ts, tz=timezone.utc).hour
                    if open_ts
                    else None,
                    duration_seconds=duration,
                )
                added += 1

        try:
            deals = client.deals(days=days)
        except Exception as exc:
            logger.warning("sync_from_mt5 (deals) failed: %s", exc)
            deals = []

        by_position: dict[int, list[dict]] = {}
        for deal in deals:
            pid = int(deal.get("position_id", 0) or 0)
            if pid:
                by_position.setdefault(pid, []).append(deal)

        for pid, position_deals in by_position.items():
            ins = [d for d in position_deals if int(d.get("entry", -1)) == 0]
            outs = [d for d in position_deals if int(d.get("entry", -1)) == 1]
            if not outs:
                continue
            in_deal = min(ins, key=lambda d: int(d.get("time", 0) or 0)) if ins else None
            out_deal = max(outs, key=lambda d: int(d.get("time", 0) or 0))
            profit = float(
                sum(
                    float(d.get("profit", 0.0) or 0.0)
                    + float(d.get("swap", 0.0) or 0.0)
                    + float(d.get("fee", 0.0) or 0.0)
                    for d in outs
                )
            )
            side = "BUY" if int((in_deal or out_deal).get("type", 0) or 0) == 0 else "SELL"
            record(
                symbol=str((in_deal or out_deal).get("symbol", "")),
                side=side,
                entry_price=float(in_deal.get("price", 0.0) or 0.0) if in_deal else 0.0,
                exit_price=float(out_deal.get("price", 0.0) or 0.0),
                lots=float(
                    sum(float(d.get("volume", 0.0) or 0.0) for d in ins)
                    if ins
                    else float(outs[0].get("volume", 0.0) or 0.0)
                ),
                open_ts=int((in_deal or out_deal).get("time", 0) or 0),
                close_ts=int(out_deal.get("time", 0) or 0),
                profit=profit,
                ticket=pid,
            )

        if not deals:
            try:
                orders = client.closed_orders(days=days)
            except Exception as exc:
                logger.warning("sync_from_mt5 (orders) failed: %s", exc)
                return {"added": added, "updated": updated, "error": str(exc)}

            groups: dict[int, list[dict]] = {}
            standalone: list[dict] = []
            for order in orders:
                pid = int(order.get("position_id", 0) or 0)
                if pid:
                    groups.setdefault(pid, []).append(order)
                else:
                    standalone.append(order)

            def record_from_orders(open_order: dict, close_order: dict, ticket: int) -> None:
                profit = float(close_order.get("profit", 0.0) or 0.0)
                side = "BUY" if int(open_order.get("type", 0) or 0) == 0 else "SELL"
                volume = float(
                    open_order.get("volume_initial")
                    or open_order.get("volume")
                    or open_order.get("volume_current")
                    or 0.0
                )
                close_price = float(
                    close_order.get("price_current")
                    or close_order.get("price_open")
                    or 0.0
                )
                record(
                    symbol=str(open_order.get("symbol", "")),
                    side=side,
                    entry_price=float(open_order.get("price_open", 0.0) or 0.0),
                    exit_price=close_price,
                    lots=volume,
                    open_ts=int(open_order.get("time_setup", 0) or 0),
                    close_ts=int(close_order.get("time_done", 0) or 0),
                    profit=profit,
                    ticket=ticket,
                )

            for order in standalone:
                record_from_orders(order, order, int(order.get("ticket", 0) or 0))

            for orders_in_position in groups.values():
                if len(orders_in_position) > 1:
                    orders_in_position.sort(
                        key=lambda o: int(o.get("time_setup", 0) or 0)
                    )
                    open_order, close_order = (
                        orders_in_position[0],
                        orders_in_position[-1],
                    )
                else:
                    open_order = close_order = orders_in_position[0]
                ticket = int(orders_in_position[0].get("position_id", 0) or 0)
                if not ticket:
                    continue
                record_from_orders(open_order, close_order, ticket)

        logger.info("journal sync: added=%d updated=%d", added, updated)
        return {"added": added, "updated": updated}

    def get_trade(self, ticket: int) -> dict | None:
        with self._lock:
            row = (
                self._conn()
                .execute("SELECT * FROM trades WHERE ticket = ?", (ticket,))
                .fetchone()
            )
            return dict(row) if row else None

    def list_trades(
        self, limit: int = 100, symbol: str = "", setup: str = "", outcome: str = ""
    ) -> list[dict]:
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if setup:
            query += " AND setup = ?"
            params.append(setup)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        query += " ORDER BY COALESCE(close_time, created_at) DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn().execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def record_event(self, kind: str, message: str) -> int:
        """Record a compliance/risk event (halt, revenge pause, manual resume)."""
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO events (time, kind, message) VALUES (?, ?, ?)",
                    (self._now(), kind, message[:2000]),
                )
                return int(cur.lastrowid)

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, time, kind, message FROM events "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [
                    {"id": r[0], "time": r[1], "kind": r[2], "message": r[3]}
                    for r in rows
                ]

    def audit_write(
        self,
        tool: str,
        user: str,
        inputs: Any,
        outputs: Any,
        duration_ms: float,
        error: str | None,
    ) -> None:
        with self._lock:
            self._conn().execute(
                "INSERT INTO audit (time, user, tool, inputs, outputs, duration_ms, error) VALUES (?,?,?,?,?,?,?)",
                (
                    self._now(),
                    user,
                    tool,
                    json.dumps(inputs, default=str)[:4000],
                    str(outputs)[:4000],
                    duration_ms,
                    error,
                ),
            )
            self._conn().commit()

    def recent_audit(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = (
                self._conn()
                .execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
                .fetchall()
            )
            return [dict(r) for r in rows]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_journal: Journal | None = None
_journal_lock = threading.Lock()


def get_journal() -> Journal:
    global _journal
    with _journal_lock:
        if _journal is None:
            _journal = Journal(get_settings().memory.db_path)
        return _journal
