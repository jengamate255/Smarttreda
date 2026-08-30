"""Standalone MT5 connector for the forex RL trading system.

Wraps the MetaTrader5 Python API with:
- Automatic reconnect with retries
- Thread-safe access (RLock)
- Lazy import so the module loads on machines without the package
- Standardised dict returns for all queries
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── MT5 Constants ────────────────────────────────────────────────────────

TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

RETCODE_DONE = 10009
RETCODE_PLACED = 10008

SYMBOL_TRADE_MODE_DISABLED = 1

TIMEFRAME_MT5: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}

AGG_SOURCES: dict[str, tuple[str, int]] = {
    "H1": ("M5", 12),
    "H4": ("M5", 48),
    "D1": ("M30", 48),
    "W1": ("M30", 336),
    "MN1": ("M30", 1440),
}

MAX_HISTORY_BARS = 80_000


class MT5Error(RuntimeError):
    """Raised when the MT5 terminal is unreachable or a request is rejected."""


class MT5Connector:
    """Thread-safe wrapper around the MetaTrader 5 Python API.

    Parameters
    ----------
    config : dict
        Configuration with keys:
        - ``terminal_path``: optional path to MT5 terminal executable
        - ``login``: account login number
        - ``password``: account password
        - ``server``: broker server name
        - ``timeout``: connection timeout in seconds (default 10)
        - ``reconnect_attempts``: max reconnect attempts (default 5)
        - ``reconnect_delay``: seconds between reconnect attempts (default 2)
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._mt5: Any = None
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._connect_count = 0
        self._health_failures = 0

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _load_module(self) -> None:
        if self._mt5 is not None:
            return
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError as exc:
            raise MT5Error(
                "MetaTrader5 package is not installed. "
                "Install with: pip install MetaTrader5 (Windows + running terminal required)"
            ) from exc

    def connect(self) -> bool:
        """Connect to the MT5 terminal with retry logic."""
        with self._lock:
            if self._connected:
                return True
            self._load_module()
            mt5 = self._mt5

            attempts = max(1, self._config.get("reconnect_attempts", 5))
            timeout = self._config.get("timeout", 10)

            strategies: list[dict[str, Any]] = []
            terminal_path = self._config.get("terminal_path")
            if terminal_path:
                strategies.append({"path": terminal_path})
            strategies.append({})

            for strategy in strategies:
                for attempt in range(1, attempts + 1):
                    try:
                        kwargs: dict[str, Any] = {"timeout": timeout}
                        kwargs.update(strategy)
                        login = self._config.get("login")
                        password = self._config.get("password")
                        server = self._config.get("server")
                        if login and password and server:
                            kwargs["login"] = int(login)
                            kwargs["password"] = str(password)
                            kwargs["server"] = str(server)

                        if not mt5.initialize(**kwargs):
                            self._last_error = str(mt5.last_error())
                            logger.warning(
                                "MT5 init failed (attempt %d/%d): %s",
                                attempt, attempts, self._last_error,
                            )
                        else:
                            account = mt5.account_info()
                            if account is None:
                                self._last_error = "account_info() returned None"
                                logger.warning(
                                    "MT5 init ok but account_info failed (attempt %d/%d)",
                                    attempt, attempts,
                                )
                            else:
                                self._connected = True
                                self._connected_at = time.time()
                                self._connect_count += 1
                                logger.info(
                                    "Connected to MT5 (login=%s server=%s)",
                                    getattr(account, "login", "?"),
                                    getattr(account, "server", "?"),
                                )
                                return True
                    except Exception as exc:
                        self._last_error = str(exc)
                        logger.warning(
                            "MT5 connect error (attempt %d/%d): %s",
                            attempt, attempts, exc,
                        )
                    if attempt < attempts:
                        delay = self._config.get("reconnect_delay", 2)
                        time.sleep(delay)

            self._connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect from the MT5 terminal."""
        with self._lock:
            if self._mt5 is not None and self._connected:
                try:
                    self._mt5.shutdown()
                except Exception as exc:
                    logger.warning("MT5 shutdown error: %s", exc)
            self._connected = False
            logger.info("MT5 connection closed")

    def _ensure_connected(self) -> None:
        if not self._connected or self._mt5 is None:
            if not self.connect():
                raise MT5Error(f"MT5 not connected: {self._last_error}")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._ensure_connected()
        with self._lock:
            fn = getattr(self._mt5, method, None)
            if fn is None:
                raise MT5Error(f"MetaTrader5 has no function '{method}'")
            if (
                method in ("history_orders_get", "history_deals_get")
                and "date_from" in kwargs
            ):
                date_from = kwargs.pop("date_from")
                date_to = kwargs.pop("date_to", None)
                group = kwargs.pop("group", None)
                if group is not None:
                    return fn(date_from, date_to, group)
                if date_to is not None:
                    return fn(date_from, date_to)
                return fn(date_from)
            return fn(*args, **kwargs) if kwargs else fn(*args)

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return {}

    # ── Account ───────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Get account info: balance, equity, margin, free_margin, profit."""
        info = self._to_dict(self._call("account_info"))
        return {
            "balance": float(info.get("balance", 0.0)),
            "equity": float(info.get("equity", 0.0)),
            "margin": float(info.get("margin", 0.0)),
            "free_margin": float(info.get("margin_free", 0.0)),
            "profit": float(info.get("profit", 0.0)),
            "leverage": int(info.get("leverage", 1)),
            "currency": str(info.get("currency", "USD")),
            "login": int(info.get("login", 0)),
            "server": str(info.get("server", "")),
        }

    # ── Positions ─────────────────────────────────────────────────────

    def get_positions(self, symbol: Optional[str] = None) -> list[dict]:
        """Get open positions, optionally filtered by symbol."""
        data = (
            self._call("positions_get", symbol=symbol)
            if symbol
            else self._call("positions_get")
        )
        return [self._to_dict(p) for p in (data or [])]

    # ── Symbol Info ───────────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> dict:
        """Get symbol properties: point, tick_value, lot_min, lot_max, spread."""
        info = self._to_dict(self._call("symbol_info", symbol))
        if not info:
            raise MT5Error(f"Symbol '{symbol}' not found")
        return {
            "point": float(info.get("point", 0.0)),
            "digits": int(info.get("digits", 0)),
            "tick_value": float(info.get("trade_tick_value", 0.0)),
            "tick_size": float(info.get("trade_tick_size", 0.0)),
            "lot_min": float(info.get("volume_min", 0.01)),
            "lot_max": float(info.get("volume_max", 100.0)),
            "lot_step": float(info.get("volume_step", 0.01)),
            "spread": int(info.get("spread", 0)),
            "trade_mode": int(info.get("trade_mode", 0)),
            "filling_mode": int(info.get("filling_mode", 0)),
        }

    # ── Market Data ───────────────────────────────────────────────────

    def get_rates(self, symbol: str, timeframe: str, count: int = 1000) -> pd.DataFrame:
        """Get OHLCV data as a DataFrame."""
        tf_name = timeframe.upper()
        tf = TIMEFRAME_MT5.get(tf_name)
        if tf is None:
            raise ValueError(f"Unknown timeframe '{timeframe}'")

        try:
            self._call("symbol_select", symbol, True)
        except Exception:
            pass

        data = self._fetch_rates_newest(symbol, tf, tf_name, count)
        if data is None or len(data) == 0:
            data = self._aggregate_rates(symbol, tf_name, count)
            if data is None or len(data) == 0:
                raise MT5Error(f"No rate data for '{symbol}' ({timeframe})")

        frame = pd.DataFrame(data)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert(None)
        for col in ("open", "high", "low", "close"):
            frame[col] = frame[col].astype(float)
        return (
            frame[["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]
            .tail(count)
            .reset_index(drop=True)
        )

    def _fetch_rates_newest(
        self, symbol: str, tf: int, tf_name: str, count: int
    ) -> list | None:
        now = datetime.now(timezone.utc)
        span = timedelta(seconds=count * tf * 60)
        try:
            data = self._call("copy_rates_range", symbol, tf, now - span, now)
            if data is not None and len(data) > 0:
                return data
        except Exception:
            pass
        try:
            return self._call("copy_rates_from_pos", symbol, tf, 0, count)
        except Exception:
            return None

    def _aggregate_rates(
        self, symbol: str, tf_name: str, count: int
    ) -> list | None:
        source = AGG_SOURCES.get(tf_name)
        if source is None:
            return None
        src_tf_name, bars_per_bar = source
        src_tf = TIMEFRAME_MT5[src_tf_name]
        needed = min(count * bars_per_bar + bars_per_bar, MAX_HISTORY_BARS)
        data = self._fetch_rates_newest(symbol, src_tf, src_tf_name, needed)
        if data is None or len(data) == 0:
            return None

        frame = pd.DataFrame(data)
        raw = frame["time"].astype("int64")
        tf_seconds = TIMEFRAME_MT5[tf_name] * 60

        if tf_name in ("D1", "W1", "MN1"):
            if tf_name == "D1":
                frame["bucket"] = raw // 86400 * 86400
            elif tf_name == "W1":
                day = raw // 86400
                frame["bucket"] = (day - (day + 3) % 7) * 86400
            else:
                month_start = pd.to_datetime(raw, unit="s").dt.to_period("M").dt.start_time
                unit = str(month_start.dtype)[11:-1]
                month_start = (
                    month_start.astype("int64")
                    // {"s": 1, "ms": 10**3, "us": 10**6, "ns": 10**9}.get(unit, 1)
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

    # ── Orders ────────────────────────────────────────────────────────

    def _filling(self, symbol: str) -> int:
        info = self._to_dict(self._call("symbol_info", symbol))
        mode = int(info.get("filling_mode", 0) or 0)
        if mode & 4:
            return ORDER_FILLING_RETURN
        if mode & 2:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_FOK

    def send_order(
        self,
        symbol: str,
        side: str,
        lots: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "",
    ) -> dict:
        """Send market order. Returns order result dict."""
        if side.upper() == "BUY":
            order_type = ORDER_TYPE_BUY
        elif side.upper() == "SELL":
            order_type = ORDER_TYPE_SELL
        else:
            raise ValueError(f"Invalid side '{side}': must be BUY or SELL")

        tick = self._to_dict(self._call("symbol_info_tick", symbol))
        if not tick:
            raise MT5Error(f"No tick data for '{symbol}'")

        price = float(tick["ask"]) if order_type == ORDER_TYPE_BUY else float(tick["bid"])

        request: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lots),
            "type": order_type,
            "price": price,
            "deviation": int(self._config.get("slippage_points", 10)),
            "magic": 20250101,
            "comment": comment,
            "type_time": ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }

        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)

        result = self._to_dict(self._call("order_send", request))
        retcode = result.get("retcode")
        if retcode not in (RETCODE_DONE, RETCODE_PLACED):
            raise MT5Error(
                f"Order rejected: retcode={retcode} ({result.get('comment', '')})"
            )
        return result

    def modify_order(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        """Modify SL/TP of existing position."""
        positions = self.get_positions()
        pos = next((p for p in positions if p.get("ticket") == ticket), None)
        if pos is None:
            logger.warning("Position %d not found", ticket)
            return False

        request: dict[str, Any] = {
            "action": TRADE_ACTION_SLTP,
            "symbol": pos.get("symbol", ""),
            "position": ticket,
            "sl": float(sl) if sl is not None else float(pos.get("sl", 0.0)),
            "tp": float(tp) if tp is not None else float(pos.get("tp", 0.0)),
        }

        result = self._to_dict(self._call("order_send", request))
        retcode = result.get("retcode")
        if retcode not in (RETCODE_DONE, RETCODE_PLACED):
            logger.warning("Modify failed for %d: %s", ticket, result.get("comment", ""))
            return False
        return True

    def close_position(self, ticket: int) -> bool:
        """Close a position by ticket."""
        positions = self.get_positions()
        pos = next((p for p in positions if p.get("ticket") == ticket), None)
        if pos is None:
            logger.warning("Position %d not found", ticket)
            return False

        symbol = pos.get("symbol", "")
        volume = float(pos.get("volume", 0.0))
        pos_type = int(pos.get("type", 0))

        if pos_type == ORDER_TYPE_BUY:
            close_type = ORDER_TYPE_SELL
            tick = self._to_dict(self._call("symbol_info_tick", symbol))
            price = float(tick.get("bid", 0.0))
        else:
            close_type = ORDER_TYPE_BUY
            tick = self._to_dict(self._call("symbol_info_tick", symbol))
            price = float(tick.get("ask", 0.0))

        request: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": int(self._config.get("slippage_points", 10)),
            "magic": 20250101,
            "comment": "close",
            "type_time": ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }

        result = self._to_dict(self._call("order_send", request))
        retcode = result.get("retcode")
        if retcode not in (RETCODE_DONE, RETCODE_PLACED):
            logger.warning("Close failed for %d: %s", ticket, result.get("comment", ""))
            return False
        return True

    # ── Health ────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Check connection health."""
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
                self._health_failures += 1
                if self._health_failures >= 3:
                    self._connected = False
            else:
                self._health_failures = 0

            return {
                "ok": ok,
                "connected": ok,
                "last_error": "" if ok else self._last_error,
                "uptime_s": time.time() - self._connected_at if ok else 0.0,
                "connect_count": self._connect_count,
            }

    def is_connected(self) -> bool:
        """Check if MT5 is connected and healthy."""
        return self.health_check()["ok"]

    def server_time(self) -> datetime:
        """Get the MT5 terminal server time."""
        info = self._to_dict(self._call("terminal_info"))
        value = int(info.get("time", 0) or 0)
        return datetime.fromtimestamp(value, tz=timezone.utc)

    def market_open(self, symbol: str) -> tuple[bool, str]:
        """Check if the market is open for a symbol."""
        info = self._to_dict(self._call("symbol_info", symbol))
        trade_mode = int(info.get("trade_mode", 0) or 0)
        if trade_mode == SYMBOL_TRADE_MODE_DISABLED:
            return False, "trading disabled"
        weekday = time.gmtime().tm_wday
        if weekday >= 5:
            return False, "weekend"
        tick = self._to_dict(self._call("symbol_info_tick", symbol))
        tick_time = int(tick.get("time", 0) or 0)
        if tick_time:
            age = time.time() - tick_time
            if age > 900:
                return False, f"no fresh quotes for {int(age)}s"
        return True, "market open"
