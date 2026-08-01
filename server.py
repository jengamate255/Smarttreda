import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

import logging
import MetaTrader5 as mt5
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from pydantic import BaseModel

import numpy as np

load_dotenv()

logger = logging.getLogger("mt5_mcp.server")


class MT5Config:
    def __init__(self):
        self.login = int(os.getenv("MT5_LOGIN", "0"))
        self.password = os.getenv("MT5_PASSWORD", "")
        self.server = os.getenv("MT5_SERVER", "")
        self.path = os.getenv("MT5_PATH", "")

    def is_configured(self) -> bool:
        return all([self.login, self.password, self.server])


class MT5Connection:
    _instance: Optional["MT5Connection"] = None
    _connected: bool = False
    _config: MT5Config | None = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def connect(self, config: MT5Config, attempts: int = 3, delay: float = 2.0) -> bool:
        if self._connected:
            return True
        with self._lock:
            if self._connected:
                return True
            self._config = config
            for attempt in range(1, attempts + 1):
                if self._try_connect():
                    return True
                if attempt < attempts:
                    time.sleep(delay * attempt)
            return False

    def _try_connect(self) -> bool:
        if not mt5.initialize(path=self._config.path):
            logger.warning("MT5 initialize failed: %s", mt5.last_error())
            return False

        if self._config.is_configured() and not mt5.login(
            self._config.login, password=self._config.password, server=self._config.server
        ):
            logger.warning("MT5 login failed: %s", mt5.last_error())
            mt5.shutdown()
            return False

        self._connected = True
        logger.info("Connected to MT5 successfully")
        return True

    def reconnect(self) -> bool:
        """Re-attach after a dropped connection (e.g. terminal restart).

        Never calls shutdown() on an already-dead session (that can hang);
        a fresh initialize() re-attaches to the terminal."""
        with self._lock:
            if self._connected:
                try:
                    if mt5.account_info() is not None:
                        return True  # session is actually healthy
                except (Exception, SystemExit) as exc:  # noqa: BLE001
                    logger.warning("MT5 liveness check failed: %s", exc)
            self._connected = False
            try:
                if mt5.initialize(path=self._config.path):
                    self._connected = True
                    logger.info("Reconnected to MT5 successfully")
                    return True
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                logger.warning("MT5 reconnect failed: %s", exc)
            return False

    def disconnect(self):
        with self._lock:
            if self._connected:
                mt5.shutdown()
                self._connected = False
                logger.info("Disconnected from MT5")

    def is_connected(self) -> bool:
        return self._connected

    def ensure_connected(self):
        if not self._connected and not self.reconnect():
            raise RuntimeError("MT5 not connected. Call connect() first.")


mt5_connection = MT5Connection()


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[None]:
    config = MT5Config()
    if config.is_configured():
        mt5_connection.connect(config)
    try:
        yield
    finally:
        mt5_connection.disconnect()


mcp = FastMCP("MT5 MCP Server", lifespan=lifespan)

# Register mt5_mcp tool modules on the canonical MCP instance.
from mt5_mcp.server import register_all
register_all(mcp)


class SymbolInfo(BaseModel):
    name: str
    bid: float
    ask: float
    spread: int
    point: float
    digits: int
    trade_mode: int


class AccountInfo(BaseModel):
    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str
    leverage: int


class PositionInfo(BaseModel):
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    profit: float
    swap: float
    comment: str
    magic: int


class OrderRequest(BaseModel):
    action: int = mt5.TRADE_ACTION_DEAL
    symbol: str
    volume: float
    type: int
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    deviation: int = 20
    magic: int = 234000
    comment: str = "MCP Order"
    type_time: int = mt5.ORDER_TIME_GTC
    type_filling: int = mt5.ORDER_FILLING_IOC


class OrderResult(BaseModel):
    retcode: int
    deal: int
    order: int
    volume: float
    price: float
    bid: float
    ask: float
    comment: str
    request_id: int
    retcode_external: int


@mcp.tool()
async def get_account_info(ctx: Context) -> AccountInfo:
    """Get current account information"""
    mt5_connection.ensure_connected()
    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"Failed to get account info: {mt5.last_error()}")

    return AccountInfo(
        login=info.login,
        balance=info.balance,
        equity=info.equity,
        margin=info.margin,
        free_margin=info.margin_free,
        margin_level=info.margin_level,
        currency=info.currency,
        leverage=info.leverage,
    )


@mcp.tool()
async def get_symbol_info(ctx: Context, symbol: str) -> SymbolInfo:
    """Get symbol information"""
    mt5_connection.ensure_connected()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol {symbol} not found: {mt5.last_error()}")

    return SymbolInfo(
        name=info.name,
        bid=info.bid,
        ask=info.ask,
        spread=info.spread,
        point=info.point,
        digits=info.digits,
        trade_mode=info.trade_mode,
    )


@mcp.tool()
async def get_positions(ctx: Context, symbol: str | None = None) -> list[PositionInfo]:
    """Get open positions, optionally filtered by symbol"""
    mt5_connection.ensure_connected()
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []

    return [
        PositionInfo(
            ticket=pos.ticket,
            symbol=pos.symbol,
            type=pos.type,
            volume=pos.volume,
            price_open=pos.price_open,
            sl=pos.sl,
            tp=pos.tp,
            price_current=pos.price_current,
            profit=pos.profit,
            swap=pos.swap,
            comment=pos.comment,
            magic=pos.magic,
        )
        for pos in positions
    ]


@mcp.tool()
async def get_orders(ctx: Context, symbol: str | None = None) -> list[dict]:
    """Get pending orders, optionally filtered by symbol"""
    mt5_connection.ensure_connected()
    orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    if orders is None:
        return []

    return [
        {
            "ticket": order.ticket,
            "symbol": order.symbol,
            "type": order.type,
            "volume": order.volume_initial,
            "price_open": order.price_open,
            "sl": order.sl,
            "tp": order.tp,
            "comment": order.comment,
            "magic": order.magic,
            "time_setup": order.time_setup,
        }
        for order in orders
    ]


@mcp.tool()
async def place_order(ctx: Context, request: OrderRequest) -> OrderResult:
    """Place a new order (market or pending)"""
    mt5_connection.ensure_connected()

    req = {
        "action": request.action,
        "symbol": request.symbol,
        "volume": request.volume,
        "type": request.type,
        "price": request.price,
        "sl": request.sl,
        "tp": request.tp,
        "deviation": request.deviation,
        "magic": request.magic,
        "comment": request.comment,
        "type_time": request.type_time,
        "type_filling": request.type_filling,
    }

    result = mt5.order_send(req)
    if result is None:
        raise RuntimeError(f"Order send failed: {mt5.last_error()}")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Order failed: {result.retcode} - {result.comment}")

    return OrderResult(
        retcode=result.retcode,
        deal=result.deal,
        order=result.order,
        volume=result.volume,
        price=result.price,
        bid=result.bid,
        ask=result.ask,
        comment=result.comment,
        request_id=result.request_id,
        retcode_external=result.retcode_external,
    )


@mcp.tool()
async def close_position(ctx: Context, ticket: int, volume: float | None = None) -> OrderResult:
    """Close an open position by ticket"""
    mt5_connection.ensure_connected()

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise RuntimeError(f"Position {ticket} not found")

    pos = positions[0]
    close_volume = volume if volume else pos.volume

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": close_volume,
        "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "position": ticket,
        "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(pos.symbol).ask,
        "deviation": 20,
        "magic": pos.magic,
        "comment": "Close via MCP",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"Close failed: {mt5.last_error()}")

    return OrderResult(
        retcode=result.retcode,
        deal=result.deal,
        order=result.order,
        volume=result.volume,
        price=result.price,
        bid=result.bid,
        ask=result.ask,
        comment=result.comment,
        request_id=result.request_id,
        retcode_external=result.retcode_external,
    )


@mcp.tool()
async def cancel_order(ctx: Context, ticket: int) -> bool:
    """Cancel a pending order by ticket"""
    mt5_connection.ensure_connected()

    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
        "comment": "Cancel via MCP",
    }

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f"Cancel failed: {mt5.last_error()}")

    return result.retcode == mt5.TRADE_RETCODE_DONE


@mcp.tool()
async def get_history_deals(ctx: Context, from_date: str | None = None, to_date: str | None = None, symbol: str | None = None) -> list[dict]:
    """Get deal history"""
    mt5_connection.ensure_connected()

    import datetime
    from datetime import timezone
    if from_date:
        from_dt = datetime.datetime.fromisoformat(from_date)
    else:
        from_dt = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=30)

    if to_date:
        to_dt = datetime.datetime.fromisoformat(to_date)
    else:
        to_dt = datetime.datetime.now(timezone.utc)

    deals = mt5.history_deals_get(from_dt, to_dt, group=symbol)
    if deals is None:
        return []

    return [
        {
            "ticket": deal.ticket,
            "order": deal.order,
            "time": deal.time,
            "time_msc": deal.time_msc,
            "symbol": deal.symbol,
            "type": deal.type,
            "entry": deal.entry,
            "volume": deal.volume,
            "price": deal.price,
            "profit": deal.profit,
            "swap": deal.swap,
            "fee": deal.fee,
            "comment": deal.comment,
        }
        for deal in deals
    ]


@mcp.tool()
async def get_rates(ctx: Context, symbol: str, timeframe: int, count: int = 100) -> list[dict]:
    """Get historical rates for a symbol"""
    mt5_connection.ensure_connected()

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None:
        raise RuntimeError(f"Failed to get rates: {mt5.last_error()}")

    return [
        {
            "time": rate[0],
            "open": rate[1],
            "high": rate[2],
            "low": rate[3],
            "close": rate[4],
            "tick_volume": rate[5],
            "spread": rate[6],
            "real_volume": rate[7],
        }
        for rate in rates
    ]


@mcp.tool()
async def get_tick_info(ctx: Context, symbol: str) -> dict:
    """Get current tick info for a symbol"""
    mt5_connection.ensure_connected()

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Failed to get tick: {mt5.last_error()}")

    return {
        "time": tick.time,
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "volume": tick.volume,
        "flags": tick.flags,
    }


# Technical Analysis — delegated to technical_analysis.py

from technical_analysis import (
    sma as _sma,
    ema as _ema,
    rsi as _rsi,
    macd as _macd,
    bollinger_bands as _bollinger,
    atr as _atr,
    stochastic as _stochastic,
    adx as _adx,
    analyze_trend as _trend,
    support_resistance as _sr,
    pivot_points as _pivots,
)


def _analyze_tf(rates: list[dict]) -> dict:
    close = np.array([r["close"] for r in rates])
    high = np.array([r["high"] for r in rates])
    low = np.array([r["low"] for r in rates])
    rsi_vals = _rsi(close)
    macd_line, signal_line, histogram = _macd(close)
    bb_upper, bb_middle, bb_lower = _bollinger(close)
    atr_vals = _atr(high, low, close)
    stoch_k, stoch_d = _stochastic(high, low, close)
    adx_vals = _adx(high, low, close)
    trend = _trend(close)
    supports, resistances = _sr(close, high, low)
    pivots = _pivots(high[-2], low[-2], close[-2])
    return {
        "current_price": round(float(close[-1]), 5),
        "trend": trend,
        "rsi": round(float(rsi_vals[-1]), 2),
        "macd": round(float(macd_line[-1]), 5),
        "macd_signal": round(float(signal_line[-1]), 5),
        "macd_histogram": round(float(histogram[-1]), 5),
        "bb_upper": round(float(bb_upper[-1]), 5),
        "bb_middle": round(float(bb_middle[-1]), 5),
        "bb_lower": round(float(bb_lower[-1]), 5),
        "atr": round(float(atr_vals[-1]), 5),
        "adx": round(float(adx_vals[-1]), 2),
        "stoch_k": round(float(stoch_k[-1]), 2),
        "stoch_d": round(float(stoch_d[-1]), 2),
        "supports": supports,
        "resistances": resistances,
        "pivots": {k: round(v, 5) for k, v in pivots.items()},
    }


class TechnicalAnalysis(BaseModel):
    symbol: str
    timeframes: dict
    tick: dict


@mcp.tool()
async def get_technical_analysis(ctx: Context, symbol: str = "EURUSD", count: int = 500) -> TechnicalAnalysis:
    """Get multi-timeframe technical analysis for a symbol"""
    mt5_connection.ensure_connected()
    
    timeframes = {
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    
    results = {}
    for tf_name, tf_value in timeframes.items():
        rates = mt5.copy_rates_from_pos(symbol, tf_value, 0, count)
        if rates is not None and len(rates) > 50:
            rates_list = [
                {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "tick_volume": r[5]}
                for r in rates
            ]
            results[tf_name] = _analyze_tf(rates_list)
    
    tick = mt5.symbol_info_tick(symbol)
    tick_data = {"bid": tick.bid, "ask": tick.ask, "spread": tick.spread} if tick else {}
    
    return TechnicalAnalysis(symbol=symbol, timeframes=results, tick=tick_data)


if __name__ == "__main__":
    mcp.run()