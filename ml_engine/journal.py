"""Trade journal: SQLite-backed record of every decision and outcome.

Stores:
  - Every decision (TRADE / NO_TRADE) with full context
  - Management simulation results per trade
  - P&L tracking, win streak, drawdown

Append-only; old records never mutated.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from ml_engine.backtest.management import ManagementResult
from ml_engine.decision import Decision

_DEFAULT_DB = Path("ml_engine_data") / "journal.db"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else _DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            kind TEXT NOT NULL,
            direction TEXT,
            entry REAL,
            stop REAL,
            target REAL,
            risk_reward REAL,
            position_size REAL,
            risk_percent REAL,
            regime TEXT,
            vol_regime TEXT,
            htf_bias TEXT,
            structure_bias TEXT,
            definition TEXT,
            p_1r REAL,
            p_2r REAL,
            p_3r REAL,
            expected_r REAL,
            reason_json TEXT,
            invalidation_json TEXT,
            full_json TEXT
        );

        CREATE TABLE IF NOT EXISTS management (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER,
            setup_id TEXT,
            rule TEXT,
            r_multiple REAL,
            exit_bar INTEGER,
            exit_price REAL,
            max_favorable_r REAL,
            max_adverse_r REAL,
            bars_held INTEGER,
            FOREIGN KEY(decision_id) REFERENCES decisions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
        CREATE INDEX IF NOT EXISTS idx_decisions_kind ON decisions(kind);
        CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
    """)


class TradeJournal:
    """Append-only trade journal backed by SQLite."""

    def __init__(self, db_path: Path | str | None = None):
        self.conn = _connect(db_path)

    def close(self) -> None:
        self.conn.close()

    def log_decision(self, decision: Decision) -> int:
        """Log a decision; returns the row id."""
        d = decision.to_dict()
        cur = self.conn.execute(
            """INSERT INTO decisions
               (timestamp, symbol, kind, direction, entry, stop, target,
                risk_reward, position_size, risk_percent, regime, vol_regime,
                htf_bias, structure_bias, definition, p_1r, p_2r, p_3r,
                expected_r, reason_json, invalidation_json, full_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["timestamp"], d["symbol"], d["kind"], d["direction"],
                d["entry"], d["stop"], d["target"], d["risk_reward"],
                d["position_size"], d["risk_percent"], d["regime"],
                d["vol_regime"], d["htf_bias"], d["structure_bias"],
                d["definition"], d["p_1r"], d["p_2r"], d["p_3r"],
                d["expected_r"],
                json.dumps(d["reason"]), json.dumps(d["invalidation"]),
                json.dumps(d, default=str),
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def log_management(self, decision_id: int, result: ManagementResult) -> int:
        """Log a management simulation result; returns the row id."""
        r = result.to_dict()
        cur = self.conn.execute(
            """INSERT INTO management
               (decision_id, setup_id, rule, r_multiple, exit_bar,
                exit_price, max_favorable_r, max_adverse_r, bars_held)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id, r["setup_id"], r["rule"], r["r_multiple"],
                r["exit_bar"], r["exit_price"],
                r["max_favorable_r"], r["max_adverse_r"], r["bars_held"],
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_trades(self, kind: str = "TRADE", symbol: str | None = None) -> list[dict]:
        """Fetch all TRADE decisions, optionally filtered by symbol."""
        q = "SELECT * FROM decisions WHERE kind = ?"
        params: list = [kind]
        if symbol:
            q += " AND symbol = ?"
            params.append(symbol)
        q += " ORDER BY timestamp"
        rows = self.conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def get_management_for_trade(self, decision_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM management WHERE decision_id = ?", (decision_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        """Quick summary: total trades, win rate, avg R, total R."""
        trades = self.get_trades()
        if not trades:
            return {"total_trades": 0}
        r_vals = [t["expected_r"] for t in trades if t["expected_r"] is not None]
        wins = sum(1 for r in r_vals if r > 0)
        return {
            "total_trades": len(trades),
            "win_rate": wins / len(trades) if trades else 0.0,
            "avg_expected_r": float(np.mean(r_vals)) if r_vals else 0.0,
            "total_expected_r": float(np.sum(r_vals)) if r_vals else 0.0,
            "symbols": list({t["symbol"] for t in trades}),
        }

    def clear(self) -> None:
        """Clear all records (for testing only)."""
        self.conn.execute("DELETE FROM management")
        self.conn.execute("DELETE FROM decisions")
        self.conn.commit()
