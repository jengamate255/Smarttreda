"""Shared utilities for web_dashboard: caching, tick handling, time helpers."""

import threading
import time
import datetime
import numpy as np
import MetaTrader5 as mt5

from mt5_mcp.client import get_client


# ──────────────────────────────────────────────────────────────────────
# Timeframe constants
# ──────────────────────────────────────────────────────────────────────
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
}

CLOSED_ENTRY = mt5.DEAL_ENTRY_OUT


# ──────────────────────────────────────────────────────────────────────
# Tick cache (1s TTL micro-cache)
# ──────────────────────────────────────────────────────────────────────
_tick_cache: dict = {}
_TICK_TTL = 1.0
_tick_lock = threading.Lock()


def _cached_tick(symbol: str):
    """symbol_info_tick with a 1s micro-cache (many endpoints share ticks)."""
    now = time.monotonic()
    key = symbol.upper()
    with _tick_lock:
        hit = _tick_cache.get(key)
        if hit and now - hit[0] < _TICK_TTL:
            return hit[1]
    tick = mt5.symbol_info_tick(symbol)
    with _tick_lock:
        _tick_cache[key] = (now, tick)
    return tick


def _tick_quote(symbol: str):
    """Return {"bid": .., "ask": ..} for an MT5 symbol, or None."""
    try:
        tick = _cached_tick(symbol)
        if tick is None:
            return None
        return {"bid": float(tick.bid), "ask": float(tick.ask)}
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────
# TTL Cache
# ──────────────────────────────────────────────────────────────────────
class _TTLCache:
    """Simple in-memory TTL cache."""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._data: dict = {}

    def get(self, key):
        hit = self._data.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.time() > ts:
            del self._data[key]
            return None
        return value

    def set(self, key, value) -> None:
        self._data[key] = (time.time() + self._ttl, value)


_scan_cache = _TTLCache(ttl_seconds=30)


def _cache_key(*args, **kwargs) -> tuple:
    return (args, tuple(sorted(kwargs.items())))


# ──────────────────────────────────────────────────────────────────────
# Server-time helpers (deal-time space)
# ──────────────────────────────────────────────────────────────────────
def _server_now() -> datetime.datetime:
    """Current date/time anchored to MT5 server clock (from last tick)."""
    tick = _cached_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return datetime.datetime.fromtimestamp(tick.time)
    return datetime.datetime.now()


def _server_epoch() -> int:
    """Current time in deal-time space (MT5 deal/order times)."""
    tick = _cached_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return int(tick.time)
    return int(time.time())


def _server_midnight_epoch() -> int:
    """Epoch of current server-time day boundary (deal-time space)."""
    now = _server_epoch()
    dt = datetime.datetime.fromtimestamp(now)
    return now - (dt.hour * 3600 + dt.minute * 60 + dt.second)


def _pad_front(arr: np.ndarray, n: int) -> np.ndarray:
    """Pad short series to length n by repeating first value."""
    if len(arr) >= n:
        return arr
    return np.pad(arr, (n - len(arr), 0), mode="edge")


def _deals_since(days: int):
    """Deals in last N days. Boundaries must be ints in deal-time space."""
    now = _server_epoch()
    return mt5.history_deals_get(now - max(1, days) * 86400, now)


# ──────────────────────────────────────────────────────────────────────
# Technical indicator helpers (numpy-based)
# ──────────────────────────────────────────────────────────────────────
def _ema(arr: np.ndarray, period: int):
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    ema = np.empty_like(arr)
    ema[:period - 1] = np.nan
    ema[period - 1] = arr[:period].mean()
    alpha = 2.0 / (period + 1.0)
    for i in range(period, len(arr)):
        ema[i] = alpha * arr[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def _rsi(arr: np.ndarray, period: int = 14):
    delta = np.diff(arr, prepend=arr[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.empty_like(arr)
    avg_loss = np.empty_like(arr)
    avg_gain[0] = 50.0
    avg_loss[0] = 50.0
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    alpha = 1.0 / period
    for i in range(period + 1, len(arr)):
        avg_gain[i] = alpha * gain[i - 1] + (1.0 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i - 1] + (1.0 - alpha) * avg_loss[i - 1]
    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi[:period] = 50.0
    return rsi


def _macd(arr: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(arr, fast)
    ema_slow = _ema(arr, slow)
    macd_line = ema_fast - ema_slow
    # _ema seeds with arr[:period].mean(); on a series with a NaN head
    # (macd_line before the slow EMA warms up) that mean is NaN and the
    # recursion then stays NaN forever, nulling the whole signal line and
    # histogram. Run the signal EMA over the finite tail of macd_line and
    # map it back onto the full-length series instead.
    finite = np.where(np.isfinite(macd_line))[0]
    signal_line = np.full_like(macd_line, np.nan)
    if finite.size:
        start = int(finite[0])
        signal_line[start:] = _ema(macd_line[start:], signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _pad_front_arr(arr: np.ndarray, target_len: int) -> np.ndarray:
    if len(arr) >= target_len:
        return arr[-target_len:]
    pad = np.full(target_len - len(arr), arr[0] if len(arr) > 0 else 0.0)
    return np.concatenate([pad, arr])


def _sanitize(arr: np.ndarray) -> list:
    """Convert numpy array to list, replacing NaN/Inf with None."""
    return [None if not np.isfinite(x) else float(x) for x in arr]


# ──────────────────────────────────────────────────────────────────────
# Health check loop
# ──────────────────────────────────────────────────────────────────────
def _health_check_loop(stop: threading.Event, interval: int):
    """Background watchdog: reconnect if MT5 connection drops."""
    while not stop.is_set():
        try:
            client = get_client()
            if client.is_connected():
                info = client.account_info()
                if info is None and not client.reconnect():
                    logger.error("Health check: reconnect failed")
            elif not client.reconnect():
                logger.error("Health check: reconnect failed")
        except Exception as exc:  # noqa: BLE001
            logger.error("Health check error: %s", exc)
        stop.wait(interval)


# ──────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _setup_logging() -> logging.Logger:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    fh = RotatingFileHandler(
        log_dir / "web_dashboard.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    eh = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    eh.setLevel(logging.WARNING)
    eh.setFormatter(fmt)
    root.addHandler(eh)

    logger = logging.getLogger("web_dashboard")
    logger.info("Logging initialized")
    return logger


logger = _setup_logging()