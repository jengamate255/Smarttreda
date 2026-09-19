"""
Account Monitor.

Real-time account monitoring: balance, equity, margin, margin level.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, field

logger = logging.getLogger("mt5_mcp.account")


@dataclass
class AccountSnapshot:
    login: int = 0
    server: str = ""
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    leverage: int = 0
    currency: str = "USD"
    profit: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def margin_used_pct(self) -> float:
        if self.equity == 0:
            return 0.0
        return (self.margin / self.equity) * 100

    @property
    def drawdown_pct(self) -> float:
        if self.balance == 0:
            return 0.0
        return ((self.balance - self.equity) / self.balance) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "login": self.login, "server": self.server,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "margin": round(self.margin, 2),
            "free_margin": round(self.free_margin, 2),
            "margin_level": round(self.margin_level, 2),
            "leverage": self.leverage, "currency": self.currency,
            "profit": round(self.profit, 2),
            "margin_used_pct": round(self.margin_used_pct, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "timestamp": self.timestamp.isoformat(),
        }


class AccountMonitor:
    """Monitors account state in real-time."""

    def __init__(self, connection_manager=None):
        self._connection = connection_manager
        self._history: list[AccountSnapshot] = []
        self._peak_equity: float = 0.0

    def snapshot(self) -> AccountSnapshot:
        snap = AccountSnapshot()

        if self._connection and self._connection.is_connected:
            info = self._connection.account_info
            if info:
                snap.login = info.get("login", 0)
                snap.server = info.get("server", "")
                snap.balance = info.get("balance", 0.0)
                snap.equity = info.get("equity", 0.0)
                snap.margin = info.get("margin", 0.0)
                snap.free_margin = info.get("free_margin", 0.0)
                snap.leverage = info.get("leverage", 0)
                snap.currency = info.get("currency", "USD")
                snap.profit = snap.equity - snap.balance
                if snap.margin > 0 and snap.equity > 0:
                    snap.margin_level = (snap.equity / snap.margin) * 100

        self._peak_equity = max(self._peak_equity, snap.equity)
        self._history.append(snap)

        return snap

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._history[-limit:]]

    def get_max_drawdown(self) -> float:
        if not self._history:
            return 0.0
        peak = 0.0
        max_dd = 0.0
        for snap in self._history:
            peak = max(peak, snap.equity)
            if peak > 0:
                dd = ((peak - snap.equity) / peak) * 100
                max_dd = max(max_dd, dd)
        return round(max_dd, 2)

    def health_check(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "status": "success",
            "account": snap.to_dict(),
            "peak_equity": round(self._peak_equity, 2),
            "max_drawdown_pct": self.get_max_drawdown(),
            "history_count": len(self._history),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
