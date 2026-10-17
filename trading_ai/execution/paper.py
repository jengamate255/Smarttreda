"""Paper trading broker with realistic execution simulation."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SpreadModel(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"
    TIME_BASED = "time_based"


class SlippageModel(str, Enum):
    FIXED = "fixed"
    PROPORTIONAL = "proportional"
    RANDOM = "random"


@dataclass
class OrderBookLevel:
    price: float
    volume: float


@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    def mid_price(self) -> float | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return None


@dataclass
class Order:
    order_id: UUID
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    reject_reason: str | None = None
    partial_fills: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    position_id: UUID
    symbol: str
    side: PositionSide
    volume: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    commission: float = 0.0
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountInfo:
    balance: float = 10000.0
    equity: float = 10000.0
    margin: float = 0.0
    free_margin: float = 10000.0
    margin_level: float = 0.0
    floating_pnl: float = 0.0
    total_commission: float = 0.0


@dataclass
class SymbolInfo:
    symbol: str
    point: float = 0.0001
    digits: int = 5
    contract_size: float = 100000.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    spread: float = 0.00015
    swap_long: float = -0.5
    swap_short: float = 0.5


class PaperBroker:
    """Simulates realistic broker execution for paper trading.

    Features:
        - Configurable spread model (fixed, variable, time-based)
        - Configurable slippage model (fixed, proportional, random)
        - Order book depth simulation
        - Partial fill simulation
        - SL/TP execution
        - Order rejection simulation
        - Latency simulation
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._initial_balance: float = config.get("initial_balance", 10000.0)
        self._spread_model: SpreadModel = SpreadModel(config.get("spread_model", "variable"))
        self._slippage_model: SlippageModel = SlippageModel(config.get("slippage_model", "random"))
        self._base_spread: float = config.get("base_spread", 0.00015)
        self._fixed_slippage: float = config.get("fixed_slippage", 0.00005)
        self._proportional_slippage: float = config.get("proportional_slippage", 0.0001)
        self._max_slippage: float = config.get("max_slippage", 0.001)
        self._commission_per_lot: float = config.get("commission_per_lot", 7.0)
        self._partial_fill_chance: float = config.get("partial_fill_chance", 0.1)
        self._partial_fill_min_ratio: float = config.get("partial_fill_min_ratio", 0.3)
        self._rejection_chance: float = config.get("rejection_chance", 0.02)
        self._latency_ms: float = config.get("latency_ms", 50.0)
        self._sl_per_lot: float = config.get("sl_per_lot", 100000.0)

        self._account = AccountInfo(
            balance=self._initial_balance,
            equity=self._initial_balance,
            free_margin=self._initial_balance,
        )
        self._positions: dict[UUID, Position] = {}
        self._orders: dict[UUID, Order] = {}
        self._order_history: list[Order] = []
        self._position_history: list[Position] = []
        self._symbol_info: dict[str, SymbolInfo] = {}
        self._current_prices: dict[str, dict[str, float]] = {}
        self._order_books: dict[str, OrderBook] = {}
        self._running = False
        self._tick_task: asyncio.Task[None] | None = None
        # Optional hook(position, exit_price) fired whenever a position closes
        self.on_position_closed: Any = None
        # Default metadata stamped onto new positions (strategy_id, session, etc.)
        self.default_position_metadata: dict[str, Any] = {}

        # Equity curve for the trading dashboard (sampled at most once per second)
        self._last_equity_sample: float = 0.0
        self._equity_curve: list[dict[str, Any]] = [
            {
                "t": time.time(),
                "equity": round(self._account.equity, 2),
                "balance": round(self._account.balance, 2),
            }
        ]

        self._init_default_symbols()

    def _init_default_symbols(self) -> None:
        defaults = {
            "EURUSD": SymbolInfo("EURUSD", 0.00001, 5, 100000, 0.01, 100, 0.01, 0.00015),
            "GBPUSD": SymbolInfo("GBPUSD", 0.00001, 5, 100000, 0.01, 100, 0.01, 0.00020),
            "USDJPY": SymbolInfo("USDJPY", 0.001, 3, 100000, 0.01, 100, 0.01, 0.00015),
            "GBPJPY": SymbolInfo("GBPJPY", 0.001, 3, 100000, 0.01, 100, 0.01, 0.00030),
            "XAUUSD": SymbolInfo("XAUUSD", 0.01, 2, 100, 0.01, 100, 0.01, 0.30),
        }
        for sym, info in defaults.items():
            self._symbol_info[sym] = info
            self._current_prices[sym] = {"bid": 0.0, "ask": 0.0}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info("Paper broker started with balance %.2f", self._account.balance)

    async def stop(self) -> None:
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        logger.info("Paper broker stopped")

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(0.1)
                self._update_positions()
                self._check_sl_tp()
                self._update_equity()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Tick loop error: %s", exc)

    # ------------------------------------------------------------------
    # Price & spread
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, bid: float, ask: float) -> None:
        self._current_prices[symbol] = {"bid": bid, "ask": ask}
        self._generate_order_book(symbol, bid, ask)

    def get_spread(self, symbol: str) -> float:
        info = self._symbol_info.get(symbol)
        if info is None:
            return self._base_spread

        if self._spread_model == SpreadModel.FIXED:
            return info.spread

        if self._spread_model == SpreadModel.VARIABLE:
            noise = random.gauss(0, info.spread * 0.3)
            return max(info.spread * 0.5, info.spread + noise)

        if self._spread_model == SpreadModel.TIME_BASED:
            now = datetime.now(timezone.utc)
            hour = now.hour
            if 7 <= hour <= 16:
                return info.spread * 0.8
            elif 0 <= hour < 7:
                return info.spread * 1.5
            else:
                return info.spread * 1.2

        return info.spread

    def get_slippage(self, volume: float) -> float:
        if self._slippage_model == SlippageModel.FIXED:
            return self._fixed_slippage

        if self._slippage_model == SlippageModel.PROPORTIONAL:
            return min(volume * self._proportional_slippage, self._max_slippage)

        if self._slippage_model == SlippageModel.RANDOM:
            base = random.uniform(0, self._fixed_slippage * 2)
            volume_factor = min(volume / 10.0, 2.0)
            return min(base * volume_factor, self._max_slippage)

        return 0.0

    def _generate_order_book(self, symbol: str, bid: float, ask: float) -> None:
        spread = self.get_spread(symbol)
        levels = 5
        step = spread / levels
        bids = [OrderBookLevel(bid - i * step, random.uniform(0.5, 5.0)) for i in range(levels)]
        asks = [OrderBookLevel(ask + i * step, random.uniform(0.5, 5.0)) for i in range(levels)]
        self._order_books[symbol] = OrderBook(symbol=symbol, bids=bids, asks=asks)

    def get_order_book(self, symbol: str) -> OrderBook | None:
        return self._order_books.get(symbol)

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def send_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        volume: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Order:
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        info = self._symbol_info.get(symbol)
        if info is None:
            order = Order(
                order_id=uuid4(),
                symbol=symbol,
                side=side,
                order_type=order_type,
                volume=volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=OrderStatus.REJECTED,
                reject_reason=f"Unknown symbol: {symbol}",
            )
            self._orders[order.order_id] = order
            self._order_history.append(order)
            return order

        volume = round(max(info.min_lot, min(info.max_lot, volume)) / info.lot_step) * info.lot_step
        if volume < info.min_lot:
            order = Order(
                order_id=uuid4(),
                symbol=symbol,
                side=side,
                order_type=order_type,
                volume=volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=OrderStatus.REJECTED,
                reject_reason=f"Volume {volume} below minimum {info.min_lot}",
            )
            self._orders[order.order_id] = order
            self._order_history.append(order)
            return order

        if random.random() < self._rejection_chance:
            order = Order(
                order_id=uuid4(),
                symbol=symbol,
                side=side,
                order_type=order_type,
                volume=volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=OrderStatus.REJECTED,
                reject_reason="Simulated rejection: insufficient liquidity",
            )
            self._orders[order.order_id] = order
            self._order_history.append(order)
            logger.warning("Order rejected: %s", order.reject_reason)
            return order

        order = Order(
            order_id=uuid4(),
            symbol=symbol,
            side=side,
            order_type=order_type,
            volume=volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=dict(metadata or {}),
        )

        if order_type == OrderType.MARKET:
            await self._fill_market_order(order)
        else:
            self._orders[order.order_id] = order

        return order

    async def _fill_market_order(self, order: Order) -> None:
        prices = self._current_prices.get(order.symbol)
        if prices is None or (prices["bid"] == 0 and prices["ask"] == 0):
            order.status = OrderStatus.REJECTED
            order.reject_reason = "No price data available"
            self._orders[order.order_id] = order
            self._order_history.append(order)
            return

        spread = self.get_spread(order.symbol)
        slippage = self.get_slippage(order.volume)

        if order.side == OrderSide.BUY:
            base_price = prices["ask"] + spread / 2
        else:
            base_price = prices["bid"] - spread / 2

        if order.side == OrderSide.BUY:
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage

        fill_price = round(fill_price, self._symbol_info[order.symbol].digits)

        should_partial = random.random() < self._partial_fill_chance
        if should_partial and order.volume > info_min_lot(order.symbol, self._symbol_info):
            fill_ratio = random.uniform(self._partial_fill_min_ratio, 0.9)
            fill_volume = round(fill_ratio * order.volume, 2)
            fill_volume = max(info_min_lot(order.symbol, self._symbol_info), fill_volume)
        else:
            fill_volume = order.volume

        commission = fill_volume * self._commission_per_lot
        required_margin = fill_volume * self._sl_per_lot * fill_price * 0.01

        if required_margin > self._account.free_margin:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "Insufficient margin"
            self._orders[order.order_id] = order
            self._order_history.append(order)
            return

        order.filled_volume = fill_volume
        order.avg_fill_price = fill_price
        order.commission = commission
        order.slippage = abs(fill_price - base_price)
        order.filled_at = datetime.now(timezone.utc)
        order.partial_fills.append({
            "volume": fill_volume,
            "price": fill_price,
            "time": order.filled_at.isoformat(),
        })

        if fill_volume < order.volume:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.FILLED

        self._orders[order.order_id] = order
        self._order_history.append(order)

        position_side = PositionSide.LONG if order.side == OrderSide.BUY else PositionSide.SHORT
        position = Position(
            position_id=uuid4(),
            symbol=order.symbol,
            side=position_side,
            volume=fill_volume,
            entry_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            current_price=fill_price,
            commission=commission,
            metadata={
                **self.default_position_metadata,
                **(order.metadata or {}),
                "spread": abs(prices.get("ask", fill_price) - prices.get("bid", fill_price)),
            },
        )
        self._positions[position.position_id] = position
        self._account.margin += required_margin
        self._account.free_margin -= required_margin
        self._account.total_commission += commission

        logger.info(
            "Filled %s %s %.2f lots @ %.5f (slippage=%.6f, commission=%.2f)",
            order.side.value,
            order.symbol,
            fill_volume,
            fill_price,
            order.slippage,
            commission,
        )

    async def modify_order(
        self,
        order_id: UUID,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Order | None:
        order = self._orders.get(order_id)
        if order is None:
            return None
        if order.status not in (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED):
            return None

        if stop_loss is not None:
            order.stop_loss = stop_loss
        if take_profit is not None:
            order.take_profit = take_profit
        return order

    async def close_order(self, order_id: UUID) -> Order | None:
        order = self._orders.get(order_id)
        if order is None:
            return None
        order.status = OrderStatus.CANCELLED
        self._order_history.append(order)
        return order

    async def close_position(self, position_id: UUID) -> Position | None:
        position = self._positions.get(position_id)
        if position is None:
            return None

        prices = self._current_prices.get(position.symbol)
        if prices is None:
            return None

        if position.side == PositionSide.LONG:
            exit_price = prices["bid"]
        else:
            exit_price = prices["ask"]

        if position.side == PositionSide.LONG:
            pnl = (exit_price - position.entry_price) * position.volume * self._sl_per_lot / self._symbol_info[position.symbol].contract_size
        else:
            pnl = (position.entry_price - exit_price) * position.volume * self._sl_per_lot / self._symbol_info[position.symbol].contract_size

        pnl -= position.commission
        position.realized_pnl = pnl
        position.current_price = exit_price
        position.last_update = datetime.now(timezone.utc)

        released_margin = position.volume * self._sl_per_lot * position.entry_price * 0.01
        self._account.balance += pnl
        self._account.margin -= released_margin
        self._account.free_margin += released_margin

        del self._positions[position_id]
        self._position_history.append(position)

        # Fire close hook for journal / research wiring
        hook = getattr(self, "on_position_closed", None)
        if callable(hook):
            try:
                hook(position, exit_price)
            except Exception:
                logger.exception("on_position_closed hook failed")

        logger.info(
            "Closed %s %s %.2f lots: PnL=%.2f",
            position.side.value,
            position.symbol,
            position.volume,
            pnl,
        )
        return position

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _update_positions(self) -> None:
        for pos in self._positions.values():
            prices = self._current_prices.get(pos.symbol)
            if prices is None:
                continue

            if pos.side == PositionSide.LONG:
                pos.current_price = prices["bid"]
                pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.volume * self._sl_per_lot / self._symbol_info[pos.symbol].contract_size
            else:
                pos.current_price = prices["ask"]
                pos.unrealized_pnl = (pos.entry_price - pos.current_price) * pos.volume * self._sl_per_lot / self._symbol_info[pos.symbol].contract_size

            pos.unrealized_pnl -= pos.commission
            pos.last_update = datetime.now(timezone.utc)

    def _check_sl_tp(self) -> None:
        to_close: list[UUID] = []
        for pos_id, pos in self._positions.items():
            prices = self._current_prices.get(pos.symbol)
            if prices is None:
                continue

            if pos.stop_loss is not None:
                if pos.side == PositionSide.LONG and prices["bid"] <= pos.stop_loss:
                    to_close.append(pos_id)
                elif pos.side == PositionSide.SHORT and prices["ask"] >= pos.stop_loss:
                    to_close.append(pos_id)

            if pos.take_profit is not None:
                if pos.side == PositionSide.LONG and prices["bid"] >= pos.take_profit:
                    to_close.append(pos_id)
                elif pos.side == PositionSide.SHORT and prices["ask"] <= pos.take_profit:
                    to_close.append(pos_id)

        for pos_id in to_close:
            pos = self._positions.get(pos_id)
            if pos is None:
                continue
            if pos.stop_loss is not None:
                prices = self._current_prices[pos.symbol]
                if pos.side == PositionSide.LONG and prices["bid"] <= pos.stop_loss:
                    pos.current_price = pos.stop_loss
                elif pos.side == PositionSide.SHORT and prices["ask"] >= pos.stop_loss:
                    pos.current_price = pos.stop_loss

            if pos.take_profit is not None:
                prices = self._current_prices[pos.symbol]
                if pos.side == PositionSide.LONG and prices["bid"] >= pos.take_profit:
                    pos.current_price = pos.take_profit
                elif pos.side == PositionSide.SHORT and prices["ask"] <= pos.take_profit:
                    pos.current_price = pos.take_profit

            asyncio.create_task(self.close_position(pos_id))

    def _update_equity(self) -> None:
        floating = sum(p.unrealized_pnl for p in self._positions.values())
        self._account.floating_pnl = floating
        self._account.equity = self._account.balance + floating
        self._account.free_margin = self._account.equity - self._account.margin
        if self._account.margin > 0:
            self._account.margin_level = self._account.equity / self._account.margin * 100
        else:
            self._account.margin_level = 0.0
        # Sample equity curve at most once per second (also catches balance changes)
        now = time.monotonic()
        last = self._equity_curve[-1] if self._equity_curve else None
        if (
            now - self._last_equity_sample >= 1.0
            or last is None
            or last["balance"] != round(self._account.balance, 2)
        ):
            self._last_equity_sample = now
            self._equity_curve.append(
                {
                    "t": time.time(),
                    "equity": round(self._account.equity, 2),
                    "balance": round(self._account.balance, 2),
                }
            )
            if len(self._equity_curve) > 10000:
                del self._equity_curve[:5000]

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict[str, Any]:
        self._update_equity()
        return {
            "balance": round(self._account.balance, 2),
            "equity": round(self._account.equity, 2),
            "margin": round(self._account.margin, 2),
            "free_margin": round(self._account.free_margin, 2),
            "margin_level": round(self._account.margin_level, 2),
            "floating_pnl": round(self._account.floating_pnl, 2),
            "total_commission": round(self._account.total_commission, 2),
            "open_positions": len(self._positions),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        result = []
        for pos in self._positions.values():
            result.append({
                "position_id": str(pos.position_id),
                "symbol": pos.symbol,
                "side": pos.side.value,
                "volume": pos.volume,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "unrealized_pnl": round(pos.unrealized_pnl, 2),
                "commission": round(pos.commission, 2),
                "opened_at": pos.opened_at.isoformat(),
            })
        return result

    def get_order_history(self, limit: int = 50) -> list[dict[str, Any]]:
        result = []
        for order in self._order_history[-limit:]:
            result.append({
                "order_id": str(order.order_id),
                "symbol": order.symbol,
                "side": order.side.value,
                "order_type": order.order_type.value,
                "volume": order.volume,
                "filled_volume": order.filled_volume,
                "avg_fill_price": order.avg_fill_price,
                "status": order.status.value,
                "commission": round(order.commission, 2),
                "slippage": round(order.slippage, 6),
                "reject_reason": order.reject_reason,
                "created_at": order.created_at.isoformat(),
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            })
        return result

    def get_position_history(self, limit: int = 50) -> list[dict[str, Any]]:
        result = []
        for pos in self._position_history[-limit:]:
            result.append({
                "position_id": str(pos.position_id),
                "symbol": pos.symbol,
                "side": pos.side.value,
                "volume": pos.volume,
                "entry_price": pos.entry_price,
                "exit_price": pos.current_price,
                "realized_pnl": round(pos.realized_pnl, 2),
                "commission": round(pos.commission, 2),
                "opened_at": pos.opened_at.isoformat(),
                "closed_at": pos.last_update.isoformat(),
            })
        return result

    def get_equity_curve(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Time series of equity/balance snapshots for the trading dashboard."""
        return list(self._equity_curve[-limit:])

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        info = self._symbol_info.get(symbol)
        if info is None:
            return None
        return {
            "symbol": info.symbol,
            "point": info.point,
            "digits": info.digits,
            "contract_size": info.contract_size,
            "min_lot": info.min_lot,
            "max_lot": info.max_lot,
            "lot_step": info.lot_step,
            "spread": info.spread,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
        }


def info_min_lot(symbol: str, symbol_info: dict[str, SymbolInfo]) -> float:
    info = symbol_info.get(symbol)
    return info.min_lot if info else 0.01
