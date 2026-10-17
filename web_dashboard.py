"""FastAPI web dashboard for MT5 trading.

Serves a browser-based dashboard with account overview, open positions,
technical charts and trade history statistics. Reuses the connection and
indicator helpers from server.py.

Run:  python web_dashboard.py   (open http://localhost:8050)
"""

import asyncio
import datetime
import glob
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, skip

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import Response

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)
sys.path.append(os.path.join(_BASE_DIR, "mt5_mcp"))

# Import from canonical config/client
from mt5_mcp.config import MT5Config

# Local utilities
from web_dashboard_utils import (
    logger,
    TIMEFRAMES,
    CLOSED_ENTRY,
    _cached_tick,
    _tick_quote,
    _TTLCache,
    _cache_key,
    _scan_cache,
    _server_now,
    _server_epoch,
    _server_midnight_epoch,
    _pad_front,
    _deals_since,
    _ema,
    _rsi,
    _macd,
    _health_check_loop,
    _sanitize,
)

# MT5-MCP modules
from client import get_client
from memory.learning import best_pairs, best_sessions, best_setups
from memory.trade_journal import get_journal
from monte_carlo import build_day_pool, load_trades, simulate_month
from price_action import detect_patterns
from strategy import analyze_trend as bot_trend
from strategy import evaluate_strategy, market_structure
from supply_demand import detect_zones
from tools.news import news_filter
from tools.risk import risk_report
from tools.trades import close_position, modify_sl_tp, open_market

from mt5_mcp.chart_patterns import analyze_chart
from mt5_mcp.coach import coach_review
from mt5_mcp.market_intel import market_health, scan_markets
from mt5_mcp.research import report as research_report
from mt5_mcp.research import weekly_report
from mt5_mcp.tools.boom_crash_strategy import detect_spike

from backtest import (
    Backtester,
    BacktestParams,
    BacktestResult,
    load_frames,
    rebuild_summary,
    save_result,
)
from web_dojo import router as dojo_router


def _pad_front(arr: np.ndarray, target_len: int) -> np.ndarray:
    """Pad array front with first value to reach target_len."""
    if len(arr) >= target_len:
        return arr[-target_len:]
    pad = np.full(target_len - len(arr), arr[0] if len(arr) > 0 else 0.0)
    return np.concatenate([pad, arr])


BASE_DIR = _BASE_DIR
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.getenv("WEB_PORT", "8050"))
HOST = os.getenv("WEB_HOST", "127.0.0.1")
HEALTH_INTERVAL = int(os.getenv("WEB_HEALTH_INTERVAL", "15"))

_lock = threading.RLock()

WEB_AUTH_TOKEN = os.getenv("WEB_AUTH_TOKEN", "")

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


_tick_cache: dict = {}
_TICK_TTL = 1.0  # seconds - collapses bursts of identical tick lookups
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



def _health_check_loop(stop: threading.Event):
    """Background watchdog: reconnect if the MT5 connection drops."""
    while not stop.is_set():
        try:
            client = get_client()
            if client.is_connected():
                info = client.account_info()
                if info is None and not client.reconnect():
                    logger.error("Health check: reconnect failed")
            elif not client.reconnect():
                logger.error("Health check: reconnect failed")
        except Exception as exc:  # noqa: BLE001 - watchdog must never die
            logger.error("Health check error: %s", exc)
        stop.wait(HEALTH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("MT5 Dashboard starting up...")
    logger.info("Python %s | PID %d", sys.version.split()[0], os.getpid())

    # Determine account mode: PROP (default) or DERIV
    is_deriv = os.getenv("MT5_MCP_ACCOUNT", "PROP").upper() == "DERIV"
    prefix = "DERIV_MT5" if is_deriv else "MT5"

    # Read CLI args from environment (set by __main__ before uvicorn.run)
    login = os.getenv(f"{prefix}_LOGIN") or os.getenv(f"MT5_MCP_{prefix}_LOGIN")
    password = os.getenv(f"{prefix}_PASSWORD") or os.getenv(f"MT5_MCP_{prefix}_PASSWORD")
    server = os.getenv(f"{prefix}_SERVER") or os.getenv(f"MT5_MCP_{prefix}_SERVER")
    path = os.getenv(f"{prefix}_TERMINAL_PATH") or os.getenv(f"MT5_MCP_{prefix}_TERMINAL_PATH")
    
    # Build MT5Config (Pydantic model) with keyword args
    config_kwargs = {}
    if login:
        config_kwargs["login"] = int(login)
    if password:
        config_kwargs["password"] = password
    if server:
        config_kwargs["server"] = server
    if path:
        config_kwargs["terminal_path"] = path
    config = MT5Config(**config_kwargs)

    logger.info("Config: login=%s server=%s terminal_path=%s configured=%s mode=%s",
                config.login, config.server, config.terminal_path, config.is_configured, "DERIV" if is_deriv else "PROP")
    
    import asyncio
    loop = asyncio.get_event_loop()

    try:
        client = get_client()
        connected = await loop.run_in_executor(None, lambda: client.connect(config))
    except Exception as exc:
        logger.error("MT5 connect raised: %s", exc, exc_info=True)
        connected = False
    if connected:
        logger.info("MT5 connected successfully (login=%s)", config.login)
    else:
        logger.error("Initial MT5 connect FAILED — will retry via health check")
    stop = threading.Event()
    threading.Thread(target=_health_check_loop, args=(stop,), daemon=True).start()
    ws_task = asyncio.create_task(_ws_broadcast_loop())
    logger.info("Dashboard started (port %s, host %s)", PORT, HOST)
    try:
        yield
    finally:
        stop.set()
        ws_task.cancel()
        client = get_client()
        client.shutdown()
        logger.info("Dashboard stopped")


app = FastAPI(title="MT5 Web Dashboard", lifespan=lifespan)
app.include_router(dojo_router)


class NoCacheJS(BaseHTTPMiddleware):
    """Hashed static assets revalidate via ETag (fast 304s); HTML no-cache."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("/static/") and response.status_code == 200:
            response.headers["Cache-Control"] = "no-cache"
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce auth token on all /api/* and /ws endpoints."""

    # Rate limiting: sliding window per client IP (item #13).
    _rl_hits: dict = {}
    _RL_WINDOW = 60.0  # seconds
    _RL_MAX = 240      # requests per window per IP

    async def dispatch(self, request: Request, call_next):
        if not WEB_AUTH_TOKEN:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") and path != "/ws":
            return await call_next(request)
        token = request.headers.get("x-auth-token", "")
        if not token:
            token = request.query_params.get("token", "")
        if token != WEB_AUTH_TOKEN:
            return Response(content="Unauthorized", status_code=401)
        # Per-IP throttle (only after auth so valid users are keyed, not scanners)
        client_ip = request.client.host if request.client else "?"
        now = time.monotonic()
        hits = self._rl_hits.setdefault(client_ip, [])
        while hits and now - hits[0] > self._RL_WINDOW:
            hits.pop(0)
        if len(hits) >= self._RL_MAX:
            return Response(content="Rate limit exceeded", status_code=429)
        hits.append(now)
        return await call_next(request)


app.add_middleware(AuthMiddleware)
app.add_middleware(NoCacheJS)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/sw.js")
async def service_worker():
    """Serve the service worker at root scope. The frontend registers
    "/sw.js"; a /static/sw.js URL would cap the worker's scope at /static/
    and it could never control the "/" app page."""
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


def _has_exposure(client, symbol: str) -> bool:
    """Check if there are any positions or pending orders for a symbol."""
    positions = client.positions(symbol) or []
    orders = client.pending_orders(symbol) or []
    return bool(positions or orders)


def _server_now() -> datetime.datetime:
    """Current date/time anchored to the MT5 server clock (from the last tick)."""
    tick = _cached_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return datetime.datetime.fromtimestamp(tick.time)  # noqa: DTZ006
    return datetime.datetime.now()  # noqa: DTZ005


def _server_epoch() -> int:
    """Current time in deal-time space (server clock). MT5 deal/order times
    live in this space, so windows must be built from tick.time, not the
    local clock."""
    tick = _cached_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return int(tick.time)
    return int(time.time())


def _server_midnight_epoch() -> int:
    """Epoch of the current server-time day boundary (deal-time space)."""
    now = _server_epoch()
    dt = datetime.datetime.fromtimestamp(now)  # noqa: DTZ006
    return now - (dt.hour * 3600 + dt.minute * 60 + dt.second)


def _pad_front(arr: np.ndarray, n: int) -> np.ndarray:
    """Pad a short series to length n by repeating its first value."""
    if len(arr) >= n:
        return arr
    return np.pad(arr, (n - len(arr), 0), mode="edge")


def _deals_since(days: int):
    """Deals in the last N days. Both boundaries must be ints in deal-time
    space: this MT5 build rejects datetime objects for history windows."""
    now = _server_epoch()
    return mt5.history_deals_get(now - max(1, days) * 86400, now)


@app.get("/", response_model=None)
async def index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if WEB_AUTH_TOKEN:
        try:
            with open(html_path, encoding="utf-8") as fh:
                html = fh.read()
            # Replace placeholder meta token with actual token (keeps single tag)
            import re
            html = re.sub(
                r'<meta name="auth-token" content="[^"]*">',
                f'<meta name="auth-token" content="{WEB_AUTH_TOKEN}">',
                html,
                count=1,
            )
            return HTMLResponse(content=html)
        except OSError:
            pass
    return FileResponse(html_path)


def _server_offset() -> int:
    """Seconds between the MT5 server clock and the local clock."""
    tick = _cached_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return int(tick.time) - int(time.time())
    return 0


@app.get("/api/health")
async def health():
    """Health check - MT5 status via thread pool (non-blocking)."""
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        client = get_client()
        connected = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: client.is_connected()), timeout=5.0
        )
    except Exception:
        connected = False
    terminal = None
    if connected:
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: client.terminal_info()), timeout=5.0
            )
            if info is not None:
                terminal = {"company": info.company, "build": info.build, "connected": bool(info.connected), "name": info.name}
        except Exception:
            pass
    return {
        "connected": bool(connected),
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "terminal": terminal,
    }


@app.get("/api/ping")
def ping():
    """Simple ping endpoint that doesn't touch MT5."""
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}


@app.get("/api/account")
def account():
    with _lock:
        _require_connected()
        info = mt5.account_info()
        if info is None:
            raise HTTPException(status_code=500, detail=f"account_info failed: {mt5.last_error()}")

        positions = mt5.positions_get() or []
        floating = round(sum(p.profit + p.swap for p in positions), 2)

        deals = mt5.history_deals_get(_server_midnight_epoch(), _server_epoch()) or []
        realized = round(sum(d.profit + d.swap + d.fee for d in deals), 2)
        trade_count = sum(1 for d in deals if d.entry == CLOSED_ENTRY)

        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
            "currency": info.currency,
            "leverage": info.leverage,
            "floating_pnl": floating,
            "daily_realized": realized,
            "daily_pnl": round(realized + floating, 2),
            "daily_trades": trade_count,
            "server_time": _server_now().strftime("%H:%M:%S"),
        }


@app.get("/api/positions")
def positions():
    with _lock:
        _require_connected()
        items = mt5.positions_get() or []
        result = []
        for p in items:
            result.append(
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "price_current": p.price_current,
                    "profit": round(p.profit, 2),
                    "swap": round(p.swap, 2),
                    "total": round(p.profit + p.swap, 2),
                    "comment": p.comment,
                    "magic": getattr(p, "magic", 0) or 0,
                    "is_auto": (getattr(p, "magic", 0) or 0) == 235010,
                }
            )
        result.sort(key=lambda x: x["symbol"])
        return {"positions": result, "count": len(result)}


def _trade_source(magic: int, comment: str) -> str:
    """Classify a deal as auto-trader (which config) or manual."""
    c = (comment or "").lower()
    if "jp225_z40" in c:
        return "AT:JP225_Z40"
    if "eurusd_med" in c:
        return "AT:EURUSD_MED"
    if magic == 235010:
        return "AT:auto"
    return "manual"


@app.get("/api/history")
def history(days: int = Query(30, ge=1, le=365), limit: int = Query(30, ge=1, le=100)):
    with _lock:
        _require_connected()
        deals = _deals_since(max(1, days)) or []
        closed = [
            d for d in deals
            if d.entry == CLOSED_ENTRY and d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        ]
        closed.sort(key=lambda d: d.time, reverse=True)

        wins = [d for d in closed if d.profit > 0]
        losses = [d for d in closed if d.profit < 0]
        gross_profit = sum(d.profit for d in wins)
        gross_loss = sum(-d.profit for d in losses)
        net = sum(d.profit + d.swap + d.fee for d in closed)
        win_rate = 100 * len(wins) / len(closed) if closed else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0

        recent = [
            {
                "time": datetime.datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M"),  # noqa: DTZ006
                "symbol": d.symbol,
                "type": "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume": d.volume,
                "price": d.price,
                "profit": round(d.profit, 2),
                "swap": round(d.swap, 2),
                "total": round(d.profit + d.swap + d.fee, 2),
                "source": _trade_source(getattr(d, "magic", 0) or 0, getattr(d, "comment", "") or ""),
            }
            for d in closed[:limit]
        ]

        return {
            "days": days,
            "stats": {
                "trades": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "net_profit": round(net, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "largest_win": round(max((d.profit for d in wins), default=0.0), 2),
                "largest_loss": round(min((d.profit for d in losses), default=0.0), 2),
            },
            "recent": recent,
        }


@app.get("/api/symbols")
def symbols():
    with _lock:
        _require_connected()
        all_symbols = mt5.symbols_get() or []
        names = sorted(
            s.name for s in all_symbols
            if s.trade_mode in (mt5.SYMBOL_TRADE_MODE_FULL, mt5.SYMBOL_TRADE_MODE_LONGONLY)
            and not (s.name.startswith("_") or "." in s.name)
        )
        return {"symbols": names}


def _fetch_rates_raw(symbol: str, tf_key: str, count: int):
    """Fetch OHLCV bars for symbol/timeframe. Must be called under _lock."""
    _require_connected()
    client = get_client()
    try:
        data = client.rates(symbol, tf_key, count)
    except Exception as e:
        if not client.is_connected():
            raise HTTPException(status_code=503, detail="MT5 terminal not connected")
        raise HTTPException(status_code=404, detail=f"no rates for {symbol}: {e}")

    if data is None or len(data) == 0:
        if not client.is_connected():
            raise HTTPException(status_code=503, detail="MT5 terminal not connected")
        raise HTTPException(status_code=404, detail=f"no rates for {symbol}: {client.last_error()}")

    # client.rates() returns a pandas DataFrame with columns: time, open, high, low, close, tick_volume, spread
    df = data
    if pd.api.types.is_numeric_dtype(df["time"]):
        times = df["time"].astype("int64").tolist()
    else:
        # Pandas datetime storage may use ns or us; chart timestamps are seconds.
        times = pd.to_datetime(df["time"], utc=True).to_numpy(dtype="datetime64[s]").astype("int64").tolist()
    opens = df["open"].values.tolist()
    highs = df["high"].values.tolist()
    lows = df["low"].values.tolist()
    closes = df["close"].values.tolist()
    volumes = df["tick_volume"].astype(int).values.tolist()
    return times, opens, highs, lows, closes, volumes


from functools import lru_cache

RATES_CACHE_TTL = 5
BOT_CACHE_TTL = 20
TRADEPLAN_TTL = 30
MAX_CACHE_ENTRIES = 64

# Public cache storage for tests and runtime reuse.
_rates_cache: dict[tuple, tuple[float, dict]] = {}
_bot_cache: dict[tuple, tuple[float, dict]] = {}
_tradeplan_cache: dict[tuple, tuple[float, dict]] = {}


def _make_cache_key(symbol: str, timeframe: str, count: int):
    return (symbol.upper(), timeframe, max(20, min(count, 1000)))


@lru_cache(maxsize=MAX_CACHE_ENTRIES)
def _rates_cache_key(symbol: str, timeframe: str, count: int):
    return _make_cache_key(symbol, timeframe, count)


@app.get("/api/quote")
def quote(symbol: str = "EURUSD"):
    """Live top-of-book quote for the chart tick line: bid/ask/spread.

    The chart polls this every second to update the last candle; the same
    bid/ask/spread shape the /api/rates tick block produces is returned so
    the frontend can use both interchangeably."""
    try:
        tick = _cached_tick(symbol)
    except Exception:
        tick = None
    if tick is None or not tick.bid or not tick.ask:
        raise HTTPException(status_code=502, detail=f"no tick data for {symbol}")
    point = 0.0
    try:
        info = mt5.symbol_info(symbol)
        point = float(info.point) if info else 0.0
    except Exception:
        point = 0.0
    spread = 0
    if point > 0:
        try:
            spread = round((tick.ask - tick.bid) / point)
        except Exception:
            spread = 0
    return {
        "symbol": symbol.upper(),
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "spread": spread,
        "time": int(tick.time) if tick.time else None,
    }


@app.get("/api/rates")
def rates(symbol: str = "EURUSD", timeframe: str = "H1", count: int = Query(300, ge=20, le=1000)):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")
    key = _rates_cache_key(symbol, timeframe, count)

    now = time.monotonic()
    with _lock:
        cached = _rates_cache.get(key)
        if cached and now - cached[0] < RATES_CACHE_TTL:
            return cached[1]

    with _lock:
        times, opens, highs, lows, closes, volumes = _fetch_rates_raw(symbol, timeframe, count)
        n = len(closes)

        close_arr = np.array(closes, dtype=float)

        ema50 = _sanitize(_pad_front(_ema(close_arr, 50), n))
        ema200 = _sanitize(_pad_front(_ema(close_arr, 200), n))
        rsi_vals = _sanitize(_pad_front(_rsi(close_arr, 14), n))
        macd_line, macd_signal, macd_hist = _macd(close_arr)
        macd_line = _sanitize(_pad_front(macd_line, n))
        macd_signal = _sanitize(_pad_front(macd_signal, n))
        macd_hist = _sanitize(_pad_front(macd_hist, n))

        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)
        point = symbol_info.point if symbol_info else 0.0
        spread = 0
        if tick and point and point > 0:
            try:
                spread = round((tick.ask - tick.bid) / point)
            except Exception:
                spread = 0

        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": n,
            "digits": int(symbol_info.digits) if symbol_info else None,
            "point": float(point),
            "times": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi_vals,
            "macd": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "tick": (
                {"bid": tick.bid, "ask": tick.ask, "spread": spread}
                if tick
                else {"bid": closes[-1], "ask": closes[-1], "spread": 0}
            ),
        }
        # Final comprehensive sanitization
        def _sanitize_any(obj):
            """Recursively sanitize any object for JSON serialization."""
            import numpy as np
            if isinstance(obj, dict):
                return {k: _sanitize_any(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [_sanitize_any(v) for v in obj]
            elif isinstance(obj, (np.floating, np.integer)):
                val = float(obj) if np.isfinite(obj) else None
                return val
            elif isinstance(obj, float):
                return obj if np.isfinite(obj) else None
            else:
                return obj
        
        # Sanitize result before caching/returning
        def _deep_sanitize(obj):
            import numpy as np
            if isinstance(obj, dict):
                return {k: _deep_sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [_deep_sanitize(v) for v in obj]
            elif isinstance(obj, np.ndarray):
                return [_deep_sanitize(v) for v in obj.tolist()]
            elif isinstance(obj, (np.floating, np.integer)):
                val = float(obj) if np.isfinite(obj) else None
                return val
            elif isinstance(obj, float):
                return obj if np.isfinite(obj) else None
            else:
                return obj
        
        result = _deep_sanitize(result)
        _rates_cache[key] = (time.monotonic(), result)
        return result


@lru_cache(maxsize=MAX_CACHE_ENTRIES)
def _bot_cache_key(symbol: str, timeframe: str, count: int):
    return _make_cache_key(symbol, timeframe, count)


@lru_cache(maxsize=MAX_CACHE_ENTRIES)
def _tradeplan_cache_key(symbol: str, timeframe: str, lookback: int):
    return _make_cache_key(symbol, timeframe, lookback)


@app.get("/api/bot-analysis")
def bot_analysis(symbol: str = "EURUSD", timeframe: str = "H1", count: int = Query(300, ge=50, le=1000)):
    """Run the mt5_mcp bot's own analysis on the chart data: supply/demand
    zones, market structure (HH/HL/LH/LL swings), price-action patterns and
    EMA50/200 trend. Zones are scanned on a wider 1000-bar window (they are
    long-lived levels); all other analysis aligns with /api/rates bars.
    A formation/touch index of -1 means the event predates the visible chart."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")
    count = max(50, min(count, 1000))
    key = _bot_cache_key(symbol, timeframe, count)

    now = time.monotonic()
    with _lock:
        cached = _bot_cache.get(key)
        if cached and now - cached[0] < BOT_CACHE_TTL:
            return cached[1]

    result = _compute_bot_analysis(symbol, timeframe, count)
    with _lock:
        _bot_cache[key] = (time.monotonic(), result)
    return result


def _compute_bot_analysis(symbol: str, tf_key: str, count: int) -> dict:
    scan = 1000

    with _lock:
        times, opens, highs, lows, closes, volumes = _fetch_rates_raw(symbol, tf_key, scan)

    offset = scan - count
    dt_all = pd.to_datetime(times, unit="s")
    frame_all = pd.DataFrame(
        {
            "time": dt_all,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": volumes,
        }
    )
    frame_win = frame_all.tail(count).reset_index(drop=True)
    time_to_idx = {t: i for i, t in enumerate(dt_all[offset:])}

    zones = []
    for z in detect_zones(frame_all, lookback=scan, min_strength=30.0):
        if z.formation_time is None:
            continue
        formation = time_to_idx.get(z.formation_time)
        last_touch = z.last_touch_index
        if last_touch is not None:
            last_touch -= offset
        zones.append(
            {
                "type": z.type,
                "lower": round(float(z.lower), 6),
                "upper": round(float(z.upper), 6),
                "strength": z.strength,
                "touches": z.touches,
                "broken": z.broken,
                "fresh": z.fresh,
                "retested": z.retested,
                "formation_index": -1 if formation is None else formation,
                "last_touch_index": last_touch if last_touch is not None else -1,
            }
        )

    struct = market_structure(frame_win)
    prev_h = prev_l = None
    swings_h = []
    for idx in struct["highs"]:
        price = float(frame_win["high"].iloc[idx])
        label = "HH" if prev_h is not None and price > prev_h else ("LH" if prev_h is not None else "H")
        prev_h = price
        swings_h.append({"index": int(idx), "price": round(price, 6), "label": label})
    swings_l = []
    for idx in struct["lows"]:
        price = float(frame_win["low"].iloc[idx])
        label = "HL" if prev_l is not None and price > prev_l else ("LL" if prev_l is not None else "L")
        prev_l = price
        swings_l.append({"index": int(idx), "price": round(price, 6), "label": label})

    patterns = [
        {
            "pattern": p.pattern,
            "direction": p.direction,
            "confidence": p.confidence,
            "index": int(p.index),
            "price": round(float(p.price), 6),
        }
        for p in detect_patterns(frame_win, lookback=count, check_last=count)
    ]

    trend = None
    if len(frame_win) >= 210:
        t = bot_trend(frame_win)
        trend = {
            "direction": t.direction,
            "confidence": t.confidence,
            "ema_fast": t.ema_fast,
            "ema_slow": t.ema_slow,
            "hh": t.hh,
            "hl": t.hl,
            "lh": t.lh,
            "ll": t.ll,
            "last_swing_high": t.last_swing_high,
            "last_swing_low": t.last_swing_low,
        }

    return {
        "symbol": symbol,
        "timeframe": tf_key,
        "count": count,
        # Bar-open epoch seconds for the analysis window (frame_win). The
        # chart window can be shorter than this window because the
        # time-anchored /api/rates fetch loses bars to market gaps
        # (weekends), so clients must align bot bar indices to chart bars
        # via these times instead of assuming index equality.
        "times": [int(t) for t in times[-count:]],
        "trend": trend,
        "zones": zones,
        "swings": {"highs": swings_h, "lows": swings_l},
        "patterns": patterns,
    }


@lru_cache(maxsize=MAX_CACHE_ENTRIES)
def _tradeplan_cache_key(symbol: str, timeframe: str, lookback: int):
    return _make_cache_key(symbol, timeframe, lookback)


@app.get("/api/trade-plan")
def trade_plan(symbol: str = "EURUSD", timeframe: str = "H1", lookback: int = 300):
    """Run the bot's full strategy evaluation (trend, zone, confirmation,
    RR, lot sizing) for the current market and return the resulting trade
    plan for the chart timeframe."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")
    lookback = max(50, min(lookback, 1000))
    key = _tradeplan_cache_key(symbol, timeframe, lookback)
    _active_plan_key["key"] = (symbol.upper(), timeframe, lookback)

    now = time.monotonic()
    with _lock:
        cached = _tradeplan_cache.get(key)
        if cached and now - cached[0] < TRADEPLAN_TTL:
            return cached[1]

    try:
        plan = evaluate_strategy(
            symbol=symbol,
            confirmation_timeframe=timeframe,
            trend_timeframe="H4" if timeframe != "H4" else "D1",
            lookback=lookback,
        )
    except Exception as exc:  # noqa: BLE001 - strategy errors are surfaced as plans
        logger.error("trade plan failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "action": "NONE", "errors": [str(exc)]}

    result = plan.model_dump(mode="json")
    with _lock:
        _tradeplan_cache[key] = (time.monotonic(), result)
    return result


@app.get("/api/equity")
def equity(days: int = Query(30, ge=1, le=365)):
    """Balance curve + daily realized P&L built from deal history, anchored
    to the current balance. Day boundaries follow the MT5 server clock."""
    with _lock:
        _require_connected()
        days = max(1, min(days, 365))
        deals = _deals_since(days) or []
        offset = _server_offset()

        per_day: dict[str, float] = {}
        for d in deals:
            if d.type == mt5.DEAL_TYPE_BALANCE:
                net = float(d.profit)
            elif d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL) and d.entry == CLOSED_ENTRY:
                net = float(d.profit + d.swap + d.fee)
            else:
                continue
            day = datetime.datetime.fromtimestamp(int(d.time) + offset).strftime("%Y-%m-%d")  # noqa: DTZ006
            per_day[day] = per_day.get(day, 0.0) + net

        days_list = sorted(per_day)
        daily_pnl = [round(per_day[d], 2) for d in days_list]
        total = sum(daily_pnl)

        info = mt5.account_info()
        balance = float(info.balance) if info else 0.0
        positions = mt5.positions_get() or []
        floating = round(sum(p.profit + p.swap for p in positions), 2)

        curve = []
        cum = 0.0
        for p in daily_pnl:
            cum += p
            curve.append(round(balance - (total - cum), 2))

        return {
            "days": days,
            "dates": days_list,
            "daily_pnl": daily_pnl,
            "balance": curve,
            "floating": floating,
            "total": round(total, 2),
            "best_day": round(max(daily_pnl, default=0.0), 2),
            "worst_day": round(min(daily_pnl, default=0.0), 2),
            "profitable_days": sum(1 for p in daily_pnl if p > 0),
        }


@app.get("/api/journal")
def journal(limit: int = Query(30, ge=1, le=1000), sync: bool = False):
    """Trade journal entries from the mt5_mcp SQLite journal."""
    try:
        j = get_journal()
        if sync:
            try:
                result = j.sync_from_mt5(get_client())
                logger.info("journal sync: %s", result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("journal sync failed: %s", exc)
        entries = j.list_trades(limit=max(1, min(limit, 1000)))
        return {"entries": entries, "count": len(entries)}
    except Exception as exc:  # noqa: BLE001 - journal is best-effort
        logger.warning("journal endpoint failed: %s", exc)
        return {"entries": [], "count": 0, "error": str(exc)}


@app.post("/api/journal/{ticket}/notes")
def journal_notes(ticket: int, payload: dict):
    """Attach reason/mistakes/lessons to a journal entry."""
    fields = {k: v for k, v in payload.items() if k in ("reason", "mistakes", "lessons") and v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update (reason/mistakes/lessons)")
    try:
        ok = get_journal().update_notes(ticket, **fields)
    except Exception as exc:
        logger.warning("journal notes failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"ticket {ticket} not found")
    return {"ok": True, "ticket": ticket}



# ---------- Accounts & switching (reconstructed) ----------

@app.get("/api/accounts")
def accounts():
    """Configured MT5 accounts with active flag based on current session."""
    try:
        from mt5_mcp.config import get_settings

        settings = get_settings()
        try:
            info = get_client().account_info()
            active_login = int(info.get("login", 0)) if info else 0
        except Exception:
            active_login = 0
        result = []
        for label, cfg, name in (
            ("prop", settings.mt5, "Prop firm"),
            ("deriv", getattr(settings, "deriv_mt5", None), "Deriv synthetic"),
        ):
            if not cfg or not cfg.login:
                continue
            result.append({
                "login": int(cfg.login),
                "server": cfg.server,
                "name": f"{name} ({cfg.server})",
                "terminal": cfg.terminal_path,
                "active": int(cfg.login) == active_login,
                "label": label,
            })
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("accounts endpoint failed: %s", exc)
        return []


@app.post("/api/account/switch")
def account_switch(payload: dict):
    """Switch the shared client to another configured account (same process).

    Re-routes the mt5_mcp client layer by setting MT5_MCP_ACCOUNT, which is
    the supported dual-terminal mechanism. Terminals must already be logged
    in to their respective accounts.
    """
    try:
        import os as _os

        login = int(payload.get("login", 0))
        if not login:
            raise HTTPException(status_code=400, detail="login required")
        from mt5_mcp.config import get_settings

        settings = get_settings()
        target = None
        if getattr(settings, "mt5", None) and int(settings.mt5.login or 0) == login:
            target = "PROP"
        elif getattr(settings, "deriv_mt5", None) and int(settings.deriv_mt5.login or 0) == login:
            target = "DERIV"
        if not target:
            raise HTTPException(status_code=404, detail=f"account {login} not configured")
        _os.environ["MT5_MCP_ACCOUNT"] = target
        # Force a fresh connection attempt against the routed config.
        try:
            from client import _clients  # type: ignore

            _clients.clear()
        except Exception:
            pass
        client = get_client()
        client.ensure_connected()
        info = client.account_info()
        return {"ok": True, "login": info.get("login"), "routed": target}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("account switch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/account/switch-status")
def account_switch_status():
    return {"switching": False}


# ---------- AI detection & signals (reconstructed wrappers) ----------

def _ai_detection_report(symbol: str, timeframe: str, lookback: int = 300) -> dict:
    """Fetch candles and map analyze_chart output to the dashboard shape."""
    from mt5_mcp.tools.boom_crash_strategy import mt5_symbol as _mt5sym

    name = (
        _mt5sym(symbol)
        if any(k in symbol.upper() for k in ("BOOM", "CRASH", "VOLATILITY"))
        else symbol
    )
    frame = get_client().rates(name, timeframe, max(lookback + 100, 400))
    if frame is None or len(frame) == 0:
        raise RuntimeError(f"no rate data for {symbol} {timeframe}")
    import pandas as _pd

    if not isinstance(frame, _pd.DataFrame):
        frame = _pd.DataFrame(frame)
    report = analyze_chart(frame, symbol=symbol, timeframe=timeframe, lookback=lookback)
    patterns = []
    for p in report.get("patterns", []):
        patterns.append({
            "name": p.get("name") or p.get("pattern") or "pattern",
            "confidence": p.get("quality_score", p.get("confidence", 0)),
            "direction": p.get("direction", ""),
            "target": p.get("target"),
            "invalidation": p.get("invalidation"),
        })
    sr = [
        {"type": lv.get("type", "support"), "price": lv.get("price"),
         "strength": lv.get("strength", lv.get("quality", 50))}
        for lv in (report.get("levels") or report.get("support_resistance") or [])
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "patterns": patterns,
        "support_resistance": sr,
        "verdict": report.get("verdict", ""),
        "score": report.get("overall_score", report.get("score", 0)),
    }


@app.get("/api/ai-detection")
def ai_detection(
    symbol: str = Query("EURUSD"),
    timeframe: str = Query("H1"),
    lookback: int = Query(300, ge=100, le=1000),
):
    """Autochartist-style pattern + S/R detection with quality scores."""
    try:
        return _ai_detection_report(symbol.upper(), timeframe.upper(), lookback)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai-detection failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "patterns": [], "support_resistance": [], "error": str(exc)}


@app.get("/api/ai-detection/scan")
def ai_detection_scan(
    symbols: str = Query(""),
    timeframe: str = Query("H1"),
    min_quality: float = Query(60.0, ge=0.0, le=100.0),
):
    """Rank symbols by best available pattern quality."""
    try:
        universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not universe:
            universe = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD"]
        results = []
        for sym in universe[:12]:
            try:
                rep = _ai_detection_report(sym, timeframe.upper())
                best = max(
                    (p.get("confidence", 0) for p in rep.get("patterns", [])),
                    default=0,
                )
                if best >= min_quality:
                    results.append({"symbol": sym, "best_quality": best,
                                    "patterns": rep.get("patterns", [])})
            except Exception:  # noqa: BLE001
                continue
        results.sort(key=lambda r: -r["best_quality"])
        return {"scanned": len(universe), "results": results}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai-detection scan failed: %s", exc)
        return {"results": [], "error": str(exc)}


@app.get("/api/signals")
def get_signals(
    symbol: str = Query("BOOM500"),
    timeframe: str = Query("M1"),
    confidence_floor: float = Query(0.0, ge=0.0, le=100.0),
):
    """Boom/Crash spike signals (signals only exist for synthetic indices)."""
    sym = symbol.upper()
    if not any(k in sym for k in ("BOOM", "CRASH")):
        return {
            "symbol": sym, "signals": [], "enabled": False,
            "confidence_floor": confidence_floor,
            "message": "signals only available for Boom/Crash synthetic indices",
        }
    try:
        signal = detect_spike(sym, lookback_minutes=60)
        out = []
        if signal and signal.confidence >= confidence_floor:
            out.append({
                "symbol": sym,
                "direction": signal.direction,
                "confidence": round(signal.confidence, 1),
                "entry": signal.entry_price,
                "sl": signal.sl_price,
                "tp": signal.tp_price,
                "age": signal.spike_age_candles,
                "reason": signal.reason,
            })
        return {"symbol": sym, "signals": out, "enabled": True,
                "confidence_floor": confidence_floor}
    except Exception as exc:  # noqa: BLE001
        logger.warning("signals failed for %s: %s", symbol, exc)
        return {"symbol": sym, "signals": [], "enabled": True,
                "confidence_floor": confidence_floor, "error": str(exc)}


# ---------- Journal analytics (equity curve + distribution) ----------

@app.get("/api/journal/equity-curve")
def journal_equity_curve(days: int = Query(30, ge=1, le=365)):
    """Equity curve data for PnL chart: cumulative PnL over time."""
    try:
        j = get_journal()
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)
        ).isoformat()

        with j._lock:
            conn = j._conn()
            rows = conn.execute(
                "SELECT close_time, pnl, outcome, symbol, side, lots, entry, exit_price, setup "
                "FROM trades WHERE close_time >= ? AND pnl IS NOT NULL "
                "ORDER BY close_time ASC",
                [cutoff],
            ).fetchall()

        points = []
        cumulative = 0.0
        for row in rows:
            pnl = row["pnl"] or 0.0
            cumulative += pnl
            points.append({
                "time": row["close_time"],
                "pnl": round(pnl, 2),
                "cumulative": round(cumulative, 2),
                "outcome": row["outcome"],
                "symbol": row["symbol"],
                "setup": row["setup"] or "manual",
            })

        return {
            "points": points,
            "total_trades": len(points),
            "net_pnl": round(sum(p["pnl"] for p in points), 2),
            "max_dd": round(min(p["cumulative"] for p in points), 2) if points else 0,
            "max_runup": round(max(p["cumulative"] for p in points), 2) if points else 0,
        }
    except Exception as exc:
        logger.warning("journal equity curve failed: %s", exc)
        return {"error": str(exc)}


def _histogram(values, bins=20):
    if not values:
        return {"bins": [], "counts": []}
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    width = (hi - lo) / bins
    counts = [0] * bins
    edges = [lo + i * width for i in range(bins + 1)]
    for v in values:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    bin_centers = [round((edges[i] + edges[i + 1]) / 2, 4) for i in range(bins)]
    return {"bins": bin_centers, "counts": counts}


@app.get("/api/journal/distribution")
def journal_distribution(days: int = Query(30, ge=1, le=365)):
    """PnL distribution: histogram + summary stats for the distribution panel."""
    try:
        j = get_journal()
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)
        ).isoformat()

        with j._lock:
            conn = j._conn()
            rows = conn.execute(
                "SELECT pnl, outcome FROM trades WHERE close_time >= ? AND pnl IS NOT NULL",
                [cutoff],
            ).fetchall()

        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        rs = []
        for w, l in ((wins, avg_win and -avg_loss or 1),):
            pass
        avg_abs_loss = abs(avg_loss) if avg_loss else 1.0
        rs = [p / avg_abs_loss for p in pnls]

        return {
            "days": days,
            "total_trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
            "largest_win": round(max(pnls), 2) if pnls else 0.0,
            "largest_loss": round(min(pnls), 2) if pnls else 0.0,
            "net_pnl": round(sum(pnls), 2),
            "pnl_histogram": _histogram(pnls, bins=20),
        }
    except Exception as exc:
        logger.warning("journal distribution failed: %s", exc)
        return {"error": str(exc)}


# ---------- Strategy Performance Dashboard ----------

@app.get("/api/strategy_performance")
def strategy_performance(
    window_30: bool = True,
    window_60: bool = True,
    window_90: bool = True,
):
    """Per-strategy performance metrics with rolling windows (30/60/90 days).

    Groups journal trades by setup (strategy) field and calculates:
    total trades, win rate, total P&L, profit factor, avg RR.
    """
    try:
        j = get_journal()
        now = datetime.datetime.now(datetime.timezone.utc)

        windows = {}
        for label, days in [("30d", 30), ("60d", 60), ("90d", 90)]:
            if label == "30d" and not window_30:
                continue
            if label == "60d" and not window_60:
                continue
            if label == "90d" and not window_90:
                continue

            cutoff = (now - datetime.timedelta(days=days)).isoformat()
            with j._lock:
                conn = j._conn()
                rows = conn.execute(
                    "SELECT setup, pnl, rr, outcome FROM trades "
                    "WHERE close_time >= ? AND pnl IS NOT NULL "
                    "ORDER BY close_time ASC",
                    [cutoff],
                ).fetchall()

            # Group by strategy/setup
            by_setup: dict[str, dict] = {}
            for row in rows:
                setup = row["setup"] or "manual"
                if setup not in by_setup:
                    by_setup[setup] = {
                        "trades": 0, "wins": 0, "losses": 0,
                        "pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
                        "rr_values": [],
                    }
                s = by_setup[setup]
                s["trades"] += 1
                pnl = float(row["pnl"] or 0.0)
                s["pnl"] += pnl
                if pnl > 0:
                    s["wins"] += 1
                    s["gross_win"] += pnl
                elif pnl < 0:
                    s["losses"] += 1
                    s["gross_loss"] += abs(pnl)
                if row["rr"] is not None:
                    s["rr_values"].append(float(row["rr"]))

            # Compute derived metrics
            strategies = {}
            for name, s in by_setup.items():
                total = s["wins"] + s["losses"]
                pf = (
                    round(s["gross_win"] / s["gross_loss"], 2)
                    if s["gross_loss"] > 0
                    else (999.0 if s["gross_win"] > 0 else 0.0)
                )
                rr_vals = s["rr_values"]
                strategies[name] = {
                    "trades": s["trades"],
                    "wins": s["wins"],
                    "losses": s["losses"],
                    "win_rate": round(s["wins"] / total * 100, 1) if total else 0.0,
                    "total_pnl": round(s["pnl"], 2),
                    "profit_factor": pf,
                    "avg_rr": round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0.0,
                    "avg_win": round(s["gross_win"] / s["wins"], 2) if s["wins"] else 0.0,
                    "avg_loss": round(s["gross_loss"] / s["losses"], 2) if s["losses"] else 0.0,
                }

            # Overall totals for this window
            all_pnls = [float(r["pnl"] or 0.0) for r in rows]
            all_wins = [p for p in all_pnls if p > 0]
            all_losses_p = [p for p in all_pnls if p < 0]
            total_gw = sum(all_wins)
            total_gl = abs(sum(all_losses_p))
            total_trades = len(all_pnls)
            total_wins = len(all_wins)

            windows[label] = {
                "days": days,
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_pnl": round(sum(all_pnls), 2),
                "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0.0,
                "profit_factor": round(total_gw / total_gl, 2) if total_gl > 0 else (
                    999.0 if total_gw > 0 else 0.0
                ),
                "strategies": strategies,
            }

        return {"windows": windows}
    except Exception as exc:
        logger.warning("strategy performance failed: %s", exc)
        return {"error": str(exc)}


# ---------- Extracted routers ----------
from web_backtests import router as backtests_router  # noqa: E402
import web_boomcrash as _boomcrash  # noqa: E402
from web_account import router as account_router  # noqa: E402
from web_research import router as research_router  # noqa: E402

app.include_router(backtests_router)
app.include_router(research_router)

# Auto Trader monitor (deployed JP225_Z40 / EURUSD_MED unattended runner)
try:
    from web_autotrader import router as autotrader_router  # noqa: E402
    app.include_router(autotrader_router)
except ImportError:
    pass

# ForexDojo RL training engine
try:
    from web_dojo import router as dojo_router  # noqa: E402
    app.include_router(dojo_router)
except ImportError:
    pass

# Diagnostic dashboard (pipeline health + per-symbol gate traces)
try:
    from web_diagnostic import router as diagnostic_router  # noqa: E402
    app.include_router(diagnostic_router)
except ImportError:
    pass


def _require_connected():
    if not get_client().is_connected():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")


_boomcrash.configure(require_connected=_require_connected, lock=_lock)
app.include_router(_boomcrash.router)
import web_account as _account_mod
_account_mod.configure(require_connected=_require_connected, tick_quote=_tick_quote)
app.include_router(account_router)

from web_challenge import router as challenge_router  # noqa: E402
app.include_router(challenge_router)



def _forex_args():
    """Defaults namespace mirroring forex_breakout_watch argparse."""
    import argparse as _ap

    args = _ap.Namespace()
    args.live = False
    args.once = False
    args.interval = 60.0
    args.confidence_floor = 65.0
    args.range_bars = 24
    args.min_rr = 1.2
    args.rr = 1.5
    args.risk_percent = 0.5
    args.symbols = ""
    args.trend_tf = "H1"
    args.lookback = 400
    return args



# ---------- Forex Breakout Signals ----------

@app.get("/api/forex-breakout/scan")
def forex_breakout_scan(
    symbols: str = Query("", description="Comma-separated symbols, default: all forex majors"),
    min_confidence: float = Query(65.0, ge=0.0, le=100.0),
    range_bars: int = Query(24, ge=5, le=100),
    min_rr: float = Query(1.2, ge=0.5, le=5.0),
    rr: float = Query(1.5, ge=0.5, le=5.0),
    risk_percent: float = Query(0.5, ge=0.1, le=5.0),
):
    """Scan forex symbols for Donchian range breakout setups (same logic as forex_breakout_watch.py)."""
    # Cache key includes all parameters that affect results
    cache_key = _cache_key("forex_scan", symbols=symbols, min_confidence=min_confidence,
                           range_bars=range_bars, min_rr=min_rr, rr=rr, risk_percent=risk_percent)
    cached = _scan_cache.get(cache_key)
    if cached is not None:
        return cached

    with _lock:
        _require_connected()
    try:
        from forex_breakout_watch import _find_breakout, FOREX_SYMBOLS

        args = _forex_args()
        args.symbols = symbols if symbols else ",".join(FOREX_SYMBOLS)
        args.confidence_floor = min_confidence
        args.range_bars = range_bars
        args.min_rr = min_rr
        args.rr = rr
        args.risk_percent = risk_percent
        args.live = False  # dry-run for scanning

        client = get_client()
        client.ensure_connected()

        symbols_list = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        results = []
        for sym in symbols_list:
            try:
                if _has_exposure(client, sym):
                    continue
                setup = _find_breakout(client, None, args, sym)
                if setup:
                    results.append({
                        "symbol": sym,
                        "side": setup["side"],
                        "entry": round(setup["entry"], 5),
                        "sl": round(setup["sl"], 5),
                        "tp": round(setup["tp"], 5),
                        "rr": setup["rr"],
                        "confidence": round(setup["confidence"], 1),
                        "range_high": round(setup["range_high"], 5),
                        "range_low": round(setup["range_low"], 5),
                        "atr": round(setup["atr"], 5),
                    })
            except Exception as exc:
                logger.debug("breakout scan failed for %s: %s", sym, exc)

        result = {
            "scanned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbols_scanned": len(symbols_list),
            "signals": sorted(results, key=lambda x: x["confidence"], reverse=True),
            "count": len(results),
        }
        _scan_cache.set(cache_key, result)
        return result
    except Exception as exc:
        logger.warning("forex breakout scan failed: %s", exc)
        return {"signals": [], "count": 0, "error": str(exc)}


# ---------- Signal Quality Tracker ----------

@app.get("/api/signal-quality")
def signal_quality(
    symbol: str = Query("", description="Filter by symbol"),
    setup: str = Query("", description="Filter by setup type"),
    min_confidence: float = Query(0.0, ge=0.0, le=100.0),
    days: int = Query(30, ge=1, le=365),
):
    """Signal quality statistics: win rate, profit factor, R multiple by confidence tier and setup."""
    try:
        from mt5_mcp.memory.trade_journal import get_journal
        journal = get_journal()
        stats = journal.get_signal_stats(
            symbol=symbol.upper() if symbol else "",
            setup=setup,
            min_confidence=min_confidence,
            days=days,
        )
        return stats
    except Exception as exc:
        logger.warning("signal quality failed: %s", exc)
        return {"error": str(exc)}


# ---------- Market Intelligence Agent ----------

@app.get("/api/market-intelligence/{symbol}")
def market_intelligence(symbol: str, timeframe: str = "H1", lookback: int = 400):
    """Market Health Score (0-100) with regime, trend, session, spread, news, liquidity."""
    with _lock:
        _require_connected()
    try:
        return market_health(symbol.upper(), timeframe, lookback)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market intelligence failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "health_score": 0.0, "verdict": "unavailable", "errors": [str(exc)]}


@app.get("/api/market-scan")
def market_scan(symbols: str = "", timeframe: str = "H1", min_score: float = 0.0, max_results: int = 20):
    """Rank multiple symbols by Market Health Score. symbols=comma-separated or empty for majors."""
    with _lock:
        _require_connected()
    try:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
        return scan_markets(sym_list, timeframe, 300, min_score, max_results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market scan failed: %s", exc)
        return {"scanned": 0, "ranked": [], "failed": [str(exc)]}


# ---------- Live strategy overlay (chart) ----------

def _atr_frame(frame: pd.DataFrame, period: int = 14) -> float:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


@app.get("/api/live-overlay")
def live_overlay(
    symbol: str = "EURUSD",
    range_bars: int = Query(24, ge=5, le=100),
    atr_period: int = Query(14, ge=2, le=60),
):
    """Strategy overlay for the chart: Donchian range, H1 EMA200 trend and the
    live breakout setup (same math as forex_breakout_watch.py), plus the
    boom/crash spike levels when the symbol is a synthetic index."""
    api_key = re.sub(r"\s+", "", symbol.upper())
    api_key = re.sub(r"^(BOOM|CRASH)(\d*)INDEX$", r"\1\2", api_key)
    api_key = re.sub(r"^(BOOM|CRASH)0+", r"\1", api_key)
    is_boom_crash = bool(re.fullmatch(r"(BOOM|CRASH)\d+", api_key))

    try:
        with _lock:
            times_m15, _o, hh, ll, cc, _v = _fetch_rates_raw(symbol, "M15", 100)
            hh2 = ll2 = cc2 = None
            try:
                times_h1, _o2, hh2, ll2, cc2, _v2 = _fetch_rates_raw(symbol, "H1", 300)
            except HTTPException:
                pass

        result: dict = {
            "symbol": symbol,
            "is_boom_crash": is_boom_crash,
            "range_bars": range_bars,
            "range": None,
            "trend": None,
            "breakout": None,
            "spike": None,
        }

        df_m15 = pd.DataFrame({"high": hh, "low": ll, "close": cc})
        atr_m15 = _atr_frame(df_m15, atr_period)
        if len(df_m15) >= range_bars + 2 and atr_m15 > 0:
            window = df_m15.iloc[-(range_bars + 1): -1]
            range_high = float(window["high"].max())
            range_low = float(window["low"].min())
            range_height = range_high - range_low
            compactness = (
                min(100.0, 2.0 * atr_m15 / range_height * 100.0)
                if range_height > 0
                else 50.0
            )
            result["range"] = {
                "high": round(range_high, 6),
                "low": round(range_low, 6),
                "atr": round(atr_m15, 6),
                "compactness": round(compactness, 1),
            }

            # H1 EMA200 trend filter (same as _ema200_direction).
            if hh2 is not None:
                df_h1 = pd.DataFrame({"high": hh2, "low": ll2, "close": cc2})
            if hh2 is not None and len(df_h1) >= 220:
                closes = df_h1["close"].astype(float)
                ema200 = float(closes.tail(220).ewm(span=200, adjust=False).mean().iloc[-1])
                last = float(closes.iloc[-1])
                h1_atr = _atr_frame(df_h1, 14)
                if last > ema200 + 0.3 * h1_atr:
                    trend_dir = "bull"
                elif last < ema200 - 0.3 * h1_atr:
                    trend_dir = "bear"
                else:
                    trend_dir = "flat"
                result["trend"] = {
                    "direction": trend_dir,
                    "ema200": round(ema200, 6),
                    "atr_h1": round(h1_atr, 6),
                }

                # Breakout candidates (mirror forex_breakout_watch confidence).
                last_close = float(df_m15["close"].iloc[-1])
                buffer = 0.2 * atr_m15
                candidates = []
                if trend_dir != "bear":
                    entry_buy = range_high + buffer
                    if last_close - range_high <= 0.5 * atr_m15:
                        sl_buy = range_low - 0.8 * atr_m15
                        risk = entry_buy - sl_buy
                        if risk > 0:
                            candidates.append(("BUY", entry_buy, sl_buy, risk))
                if trend_dir != "bull":
                    entry_sell = range_low - buffer
                    if range_low - last_close <= 0.5 * atr_m15:
                        sl_sell = range_high + 0.8 * atr_m15
                        risk = sl_sell - entry_sell
                        if risk > 0:
                            candidates.append(("SELL", entry_sell, sl_sell, risk))
                best = None
                best_conf = -1.0
                for side, entry, sl, risk in candidates:
                    align = 25.0 if (side == "BUY" and trend_dir == "bull") or (side == "SELL" and trend_dir == "bear") else 0.0
                    overshoot = max(0.0, min(
                        (last_close - range_high) if side == "BUY" else (range_low - last_close),
                        0.5 * atr_m15,
                    ))
                    momentum = min(100.0, overshoot / max(0.5 * atr_m15, 1e-12) * 100.0)
                    confidence = 0.45 * compactness + align + 0.2 * momentum
                    if confidence > best_conf:
                        best_conf = confidence
                        best = (side, entry, sl, entry + 1.5 * risk if side == "BUY" else entry - 1.5 * risk)
                if best:
                    result["breakout"] = {
                        "side": best[0],
                        "entry": round(best[1], 6),
                        "sl": round(best[2], 6),
                        "tp": round(best[3], 6),
                        "confidence": round(best_conf, 1),
                    }

        if is_boom_crash:
            try:
                signal = detect_spike(api_key)
                if signal:
                    result["spike"] = {
                        "direction": signal.direction,
                        "spike_high": signal.spike_high,
                        "spike_low": signal.spike_low,
                        "entry_price": signal.entry_price,
                        "sl_price": signal.sl_price,
                        "tp_price": signal.tp_price,
                        "confidence": signal.confidence,
                        "spike_age_candles": signal.spike_age_candles,
                        "atr": signal.atr,
                        "drift_direction": signal.drift_direction,
                        "retracement": signal.retracement,
                        "current_price": signal.current_price,
                    }
            except Exception as exc:  # noqa: BLE001 - spike is best-effort
                logger.debug("live-overlay spike failed for %s: %s", symbol, exc)
        return result
    except HTTPException:
        return {"symbol": symbol, "error": "no data"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("live-overlay failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


# ---------- Trade events (watcher fills/closes/trails) ----------

@app.get("/api/events")
def trade_events(
    since: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Trade events emitted by the watchers (fills, orders, exits, trails)."""
    from mt5_mcp.trade_events import read_since

    events, latest = read_since(since, limit)
    return {"events": events, "latest_id": latest}


# ---------- Peer dashboard (dual-account view) ----------

PEER_PORT = int(os.getenv("WEB_PEER_PORT", "0"))
PEER_NAME = os.getenv("WEB_PEER_NAME", "").strip() or "other account"
_peer_cache: dict = {"ts": 0.0, "data": None}


def _fetch_peer_data() -> dict | None:
    if not PEER_PORT:
        return None
    now = time.monotonic()
    if _peer_cache["data"] is not None and now - _peer_cache["ts"] < 5:
        return _peer_cache["data"]
    data = {"configured": True, "name": PEER_NAME, "port": PEER_PORT, "ok": False, "error": None}
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{PEER_PORT}/api/account", timeout=3
        ) as resp:
            account = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PEER_PORT}/api/positions", timeout=3
        ) as resp:
            positions = json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PEER_PORT}/api/health", timeout=3
        ) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        data.update(
            {
                "ok": True,
                "account": account,
                "positions": positions.get("positions", []),
                "health": health,
            }
        )
    except Exception as exc:  # noqa: BLE001
        data["error"] = str(exc)
    _peer_cache.update(ts=now, data=data)
    return data


@app.get("/api/peer")
def peer_status():
    """Live snapshot of the other dashboard's account (dual-account view)."""
    data = _fetch_peer_data()
    if data is None:
        return {"configured": False}
    return data

def _execute_plan_inner(symbol: str, timeframe: str) -> dict:
    """Shared: rebuild the plan server-side, verify risk limits, then send
    the market order through the safety-checked trades path."""
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")
    try:
        plan = evaluate_strategy(
            symbol=symbol,
            confirmation_timeframe=timeframe,
            trend_timeframe="H4" if timeframe != "H4" else "D1",
            lookback=300,
        )
    except Exception as exc:
        logger.error("execute-plan evaluation failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=f"plan evaluation failed: {exc}") from exc

    if plan.action == "NONE" or not plan.valid:
        raise HTTPException(
            status_code=400,
            detail=f"no valid setup: {plan.reason or 'action NONE'}",
        )
    if plan.entry is None or plan.sl is None or plan.tp is None or not plan.lots:
        raise HTTPException(status_code=400, detail="plan missing entry/SL/TP/lots")

    rr = risk_report()
    if not rr.get("trading_allowed", True):
        raise HTTPException(
            status_code=400,
            detail="risk limits block trading: " + "; ".join(rr.get("warnings", [])),
        )

    try:
        result = open_market(
            symbol=symbol,
            side=plan.action,
            lots=plan.lots,
            sl=plan.sl,
            tp=plan.tp,
            comment=f"dashplan-{timeframe}",
        )
    except Exception as exc:
        logger.warning("execute-plan order failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    logger.info("execute-plan %s %s %s lots=%s -> %s", symbol, plan.action, plan.lots, payload.get("ticket"), payload.get("error"))
    return payload


@app.post("/api/execute-plan")
def execute_plan(payload: dict):
    """Execute the current bot trade plan for a symbol/timeframe.

    Re-evaluates the plan server-side (client cannot spoof entry/SL/TP/lots),
    refuses when risk limits are hit, then routes the order through the
    safety-checked trades path (spread, stop level, margin, lot range)."""
    symbol = str((payload or {}).get("symbol", "")).upper()
    timeframe = str((payload or {}).get("timeframe", "H1"))
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    with _lock:
        _require_connected()
    return _execute_plan_inner(symbol, timeframe)


@app.post("/api/positions/{ticket}/sltp")
def position_sltp(ticket: int, payload: dict):
    """Modify stop loss and/or take profit of an open position."""
    sl = payload.get("sl")
    tp = payload.get("tp")
    if sl is None and tp is None:
        raise HTTPException(status_code=400, detail="provide sl and/or tp")
    with _lock:
        _require_connected()
    try:
        result = modify_sl_tp(ticket, sl=float(sl) if sl is not None else None, tp=float(tp) if tp is not None else None)
    except Exception as exc:
        logger.warning("modify sl/tp failed for %s: %s", ticket, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)


@app.post("/api/positions/{ticket}/close")
def position_close(ticket: int):
    """Close an open position by ticket."""
    with _lock:
        _require_connected()
    try:
        result = close_position(ticket)
    except Exception as exc:
        logger.warning("close failed for %s: %s", ticket, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)


@app.post("/api/client-error")
async def client_error(payload: dict):
    """Receive frontend error reports for monitoring/debugging."""
    errors = payload.get("errors", [])
    for e in errors:
        logger.warning(
            "CLIENT_ERROR | session=%s | msg=%s | src=%s:%s | stack=%s",
            e.get("sessionId"),
            e.get("message"),
            e.get("source"),
            e.get("lineno"),
            (e.get("stack") or "")[:200],
        )
    return {"ok": True, "received": len(errors)}


_ws_clients: set[WebSocket] = set()
_ws_subscriptions: dict[WebSocket, set[str]] = {}
_ws_lock = threading.Lock()
WS_INTERVAL = 5

# Notification state shared with the broadcast loop.
_ws_state: dict = {"positions": set(), "plan_key": None, "plan_action": None}
_active_plan_key: dict = {"key": None}
_deals_cache: dict = {"ts": 0, "realized": 0.0, "count": 0}
_DEALS_CACHE_TTL = 5  # seconds

def _summary_snapshot() -> dict:
    """Quick account + positions snapshot for WebSocket push."""
    try:
        client = get_client()
        client.ensure_connected()
        info = client.account_info()
        if info is None:
            return {"type": "summary", "connected": False}
        # MT5Client uses positions() not positions_get(), returns list[dict]
        try:
            positions = client.positions() or []
        except AttributeError:
            positions = getattr(client, "positions_get", lambda: [])() or []

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        floating = round(sum(float(_get(p, "profit", 0) or 0) + float(_get(p, "swap", 0) or 0) for p in positions), 2)

        now = time.time()
        if now - _deals_cache["ts"] >= _DEALS_CACHE_TTL:
            # MT5Client: deals_since(epoch) ; fallback to history_deals_get
            try:
                deals = client.deals_since(_server_midnight_epoch()) or []
            except AttributeError:
                try:
                    deals = client.history_deals_get(_server_midnight_epoch(), _server_epoch()) or []
                except Exception:
                    deals = []
            _deals_cache["ts"] = now
            _deals_cache["realized"] = round(sum(float(_get(d, "profit", 0) or 0) + float(_get(d, "swap", 0) or 0) + float(_get(d, "fee", 0) or 0) for d in deals), 2)
            _deals_cache["count"] = sum(1 for d in deals if _get(d, "entry", None) == CLOSED_ENTRY)
        realized = _deals_cache["realized"]
        trade_count = _deals_cache["count"]

        return {
                "type": "summary",
                "connected": True,
                "account": {
                    "login": info.get("login"),
                    "balance": info.get("balance"),
                    "equity": info.get("equity"),
                    "free_margin": info.get("margin_free"),
                    "margin_level": info.get("margin_level"),
                    "currency": info.get("currency"),
                    "floating_pnl": floating,
                    "daily_realized": realized,
                    "daily_pnl": round(realized + floating, 2),
                    "daily_trades": trade_count,
                    "server_time": _server_now().strftime("%H:%M:%S"),
                },
                "positions": [
                    {
                        "ticket": _get(p, "ticket"),
                        "symbol": _get(p, "symbol"),
                        "type": "BUY" if _get(p, "type") == 0 else "SELL",
                        "volume": _get(p, "volume"),
                        "price_open": _get(p, "price_open"),
                        "sl": _get(p, "sl"),
                        "tp": _get(p, "tp"),
                        "price_current": _get(p, "price_current"),
                        "total": round(float(_get(p, "profit", 0) or 0) + float(_get(p, "swap", 0) or 0), 2),
                    }
                    for p in positions
                ],
            }
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        logger.warning("ws snapshot failed: %s", exc)
        return {"type": "summary", "connected": False}


def _breakout_snapshot():
    """Collect read-only breakout candidates outside the HTTP event loop."""
    from forex_breakout_watch import _find_breakout, FOREX_SYMBOLS

    args = _forex_args()
    args.confidence_floor = 25.0
    args.live = False
    client = get_client()
    client.ensure_connected()
    signals = []
    for sym in FOREX_SYMBOLS:
        try:
            if _has_exposure(client, sym):
                continue
            setup = _find_breakout(client, None, args, sym)
            if setup:
                signals.append({
                    "symbol": sym,
                    "side": setup["side"],
                    "entry": round(setup["entry"], 5),
                    "sl": round(setup["sl"], 5),
                    "tp": round(setup["tp"], 5),
                    "confidence": round(setup["confidence"], 1),
                    "range_high": round(setup["range_high"], 5),
                    "range_low": round(setup["range_low"], 5),
                    "atr": round(setup["atr"], 5),
                })
        except Exception:
            logger.debug("Breakout snapshot unavailable for %s", sym, exc_info=True)
    return sorted(signals, key=lambda signal: signal["confidence"], reverse=True)


async def _ws_broadcast_loop():
    """Broadcast live account/position/compliance/breakout updates to all WS clients."""
    position_interval = 3  # seconds (was 1 - hammered MT5 IPC 86k times/day)
    compliance_interval = 30  # seconds
    breakout_interval = 60  # seconds
    alerts_interval = 15  # seconds

    last_compliance = 0
    last_breakout = 0
    last_alerts = 0
    last_position_push = 0.0
    last_payload_sig: str = ""

    while True:
        await asyncio.sleep(1)
        if not _ws_clients:
            continue

        now = time.time()

        # Position/account snapshot - throttled + deduplicated
        if now - last_position_push < position_interval:
            payload = None
        else:
            last_position_push = now
            payload = await asyncio.to_thread(_summary_snapshot)
            sig = json.dumps(payload, sort_keys=True, default=str)
            if sig == last_payload_sig:
                payload = None  # nothing changed - skip network write
            else:
                last_payload_sig = sig
        
        notify = []
        if payload is not None and payload.get("connected"):
            current = {p["ticket"] for p in payload.get("positions", [])}
            closed = _ws_state["positions"] - current
            if closed:
                notify.append({"type": "notify", "kind": "position_closed", "tickets": sorted(closed)})
            _ws_state["positions"] = current

            key = _active_plan_key["key"]
            if key:
                cached = _tradeplan_cache.get(key)
                if cached:
                    action = cached[1].get("action")
                    if _ws_state["plan_key"] != key:
                        _ws_state["plan_key"] = key
                        _ws_state["plan_action"] = action
                    elif action != _ws_state["plan_action"]:
                        notify.append({
                            "type": "notify",
                            "kind": "plan_changed",
                            "symbol": key[0],
                            "action": action,
                        })
                        _ws_state["plan_action"] = action

            # Send compliance status periodically
            if now - last_compliance >= compliance_interval:
                try:
                    from mt5_mcp.compliance import evaluate as compliance_evaluate
                    comp = await asyncio.to_thread(compliance_evaluate)
                    payload["compliance"] = {
                        "mode": comp.get("mode"),
                        "halt_reason": comp.get("halt_reason", ""),
                        "daily_used_pct": round(comp.get("daily_used", 0) * 100, 1),
                        "dd_pct": round(comp.get("dd_pct", 0), 2),
                        "trades_today": comp.get("trades_today", 0),
                        "max_trades": comp.get("max_trades_per_day", 0),
                        "daily_pnl": comp.get("daily_pnl", 0),
                    }
                    last_compliance = now
                except Exception:
                    pass

            # Send forex breakout signals periodically (only if clients watch forex panel)
            if now - last_breakout >= breakout_interval:
                try:
                    with _ws_lock:
                        subs_snapshot = list(_ws_subscriptions.values())
                    has_forex_viewer = any("forex-breakout" in s for s in subs_snapshot)
                    if has_forex_viewer:
                        payload["breakout_signals"] = await asyncio.to_thread(_breakout_snapshot)
                    last_breakout = now
                except Exception:
                    pass

            # Fire due alerts (persistent store + Telegram notification)
            if now - last_alerts >= alerts_interval:
                try:
                    from mt5_mcp.alerts import check_alerts as _check_alerts
                    fired = await asyncio.to_thread(_check_alerts, _tick_quote)
                    if fired:
                        payload["alerts_fired"] = fired
                        for a in fired:
                            notify.append({
                                "type": "notify",
                                "kind": "alert_triggered",
                                "alert": {
                                    "symbol": a["symbol"],
                                    "price": a["price"],
                                    "condition": a["condition"],
                                    "name": a.get("name", ""),
                                },
                            })
                    last_alerts = now
                except Exception:
                    pass
        
        for msg in notify:
            for ws in list(_ws_clients):
                try:
                    await ws.send_json(msg)
                except Exception:
                    with _ws_lock:
                        _ws_clients.discard(ws)

        if payload is None:
            continue  # throttled or unchanged - nothing to push

        for ws in list(_ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                with _ws_lock:
                    _ws_clients.discard(ws)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    # AuthMiddleware is a BaseHTTPMiddleware and Starlette never runs it for
    # websocket scopes, so the /ws path must be checked here explicitly --
    # otherwise anyone reaching the port gets the account snapshot for free.
    # The frontend already connects as ``/ws?token=...`` (app.js).
    if WEB_AUTH_TOKEN:
        token = ws.query_params.get("token", "") or ws.headers.get("x-auth-token", "")
        if token != WEB_AUTH_TOKEN:
            await ws.close(code=1008)  # policy violation -> handshake rejected
            return
    await ws.accept()
    with _ws_lock:
        _ws_clients.add(ws)
        _ws_subscriptions[ws] = set()
    logger.info("ws client connected (%d total)", len(_ws_clients))
    # Send immediate snapshot so the UI doesn't wait for the next broadcast tick
    try:
        snapshot = await asyncio.to_thread(_summary_snapshot)
        await ws.send_json(snapshot)
    except Exception as exc:
        logger.debug("ws initial snapshot failed: %s", exc)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
            else:
                try:
                    data = json.loads(msg)
                    mtype = data.get("type", "")
                    ch = data.get("channel", "")
                    # app.js heartbeat sends {"type":"ping"} (JSON), not the
                    # bare "ping" string -- answer both or latency/heartbeat
                    # in the UI stays "--".
                    if mtype == "ping":
                        await ws.send_json({"type": "pong"})
                    elif mtype == "subscribe" and ch:
                        with _ws_lock:
                            _ws_subscriptions.setdefault(ws, set()).add(ch)
                    elif mtype == "unsubscribe" and ch:
                        with _ws_lock:
                            subs = _ws_subscriptions.get(ws, set())
                            subs.discard(ch)
                except (json.JSONDecodeError, AttributeError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)
            _ws_subscriptions.pop(ws, None)
        logger.info("ws client disconnected (%d total)", len(_ws_clients))


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="MT5 Web Dashboard")
    parser.add_argument("--port", type=int, default=None, help="Port to run on (default: WEB_PORT env or 8050)")
    parser.add_argument("--host", default=None, help="Host to bind to (default: WEB_HOST env or 127.0.0.1)")
    parser.add_argument("--terminal", default=None, help="MT5 terminal path")
    parser.add_argument("--login", type=int, default=None, help="MT5 login")
    parser.add_argument("--password", default=None, help="MT5 password")
    parser.add_argument("--server", default=None, help="MT5 server")
    args = parser.parse_args()

    if args.terminal:
        os.environ["MT5_MCP_MT5_TERMINAL_PATH"] = args.terminal
        # server.py's MT5Config reads these (legacy names):
        os.environ["MT5_PATH"] = args.terminal
    if args.login:
        os.environ["MT5_MCP_MT5_LOGIN"] = str(args.login)
        os.environ["MT5_LOGIN"] = str(args.login)
    if args.password:
        os.environ["MT5_MCP_MT5_PASSWORD"] = args.password
        os.environ["MT5_PASSWORD"] = args.password
    if args.server:
        os.environ["MT5_MCP_MT5_SERVER"] = args.server
        os.environ["MT5_SERVER"] = args.server

    # Route the shared mt5_mcp client layer (get_client) to the same terminal.
    is_deriv = bool(args.terminal and "deriv" in args.terminal.lower())
    os.environ.setdefault("MT5_MCP_ACCOUNT", "DERIV" if is_deriv else "PROP")

    # CLI flags win; otherwise fall back to the env vars read at module load
    # (WEB_PORT / WEB_HOST). Previously the hardcoded argparse defaults
    # (8050/127.0.0.1) always overrode the env vars, so launchers that route
    # instances via WEB_PORT (service.vbs deriv, docker-compose) silently
    # bound to 8050 instead.
    PORT = args.port if args.port is not None else PORT
    HOST = args.host if args.host is not None else HOST

    logger.info("MT5 Web Dashboard -> http://%s:%s", HOST, PORT)

    # Install global exception hook so uncaught errors appear in log
    import traceback as _tb
    _orig_hook = sys.excepthook
    def _log_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Uncaught exception:\n%s", "".join(_tb.format_exception(exc_type, exc_value, exc_tb)))
        _orig_hook(exc_type, exc_value, exc_tb)
    sys.excepthook = _log_exception

    uvicorn.run(app, host=HOST, port=PORT)
