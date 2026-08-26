"""Performance tracking for strategies."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.memory.trade_journal import get_journal

log = logging.getLogger("performance_tracker")


class PerformanceTracker:
    """Track and analyze strategy performance."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent / "performance.db"
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize performance database."""
        con = sqlite3.connect(self.db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                sl REAL,
                tp REAL,
                lots REAL,
                pnl REAL,
                rr REAL,
                duration_seconds INTEGER,
                setup TEXT,
                confidence REAL,
                outcome TEXT
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_time 
            ON strategy_performance(strategy_name, timestamp)
        """)
        con.commit()
        con.close()

    def record_trade(self, trade_data: dict):
        """Record a completed trade."""
        con = sqlite3.connect(self.db_path)
        con.execute("""
            INSERT INTO strategy_performance 
            (timestamp, strategy_name, symbol, side, entry_price, exit_price,
             sl, tp, lots, pnl, rr, duration_seconds, setup, confidence, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            trade_data.get("strategy_name"),
            trade_data.get("symbol"),
            trade_data.get("side"),
            trade_data.get("entry_price"),
            trade_data.get("exit_price"),
            trade_data.get("sl"),
            trade_data.get("tp"),
            trade_data.get("lots"),
            trade_data.get("pnl"),
            trade_data.get("rr"),
            trade_data.get("duration_seconds"),
            trade_data.get("setup"),
            trade_data.get("confidence"),
            trade_data.get("outcome", "OPEN"),
        ))
        con.commit()
        con.close()

    def update_trade(self, ticket: int, exit_price: float, pnl: float, outcome: str):
        """Update trade with exit info."""
        con = sqlite3.connect(self.db_path)
        con.execute("""
            UPDATE strategy_performance 
            SET exit_price = ?, pnl = ?, outcome = ?, 
                duration_seconds = (julianday(datetime('now')) - julianday(timestamp)) * 86400
            WHERE ticket = ?
        """, (exit_price, pnl, outcome, ticket))
        con.commit()
        con.close()

    def get_performance_summary(self, strategy_name: str = None, days: int = 30) -> dict:
        """Get performance summary for a strategy."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        
        query = """
            SELECT * FROM strategy_performance
            WHERE timestamp >= datetime('now', ?)
        """
        params = [f'-{days} days']
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        
        query += " ORDER BY timestamp DESC"
        
        rows = con.execute(query, params).fetchall()
        con.close()
        
        if not rows:
            return {"total_trades": 0}
        
        trades = [dict(r) for r in rows]
        closed = [t for t in trades if t["outcome"] != "OPEN"]
        
        if not closed:
            return {"total_trades": len(trades), "open_trades": len(trades)}
        
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        
        total_pnl = sum(t["pnl"] for t in closed)
        win_rate = len(wins) / len(closed) * 100 if closed else 0
        
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        
        return {
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else float('inf'),
        }

    def get_daily_pnl(self, days: int = 30) -> list:
        """Get daily P&L for charting."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        
        rows = con.execute("""
            SELECT date(timestamp) as day, SUM(pnl) as daily_pnl
            FROM strategy_performance
            WHERE timestamp >= datetime('now', ?) AND outcome != 'OPEN'
            GROUP BY date(timestamp)
            ORDER BY day
        """, (f'-{days} days',)).fetchall()
        con.close()
        
        return [{"day": r["day"], "pnl": r["daily_pnl"]} for r in rows]


# Global tracker instance
tracker = PerformanceTracker()


def record_strategy_trade(strategy_name: str, signal, ticket: int, exit_price: float = None, pnl: float = None, outcome: str = "OPEN"):
    """Helper to record strategy trade."""
    data = {
        "strategy_name": strategy_name,
        "symbol": signal.symbol,
        "side": signal.side,
        "entry_price": signal.entry,
        "exit_price": exit_price,
        "sl": signal.sl,
        "tp": signal.tp,
        "lots": signal.lots,
        "pnl": 0 if pnl is None else pnl,
        "rr": signal.rr,
        "setup": signal.setup,
        "confidence": signal.confidence,
        "outcome": outcome,
    }
    tracker.record_trade(data)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test
    t = PerformanceTracker()
    summary = t.get_performance_summary()
    print("Performance summary:", summary)