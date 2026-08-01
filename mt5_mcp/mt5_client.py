"""MetaTrader 5 connection manager.

Wraps the synchronous ``MetaTrader5`` package with:

* automatic reconnect with retries and backoff
* connection health checks
* thread-safe access (RLock serialisation)
* graceful shutdown
* lazy import so the module loads on machines without the package

The underlying MT5 Python API is synchronous; tools run it inside FastMCP's
thread pool, and every call is serialised with an RLock.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from mt5_mcp.common import (
    MT5_TIME_GTC_ALIAS,
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    RETCODE_DONE,
    RETCODE_PLACED,
    TIMEFRAME_MT5,
    Timeframe,
)
from mt5_mcp.config import MT5Config

logger = logging.getLogger("mt5_mcp.mt5")

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2
DEAL_ENTRY_OUT = 1

SYMBOL_TRADE_MODE_DISABLED = 1

#: Fallback aggregation sources for timeframes the terminal has no cached bars for
#: (e.g. fresh SIM terminals only hold M1-M30). (source_timeframe, bars_per_target_bar)
AGG_SOURCES: dict[str, tuple[str, int]] = {
    "H1": ("M5", 12),
    "H2": ("M5", 24),
    "H3": ("M5", 36),
    "H4": ("M5", 48),
    "H6": ("M30", 12),
    "H8": ("M30", 16),
    "H12": ("M30", 24),
    "D1": ("M30", 48),
    "W1": ("M30", 336),
    "MN1": ("M30", 1440),
}

MAX_HISTORY_BARS = 80_000


class MT5Error(RuntimeError):
    """Raised when the MT5 terminal is unreachable or a request is rejected."""


class MT5Client:
    """Thread-safe wrapper around the MetaTrader 5 Python API."""

    def __init__(self, config: MT5Config | None = None) -> None:
        self._config = config or MT5Config()
        self._lock = threading.RLock()
        self._mt5: Any = None
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._connect_count = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _load_module(self) -> None:
        if self._mt5 is not None:
            return
        try:
            import MetaTrader5 as mt5

            self._mt5 = mt5
        except ImportError as exc:
            raise MT5Error(
                "MetaTrader5 package is not installed. "
                "Install it with: pip install MetaTrader5 (Windows + running terminal required)"
            ) from exc

    def connect(self, force: bool = False) -> bool:
        with self._lock:
            if self._connected and not force:
                return True
            self._load_module()
            mt5 = self._mt5
            attempts = max(1, self._config.reconnect_attempts)
            for attempt in range(1, attempts + 1):
                try:
                    kwargs: dict[str, Any] = {
                        "timeout": self._config.connect_timeout_seconds
                    }
                    if self._config.terminal_path:
                        kwargs["path"] = self._config.terminal_path
                    if self._config.is_configured:
                        kwargs.update(
                            login=self._config.login,
                            password=self._config.password,
                            server=self._config.server,
                        )
                    if not mt5.initialize(**kwargs):
                        self._last_error = f"{mt5.last_error()}"
                        logger.warning(
                            "MT5 initialize failed (attempt %d/%d): %s",
                            attempt,
                            attempts,
                            self._last_error,
                        )
                    else:
                        account = mt5.account_info()
                        if account is None:
                            self._last_error = "account_info() returned None"
                            logger.warning(
                                "MT5 initialize ok but account_info failed (attempt %d/%d)",
                                attempt,
                                attempts,
                            )
                        else:
                            self._connected = True
                            self._connected_at = time.time()
                            self._connect_count += 1
                            logger.info(
                                "Connected to MT5 (login=%s server=%s attempts=%d)",
                                getattr(account, "login", "?"),
                                getattr(account, "server", "?"),
                                attempt,
                            )
                            return True
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning(
                        "MT5 connect raised (attempt %d/%d): %s", attempt, attempts, exc
                    )
                if attempt < attempts:
                    time.sleep(self._config.reconnect_delay_seconds)
            self._connected = False
            return False

    def ensure_connected(self) -> None:
        with self._lock:
            if self._connected and self.health()["ok"]:
                return
            if not self.connect():
                raise MT5Error(f"MT5 not connected: {self._last_error}")

    def health(self) -> dict[str, Any]:
        with self._lock:
            if not self._connected or self._mt5 is None:
                return {
                    "ok": False,
                    "connected": False,
                    "last_error": self._last_error,
                    "uptime_s": 0.0,
                }
            try:
                account = self._mt5.account_info()
                ok = account is not None
            except Exception as exc:
                ok = False
                self._last_error = str(exc)
            if not ok:
                self._connected = False
                logger.warning(
                    "Health check failed, marking disconnected: %s", self._last_error
                )
            return {
                "ok": ok,
                "connected": ok,
                "last_error": "" if ok else self._last_error,
                "uptime_s": time.time() - self._connected_at if ok else 0.0,
                "connect_count": self._connect_count,
            }

    def shutdown(self) -> None:
        with self._lock:
            if self._mt5 is not None and self._connected:
                try:
                    self._mt5.shutdown()
                except Exception as exc:
                    logger.warning("MT5 shutdown raised: %s", exc)
            self._connected = False
            logger.info("MT5 connection closed")

    def reconnect(self) -> bool:
        with self._lock:
            self._connected = False
            return self.connect(force=True)

    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------------
    # primitives
    # ------------------------------------------------------------------
    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.ensure_connected()
        with self._lock:
            fn = getattr(self._mt5, method, None)
            if fn is None:
                raise MT5Error(f"MetaTrader5 has no function '{method}'")
            if (
                method in ("history_orders_get", "history_deals_get")
                and "date_from" in kwargs
            ):
                # The MetaTrader5 module rejects date_from/date_to passed as
                # keyword arguments (RES_E_INVALID_PARAMS); they must be
                # positional: (date_from, date_to[, group]).
                date_from = kwargs.pop("date_from")
                date_to = kwargs.pop("date_to", None)
                group = kwargs.pop("group", None)
                if group is not None:
                    return fn(date_from, date_to, group)
                if date_to is not None:
                    return fn(date_from, date_to)
                return fn(date_from)
            return fn(*args, **kwargs)

    def _tuple_to_dict(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return {}

    # ------------------------------------------------------------------
    # account / terminal
    # ------------------------------------------------------------------
    def account_info(self) -> dict[str, Any]:
        return self._tuple_to_dict(self._call("account_info"))

    def terminal_info(self) -> dict[str, Any]:
        return self._tuple_to_dict(self._call("terminal_info"))

    # ------------------------------------------------------------------
    # market data
    # ------------------------------------------------------------------
    def symbols(self, group: str = "") -> list[str]:
        result = (
            self._call("symbols_get", group=group)
            if group
            else self._call("symbols_get")
        )
        return sorted(getattr(sym, "name", str(sym)) for sym in (result or []))

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        if not self.symbol_exists(symbol):
            raise MT5Error(f"Symbol '{symbol}' does not exist on this account")
        info = self._tuple_to_dict(self._call("symbol_info", symbol))
        if not info:
            raise MT5Error(f"Symbol '{symbol}' not found")
        return info

    def symbol_exists(self, symbol: str) -> bool:
        info = self._call("symbol_info", symbol)
        return info is not None

    def tick(self, symbol: str) -> dict[str, Any]:
        tick = self._call("symbol_info_tick", symbol)
        if tick is None:
            raise MT5Error(f"No tick data for '{symbol}'")
        return self._tuple_to_dict(tick)

    def rates(
        self, symbol: str, timeframe: Timeframe | str, count: int, from_pos: int = 0
    ) -> pd.DataFrame:
        tf_name = str(timeframe).upper()
        tf = TIMEFRAME_MT5.get(tf_name)
        if tf is None:
            raise ValueError(f"Unknown timeframe '{timeframe}'")
        if count < 1 or count > 1_000_000:
            raise ValueError("count must be between 1 and 1,000,000")
        data = self._call("copy_rates_from_pos", symbol, tf, from_pos, count)
        if data is None or len(data) == 0:
            data = self._aggregate_rates(symbol, tf_name, count, from_pos)
            if data is None or len(data) == 0:
                raise MT5Error(f"No rate data for '{symbol}' ({timeframe})")
        frame = pd.DataFrame(data)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert(
            None
        )
        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col].astype(float)
        return (
            frame[
                [
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "tick_volume",
                    "spread",
                    "real_volume",
                ]
            ]
            .tail(count)
            .reset_index(drop=True)
        )

    def _aggregate_rates(
        self, symbol: str, tf_name: str, count: int, from_pos: int
    ) -> list | None:
        source = AGG_SOURCES.get(tf_name)
        if source is None:
            return None
        src_tf_name, bars_per_bar = source
        src_tf = TIMEFRAME_MT5[src_tf_name]
        needed = min(count * bars_per_bar + bars_per_bar, MAX_HISTORY_BARS)
        data = self._call("copy_rates_from_pos", symbol, src_tf, from_pos, needed)
        if data is None or len(data) == 0:
            return None
        frame = pd.DataFrame(data)
        raw = frame["time"].astype("int64")
        tf_seconds = TIMEFRAME_MT5[tf_name] * 60
        if tf_name == "D1":
            frame["bucket"] = raw // 86400 * 86400
        elif tf_name == "W1":
            day = raw // 86400
            frame["bucket"] = (day - (day + 3) % 7) * 86400
        elif tf_name == "MN1":
            month_start = pd.to_datetime(raw, unit="s").dt.to_period("M").dt.start_time
            unit = str(month_start.dtype)[11:-1]
            month_start = (
                month_start.astype("int64")
                // {
                    "s": 1,
                    "ms": 10**3,
                    "us": 10**6,
                    "ns": 10**9,
                }[unit]
            )
            frame["bucket"] = month_start
        else:
            frame["bucket"] = raw // tf_seconds * tf_seconds
        agg = (
            frame.groupby("bucket", as_index=False)
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                tick_volume=("tick_volume", "sum"),
                spread=("spread", "mean"),
                real_volume=("real_volume", "sum"),
            )
            .rename(columns={"bucket": "time"})
        )
        return agg.tail(count).to_dict("records")

    # ------------------------------------------------------------------
    # positions / orders / history
    # ------------------------------------------------------------------
    def positions(self, symbol: str = "") -> list[dict[str, Any]]:
        data = (
            self._call("positions_get", symbol=symbol)
            if symbol
            else self._call("positions_get")
        )
        return [self._tuple_to_dict(p) for p in (data or [])]

    def pending_orders(self, symbol: str = "") -> list[dict[str, Any]]:
        data = (
            self._call("orders_get", symbol=symbol)
            if symbol
            else self._call("orders_get")
        )
        return [self._tuple_to_dict(o) for o in (data or [])]

    def closed_orders(self, days: int = 30, symbol: str = "") -> list[dict[str, Any]]:
        """Closed orders with realized P&L attached.

        MT5 orders carry no profit field - realized P&L lives on the closing
        deals (entry=OUT). So each closed order is enriched with the net
        (profit + swap + fee) of its closing deal(s)."""
        date_from = int(time.time()) - days * 86400
        date_to = int(time.time())
        kwargs: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
        if symbol:
            kwargs["group"] = symbol
        orders = [
            self._tuple_to_dict(o)
            for o in (self._call("history_orders_get", **kwargs) or [])
        ]
        closed = [o for o in orders if o.get("time_done", 0) > 0]
        if not closed:
            return []

        deals = [
            self._tuple_to_dict(d)
            for d in (
                self._call("history_deals_get", **kwargs) or []
            )
        ]
        pnl_by_order: dict[int, float] = {}
        for d in deals:
            if d.get("entry") != DEAL_ENTRY_OUT:
                continue
            if d.get("type") not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
                continue
            order = int(d.get("order", 0) or 0)
            pnl_by_order[order] = pnl_by_order.get(order, 0.0) + float(
                (d.get("profit") or 0.0)
                + (d.get("swap") or 0.0)
                + (d.get("fee") or 0.0)
            )
        for o in closed:
            o["profit"] = round(pnl_by_order.get(int(o.get("ticket", 0) or 0), 0.0), 2)
        return closed

    def deals(self, days: int = 30, symbol: str = "") -> list[dict[str, Any]]:
        date_from = int(time.time()) - days * 86400
        kwargs: dict[str, Any] = {"date_from": date_from, "date_to": int(time.time())}
        if symbol:
            kwargs["group"] = symbol
        data = self._call("history_deals_get", **kwargs)
        deals = [self._tuple_to_dict(d) for d in (data or [])]
        return [
            d
            for d in deals
            if d.get("type") in (DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_TYPE_BALANCE)
        ]

    def deals_since(self, epoch_seconds: int, symbol: str = "") -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "date_from": epoch_seconds,
            "date_to": int(time.time()),
        }
        if symbol:
            kwargs["group"] = symbol
        data = self._call("history_deals_get", **kwargs)
        deals = [self._tuple_to_dict(d) for d in (data or [])]
        return [
            d
            for d in deals
            if d.get("type") in (DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_TYPE_BALANCE)
        ]

    # ------------------------------------------------------------------
    # order plumbing
    # ------------------------------------------------------------------
    def _filling(self, symbol: str) -> int:
        info = self._tuple_to_dict(self._call("symbol_info", symbol))
        mode = int(info.get("filling_mode", 0) or 0)
        if mode & ORDER_FILLING_RETURN:
            return ORDER_FILLING_RETURN
        if mode & ORDER_FILLING_IOC:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_FOK

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        if "filling" not in request and "symbol" in request:
            request["filling"] = self._filling(str(request["symbol"]))
        if "type_time" not in request:
            request["type_time"] = MT5_TIME_GTC_ALIAS
        result = self._tuple_to_dict(self._call("order_send", request))
        retcode = result.get("retcode")
        if retcode not in (RETCODE_DONE, RETCODE_PLACED):
            raise MT5Error(
                f"Order rejected: retcode={retcode} ({result.get('comment', '')}) "
                f"request={request}"
            )
        return result

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float:
        value = self._call("order_calc_margin", action, symbol, volume, price)
        return float(value) if value is not None else 0.0

    def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float:
        value = self._call(
            "order_calc_profit", action, symbol, volume, price_open, price_close
        )
        return float(value) if value is not None else 0.0

    # ------------------------------------------------------------------
    # session / market state
    # ------------------------------------------------------------------
    def market_open(self, symbol: str) -> tuple[bool, str]:
        info = self._tuple_to_dict(self._call("symbol_info", symbol))
        trade_mode = int(info.get("trade_mode", 0) or 0)
        if trade_mode == SYMBOL_TRADE_MODE_DISABLED:
            return False, "trading disabled for this symbol"
        weekday = time.gmtime().tm_wday
        if weekday >= 5:
            return False, "market closed (weekend)"
        tick = self._tuple_to_dict(self._call("symbol_info_tick", symbol))
        tick_time = int(tick.get("time", 0) or 0)
        if tick_time:
            age = time.time() - tick_time
            if age > 900:
                return False, f"market closed (no fresh quotes for {int(age)}s)"
        return True, "market open"

    def server_time(self) -> datetime:
        info = self.terminal_info()
        value = info.get("time", 0) or 0
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
