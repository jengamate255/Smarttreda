"""
MT5 Live Connection Manager.

Handles connection to MetaTrader 5 terminal for live trading.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum

logger = logging.getLogger("mt5_mcp.connection")


class ConnectionStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


class MT5ConnectionManager:
    """Manages connection to MetaTrader 5 terminal."""

    def __init__(self):
        self._status = ConnectionStatus.DISCONNECTED
        self._terminal_info: dict[str, Any] = {}
        self._account_info: dict[str, Any] = {}
        self._mt5 = None

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    @property
    def account_info(self) -> dict[str, Any]:
        return self._account_info.copy()

    @property
    def terminal_info(self) -> dict[str, Any]:
        return self._terminal_info.copy()

    def connect(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int = 10000,
    ) -> dict[str, Any]:
        self._status = ConnectionStatus.CONNECTING
        try:
            try:
                import MetaTrader5 as mt5
                self._mt5 = mt5
            except ImportError:
                logger.warning("MetaTrader5 not installed, using mock mode")
                self._mt5 = None
                self._status = ConnectionStatus.CONNECTED
                self._terminal_info = {"name": "Mock Terminal", "version": "1.0.0", "build": 0, "connected": True}
                self._account_info = {
                    "login": login or 0, "server": server or "Mock",
                    "balance": 10000.0, "equity": 10000.0, "margin": 0.0,
                    "free_margin": 10000.0, "leverage": 100, "currency": "USD",
                }
                return {"status": "success", "message": "Connected (mock mode)", "account": self._account_info}

            init_kwargs = {}
            if path:
                init_kwargs["path"] = path
            if timeout:
                init_kwargs["timeout"] = timeout

            if not mt5.initialize(**init_kwargs):
                self._status = ConnectionStatus.ERROR
                return {"status": "error", "error": f"Initialize failed: {mt5.last_error()}"}

            if login and password:
                authorized = mt5.login(login, password=password, server=server or "")
                if not authorized:
                    self._status = ConnectionStatus.ERROR
                    mt5.shutdown()
                    return {"status": "error", "error": f"Login failed: {mt5.last_error()}"}

            terminal = mt5.terminal_info()
            account = mt5.account_info()

            self._terminal_info = {
                "name": terminal.name if terminal else "Unknown",
                "version": str(terminal.version) if terminal else "Unknown",
                "build": terminal.build if terminal else 0,
                "connected": terminal.connected if terminal else False,
            }
            self._account_info = {
                "login": account.login if account else 0,
                "server": account.server if account else "Unknown",
                "balance": account.balance if account else 0.0,
                "equity": account.equity if account else 0.0,
                "margin": account.margin if account else 0.0,
                "free_margin": account.margin_free if account else 0.0,
                "leverage": account.leverage if account else 0,
                "currency": account.currency if account else "USD",
            }

            self._status = ConnectionStatus.CONNECTED
            return {"status": "success", "message": "Connected to MT5", "terminal": self._terminal_info, "account": self._account_info}

        except Exception as e:
            self._status = ConnectionStatus.ERROR
            return {"status": "error", "error": str(e)}

    def disconnect(self) -> dict[str, Any]:
        try:
            if self._mt5 and self.is_connected:
                self._mt5.shutdown()
            self._status = ConnectionStatus.DISCONNECTED
            self._terminal_info = {}
            self._account_info = {}
            return {"status": "success", "message": "Disconnected from MT5"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        if not self.is_connected:
            return None
        if not self._mt5:
            return {"symbol": symbol, "point": 0.0001, "digits": 5, "spread": 2,
                    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}
        info = self._mt5.symbol_info(symbol)
        if info is None:
            return None
        return {"symbol": info.name, "point": info.point, "digits": info.digits,
                "spread": info.spread, "volume_min": info.volume_min,
                "volume_max": info.volume_max, "volume_step": info.volume_step}

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        if not self.is_connected:
            return None
        if not self._mt5:
            return {"symbol": symbol, "bid": 1.1000, "ask": 1.1002, "last": 1.1001,
                    "volume": 1.0, "time": datetime.now(timezone.utc).isoformat()}
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"symbol": symbol, "bid": tick.bid, "ask": tick.ask, "last": tick.last,
                "volume": tick.volume, "time": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat()}

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "success",
            "connection_status": self._status.value,
            "is_connected": self.is_connected,
            "terminal": self._terminal_info,
            "account": self._account_info if self._account_info else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
