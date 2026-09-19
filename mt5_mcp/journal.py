"""
Trade Journal & Logging.

Records all trade activity for analysis and auditing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("mt5_mcp.journal")


@dataclass
class TradeEntry:
    id: str
    symbol: str
    side: str
    volume: float
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    status: str = "OPEN"
    open_time: str = ""
    close_time: str | None = None
    duration_minutes: float = 0.0
    setup_type: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeJournal:
    """Records and retrieves trade history."""

    def __init__(self, journal_dir: str | Path | None = None):
        self._journal_dir = Path(journal_dir) if journal_dir else Path("trade_journal")
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[TradeEntry] = []
        self._load_today()

    def _today_file(self) -> Path:
        return self._journal_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"

    def _load_today(self):
        f = self._today_file()
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._entries = [TradeEntry(**e) for e in data]
            except Exception as e:
                logger.warning("Failed to load journal: %s", e)
                self._entries = []

    def _save_today(self):
        f = self._today_file()
        f.write_text(json.dumps([e.to_dict() for e in self._entries], indent=2), encoding="utf-8")

    def record_open(
        self, trade_id: str, symbol: str, side: str, volume: float,
        entry_price: float, stop_loss: float | None = None,
        take_profit: float | None = None, setup_type: str = "",
        notes: str = "", tags: list[str] | None = None,
    ) -> TradeEntry:
        entry = TradeEntry(
            id=trade_id, symbol=symbol, side=side, volume=volume,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            status="OPEN", open_time=datetime.now(timezone.utc).isoformat(),
            setup_type=setup_type, notes=notes, tags=tags or [],
        )
        self._entries.append(entry)
        self._save_today()
        return entry

    def record_close(
        self, trade_id: str, exit_price: float, pnl: float, fees: float = 0.0,
        notes: str = "",
    ) -> TradeEntry | None:
        for entry in self._entries:
            if entry.id == trade_id and entry.status == "OPEN":
                entry.exit_price = exit_price
                entry.pnl = pnl
                entry.fees = fees
                entry.net_pnl = pnl - fees
                entry.status = "CLOSED"
                entry.close_time = datetime.now(timezone.utc).isoformat()
                if entry.open_time:
                    try:
                        open_dt = datetime.fromisoformat(entry.open_time)
                        close_dt = datetime.fromisoformat(entry.close_time)
                        entry.duration_minutes = (close_dt - open_dt).total_seconds() / 60
                    except Exception:
                        pass
                self._save_today()
                return entry
        return None

    def get_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        closed = [e for e in self._entries if e.status == "CLOSED"]
        if not closed:
            return {"total": len(self._entries), "open": len(self._entries), "closed": 0}
        wins = [e for e in closed if e.net_pnl > 0]
        losses = [e for e in closed if e.net_pnl <= 0]
        total_pnl = sum(e.net_pnl for e in closed)
        avg_win = sum(e.net_pnl for e in wins) / len(wins) if wins else 0.0
        avg_loss = sum(e.net_pnl for e in losses) / len(losses) if losses else 0.0
        return {
            "total": len(self._entries),
            "open": len(self._entries) - len(closed),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 3) if closed else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_duration_min": round(sum(e.duration_minutes for e in closed) / len(closed), 1) if closed else 0.0,
        }

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "success",
            "entries_today": len(self._entries),
            "stats": self.get_stats(),
            "journal_dir": str(self._journal_dir),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
