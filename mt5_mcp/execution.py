"""
Order Execution Engine.

Handles order placement, modification, and cancellation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger("mt5_mcp.execution")


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    magic: int = 0
    comment: str = ""
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_volume: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None


class OrderExecutor:
    """Executes orders on MT5 terminal."""

    def __init__(self, connection_manager=None):
        self._connection = connection_manager
        self._pending_orders: dict[str, Order] = {}
        self._order_counter = 0

    def _generate_order_id(self) -> str:
        self._order_counter += 1
        return f"order_{datetime.now(timezone.utc).timestamp()}_{self._order_counter}"

    def send_market_order(
        self, symbol: str, side: OrderSide, volume: float,
        stop_loss: float | None = None, take_profit: float | None = None,
        magic: int = 0, comment: str = "",
    ) -> Order:
        order = Order(
            id=self._generate_order_id(), symbol=symbol, side=side,
            order_type=OrderType.MARKET, volume=volume,
            stop_loss=stop_loss, take_profit=take_profit,
            magic=magic, comment=comment,
        )

        if not self._connection or not self._connection.is_connected:
            order.status = OrderStatus.REJECTED
            order.error = "Not connected to MT5"
            return order

        mt5 = self._connection._mt5
        if not mt5:
            # Mock mode
            tick = self._connection.get_tick(symbol)
            if tick:
                order.fill_price = tick["ask"] if side == OrderSide.BUY else tick["bid"]
                order.fill_volume = volume
                order.status = OrderStatus.FILLED
            else:
                order.status = OrderStatus.REJECTED
                order.error = "No tick data"
            return order

        # Real MT5 execution
        action = mt5.TRADE_ACTION_DEAL if order.order_type == OrderType.MARKET else mt5.TRADE_ACTION_PENDING

        request = {
            "action": action,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if order.price:
            request["price"] = order.price
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = mt5.order_send(request)

        if result is None:
            order.status = OrderStatus.REJECTED
            order.error = "order_send returned None"
        elif result.retcode != mt5.TRADE_RETCODE_DONE:
            order.status = OrderStatus.REJECTED
            order.error = f"Error {result.retcode}: {result.comment}"
        else:
            order.fill_price = result.price
            order.fill_volume = result.volume
            order.status = OrderStatus.FILLED

        return order

    def send_limit_order(
        self, symbol: str, side: OrderSide, volume: float, price: float,
        stop_loss: float | None = None, take_profit: float | None = None,
        magic: int = 0, comment: str = "",
    ) -> Order:
        order = Order(
            id=self._generate_order_id(), symbol=symbol, side=side,
            order_type=OrderType.LIMIT, volume=volume, price=price,
            stop_loss=stop_loss, take_profit=take_profit,
            magic=magic, comment=comment,
        )

        if not self._connection or not self._connection.is_connected:
            order.status = OrderStatus.REJECTED
            order.error = "Not connected to MT5"
            return order

        mt5 = self._connection._mt5
        if not mt5:
            order.status = OrderStatus.PENDING
            self._pending_orders[order.id] = order
            return order

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY_LIMIT if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": price,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = mt5.order_send(request)

        if result is None:
            order.status = OrderStatus.REJECTED
            order.error = "order_send returned None"
        elif result.retcode != mt5.TRADE_RETCODE_DONE:
            order.status = OrderStatus.REJECTED
            order.error = f"Error {result.retcode}: {result.comment}"
        else:
            order.status = OrderStatus.PENDING
            self._pending_orders[order.id] = order

        return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if order_id not in self._pending_orders:
            return {"status": "error", "error": "Order not found"}

        order = self._pending_orders[order_id]

        if not self._connection or not self._connection.is_connected:
            return {"status": "error", "error": "Not connected"}

        mt5 = self._connection._mt5
        if not mt5:
            order.status = OrderStatus.CANCELLED
            del self._pending_orders[order_id]
            return {"status": "success", "message": "Order cancelled (mock)"}

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": order_id,
            "symbol": order.symbol,
        }

        result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            order.status = OrderStatus.CANCELLED
            del self._pending_orders[order_id]
            return {"status": "success", "message": "Order cancelled"}
        else:
            error = result.comment if result else "Unknown error"
            return {"status": "error", "error": error}

    def get_pending_orders(self) -> list[dict[str, Any]]:
        return [{
            "id": o.id, "symbol": o.symbol, "side": o.side.value,
            "type": o.order_type.value, "volume": o.volume, "price": o.price,
            "status": o.status.value, "created_at": o.created_at.isoformat(),
        } for o in self._pending_orders.values()]
