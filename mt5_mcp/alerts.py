"""Persistent price/signal alerts with Telegram notifications.

Alerts live in the journal SQLite database (``alerts`` table) so they survive
restarts. A background checker evaluates each active alert against the live
tick and sends a Telegram message when the condition fires (once per alert).

Usage:
    from mt5_mcp.alerts import create_alert, list_alerts, delete_alert, check_alerts
    create_alert("EURUSD", 1.0850, "below", "EurUsd drops below 1.0850")
    triggered = check_alerts(quote_fn)   # quote_fn(symbol) -> {"bid": ..., "ask": ...}
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.telegram_alerts import send_telegram

logger = logging.getLogger("mt5_mcp.alerts")

_CONDITIONS = {"above", "below"}


def _db() -> sqlite3.Connection:
    # Dedicated alerts DB avoids write-lock contention with the live watchers,
    # which hammer journal.db from multiple processes.
    path = Path(get_journal()._path).parent / "alerts.db"
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            condition TEXT NOT NULL,
            name TEXT DEFAULT '',
            created_at TEXT,
            triggered BOOLEAN DEFAULT 0,
            triggered_at TEXT,
            active BOOLEAN DEFAULT 1
        )
        """
    )
    return con


def create_alert(symbol: str, price: float, condition: str, name: str = "") -> dict[str, Any]:
    """Create a new alert. Returns the stored alert dict."""
    symbol = symbol.upper()
    condition = condition.strip().lower()
    if condition not in _CONDITIONS:
        raise ValueError(f"condition must be one of {sorted(_CONDITIONS)}")
    con = _db()
    try:
        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT INTO alerts (id, symbol, price, condition, name, created_at, triggered, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 1)",
            (alert_id, symbol, float(price), condition, name, now),
        )
        con.commit()
        return {
            "id": alert_id,
            "symbol": symbol,
            "price": float(price),
            "condition": condition,
            "name": name,
            "created_at": now,
            "triggered": False,
            "active": True,
        }
    finally:
        con.close()


def list_alerts(include_triggered: bool = True) -> list[dict[str, Any]]:
    con = _db()
    try:
        if include_triggered:
            rows = con.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alerts WHERE active = 1 AND triggered = 0 ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_alert(alert_id: str) -> bool:
    con = _db()
    try:
        cur = con.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def toggle_alert(alert_id: str) -> dict[str, Any] | None:
    """Toggle the active state of an alert. Returns the updated alert or None."""
    con = _db()
    try:
        row = con.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return None
        new_active = not row["active"]
        con.execute("UPDATE alerts SET active = ? WHERE id = ?", (new_active, alert_id))
        con.commit()
        alert = dict(row)
        alert["active"] = new_active
        return alert
    finally:
        con.close()


def check_alerts(quote_fn: Callable[[str], dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Evaluate active alerts against live prices; fire+notify on trigger.

    ``quote_fn(symbol)`` must return a dict with "bid"/"ask" or None if
    unavailable. Returns the list of alerts that fired this call.
    """
    con = _db()
    fired: list[dict[str, Any]] = []
    try:
        rows = con.execute(
            "SELECT * FROM alerts WHERE active = 1 AND triggered = 0"
        ).fetchall()
        for row in rows:
            symbol = row["symbol"]
            try:
                quote = quote_fn(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("alert %s quote error: %s", row["id"], exc)
                continue
            if not quote:
                continue
            bid = float(quote.get("bid") or 0.0)
            ask = float(quote.get("ask") or 0.0)
            price = row["price"]
            hit = False
            if row["condition"] == "above":
                hit = ask >= price
            elif row["condition"] == "below":
                hit = bid <= price
            if not hit:
                continue
            now = datetime.now(timezone.utc).isoformat()
            con.execute(
                "UPDATE alerts SET triggered = 1, triggered_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            con.commit()
            alert = dict(row)
            alert["triggered_at"] = now
            fired.append(alert)
            text = (
                f"<b>Alert fired</b> {symbol}\n"
                f"{row['name'] or ''}\n"
                f"Price {bid:.5f}/{ask:.5f} is {row['condition']} {price:.5f}"
            )
            send_telegram(text)
            logger.info("alert fired: %s %s %s %.5f (bid=%.5f)", symbol, row["condition"], price, bid)
    finally:
        con.close()
    return fired