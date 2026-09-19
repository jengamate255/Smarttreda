"""
Position Manager.

Tracks and manages open positions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("mt5_mcp.positions")


@dataclass
class Position:
    id: int
    symbol: str
    side: str  # "BUY" or "SELL"
    volume: float
    open_price: float
    current_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    magic: int = 0
    comment: str = ""
    open_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pnl: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.net_pnl > 0

    @property
    def net_pnl(self) -> float:
        return self.profit + self.swap + self.commission


class PositionManager:
    """Manages open positions on MT5."""

    def __init__(self, connection_manager=None):
        self._connection = connection_manager
        self._positions: dict[int, Position] = {}

    def refresh_positions(self) -> list[Position]:
        self._positions.clear()

        if not self._connection or not self._connection.is_connected:
            return []

        mt5 = self._connection._mt5
        if not mt5:
            return []

        mt5_positions = mt5.positions_get()
        if mt5_positions is None:
            return []

        for p in mt5_positions:
            pos = Position(
                id=p.ticket, symbol=p.symbol,
                side="BUY" if p.type == 0 else "SELL",
                volume=p.volume, open_price=p.price_open,
                current_price=p.price_current, stop_loss=p.sl,
                take_profit=p.tp, profit=p.profit, swap=p.swap,
                commission=p.comment if hasattr(p, 'commission') else 0.0,
                magic=p.magic, comment=p.comment,
                open_time=datetime.fromtimestamp(p.time, tz=timezone.utc),
            )
            pos.pnl = pos.profit + pos.swap
            self._positions[p.ticket] = pos

        return list(self._positions.values())

    def get_position(self, ticket: int) -> Position | None:
        return self._positions.get(ticket)

    def get_positions_by_symbol(self, symbol: str) -> list[Position]:
        return [p for p in self._positions.values() if p.symbol == symbol]

    def get_total_pnl(self) -> float:
        return sum(p.net_pnl for p in self._positions.values())

    def get_total_volume(self) -> float:
        return sum(p.volume for p in self._positions.values())

    def close_position(self, ticket: int) -> dict[str, Any]:
        if ticket not in self._positions:
            return {"status": "error", "error": "Position not found"}

        pos = self._positions[ticket]

        if not self._connection or not self._connection.is_connected:
            return {"status": "error", "error": "Not connected"}

        mt5 = self._connection._mt5
        if not mt5:
            del self._positions[ticket]
            return {"status": "success", "message": "Position closed (mock)", "pnl": pos.pnl}

        close_type = mt5.ORDER_TYPE_SELL if pos.side == "BUY" else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        close_price = tick.bid if pos.side == "BUY" else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "magic": pos.magic,
            "comment": "Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            del self._positions[ticket]
            return {"status": "success", "message": "Position closed", "pnl": pos.pnl}
        else:
            error = result.comment if result else "Unknown error"
            return {"status": "error", "error": error}

    def modify_position(self, ticket: int, stop_loss: float | None = None,
                        take_profit: float | None = None) -> dict[str, Any]:
        if ticket not in self._positions:
            return {"status": "error", "error": "Position not found"}

        pos = self._positions[ticket]

        if not self._connection or not self._connection.is_connected:
            return {"status": "error", "error": "Not connected"}

        mt5 = self._connection._mt5
        if not mt5:
            if stop_loss is not None:
                pos.stop_loss = stop_loss
            if take_profit is not None:
                pos.take_profit = take_profit
            return {"status": "success", "message": "Position modified (mock)"}

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": stop_loss if stop_loss is not None else pos.stop_loss,
            "tp": take_profit if take_profit is not None else pos.take_profit,
        }

        result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            if stop_loss is not None:
                pos.stop_loss = stop_loss
            if take_profit is not None:
                pos.take_profit = take_profit
            return {"status": "success", "message": "Position modified"}
        else:
            error = result.comment if result else "Unknown error"
            return {"status": "error", "error": error}

    def get_summary(self) -> dict[str, Any]:
        positions = list(self._positions.values())
        return {
            "total_positions": len(positions),
            "total_pnl": round(self.get_total_pnl(), 2),
            "total_volume": round(self.get_total_volume(), 2),
            "winning": sum(1 for p in positions if p.is_profitable),
            "losing": sum(1 for p in positions if not p.is_profitable),
            "by_symbol": {
                sym: len(self.get_positions_by_symbol(sym))
                for sym in set(p.symbol for p in positions)
            } if positions else {},
        }
