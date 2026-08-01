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
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)
sys.path.append(os.path.join(_BASE_DIR, "mt5_mcp"))
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

from backtest import (
    Backtester,
    BacktestParams,
    BacktestResult,
    load_frames,
    rebuild_summary,
    save_result,
)
from mt5_mcp.coach import coach_review

# New agents
from mt5_mcp.market_intel import market_health, scan_markets
from mt5_mcp.research import report as research_report
from mt5_mcp.research import weekly_report

sys.path.insert(0, _BASE_DIR)  # mt5_mcp modules may reorder sys.path; keep root server first
from server import MT5Config, _ema, _macd, _rsi, mt5_connection

BASE_DIR = _BASE_DIR
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.getenv("WEB_PORT", "8050"))
HOST = os.getenv("WEB_HOST", "127.0.0.1")
HEALTH_INTERVAL = int(os.getenv("WEB_HEALTH_INTERVAL", "15"))

_lock = threading.Lock()

# Backtesting (blended from the mt5_mcp backtest tooling).
_BT_RESULTS = os.path.join(BASE_DIR, "mt5_mcp", "backtest", "results")
_BT_MONTH = os.path.join(_BT_RESULTS, "month")
_BT_FORWARD = os.path.join(_BT_RESULTS, "forward")
_BT_LOCK = threading.Lock()
_BT_MC_CACHE: dict = {"ts": 0.0, "data": None}
_BT_TFS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")
_BT_MAX_BARS = 20000


def _setup_logging() -> logging.Logger:
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("web_dashboard")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "web_dashboard.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    return logger


logger = _setup_logging()

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


def _health_check_loop(stop: threading.Event):
    """Background watchdog: reconnect if the MT5 connection drops."""
    while not stop.is_set():
        try:
            if mt5_connection.is_connected():
                info = mt5.account_info()
                if info is None and not mt5_connection.reconnect():
                    logger.error("Health check: reconnect failed: %s", mt5.last_error())
            elif not mt5_connection.reconnect():
                logger.error("Health check: reconnect failed: %s", mt5.last_error())
        except Exception as exc:  # noqa: BLE001 - watchdog must never die
            logger.error("Health check error: %s", exc)
        stop.wait(HEALTH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = MT5Config()
    if not mt5_connection.connect(config):
        logger.error("Initial MT5 connect failed: %s", mt5.last_error())
    stop = threading.Event()
    threading.Thread(target=_health_check_loop, args=(stop,), daemon=True).start()
    ws_task = asyncio.create_task(_ws_broadcast_loop())
    logger.info("Dashboard started (port %s, host %s)", PORT, HOST)
    try:
        yield
    finally:
        stop.set()
        ws_task.cancel()
        mt5_connection.disconnect()
        logger.info("Dashboard stopped")


app = FastAPI(title="MT5 Web Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _require_connected():
    if not mt5_connection.is_connected():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")


def _server_now() -> datetime.datetime:
    """Current date/time anchored to the MT5 server clock (from the last tick)."""
    tick = mt5.symbol_info_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return datetime.datetime.fromtimestamp(tick.time)  # noqa: DTZ006
    return datetime.datetime.now()  # noqa: DTZ005


def _server_epoch() -> int:
    """Current time in deal-time space (server clock). MT5 deal/order times
    live in this space, so windows must be built from tick.time, not the
    local clock."""
    tick = mt5.symbol_info_tick("EURUSD")
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


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def _server_offset() -> int:
    """Seconds between the MT5 server clock and the local clock."""
    tick = mt5.symbol_info_tick("EURUSD")
    if tick is not None and tick.time > 0:
        return int(tick.time) - int(time.time())
    return 0


@app.get("/api/health")
def health():
    terminal = None
    try:
        info = mt5.terminal_info()
        if info is not None:
            terminal = {
                "company": info.company,
                "build": info.build,
                "connected": bool(info.connected),
                "name": info.name,
            }
    except Exception as exc:  # noqa: BLE001 - terminal info is best-effort
        logger.debug("terminal_info unavailable: %s", exc)
    return {
        "connected": mt5_connection.is_connected(),
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # noqa: DTZ005
        "terminal": terminal,
    }


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
                }
            )
        result.sort(key=lambda x: x["symbol"])
        return {"positions": result, "count": len(result)}


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
    data = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[tf_key], 0, count)
    if data is None or len(data) == 0:
        if not mt5_connection.is_connected():
            raise HTTPException(status_code=503, detail="MT5 terminal not connected")
        raise HTTPException(status_code=404, detail=f"no rates for {symbol}: {mt5.last_error()}")

    times = [int(r[0]) for r in data]
    opens = [r[1] for r in data]
    highs = [r[2] for r in data]
    lows = [r[3] for r in data]
    closes = [r[4] for r in data]
    volumes = [int(r[5]) for r in data]
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

        ema50 = _pad_front(_ema(close_arr, 50), n).tolist()
        ema200 = _pad_front(_ema(close_arr, 200), n).tolist()
        rsi_vals = _pad_front(_rsi(close_arr, 14), n).tolist()
        macd_line, macd_signal, macd_hist = _macd(close_arr)

        tick = mt5.symbol_info_tick(symbol)
        point = mt5.symbol_info(symbol).point if mt5.symbol_info(symbol) else 0.0

        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": n,
            "times": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi_vals,
            "macd": macd_line.tolist(),
            "macd_signal": macd_signal.tolist(),
            "macd_hist": macd_hist.tolist(),
            "tick": (
                {"bid": tick.bid, "ask": tick.ask, "spread": round((tick.ask - tick.bid) / point) if point else 0}
                if tick
                else {"bid": closes[-1], "ask": closes[-1], "spread": 0}
            ),
        }
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


@app.get("/api/news")
def news(horizon_hours: int = Query(24, ge=1, le=72)):
    """Upcoming economic events from the ForexFactory weekly calendar."""
    try:
        report = news_filter(horizon_hours=horizon_hours)
        events = [
            {
                "time": e["time"].strftime("%Y-%m-%d %H:%M"),
                "currency": e["currency"],
                "impact": e["impact"],
                "title": e["title"],
                "forecast": e["forecast"],
                "previous": e["previous"],
            }
            for e in report["events"]
        ]
        return {
            "provider": report["provider"],
            "events": events,
            "high_impact_count": report["high_impact_count"],
            "high_impact_imminent": report["high_impact_imminent"],
            "trading_blocked": report["trading_blocked"],
            "imminent_events": [
                {"currency": e["currency"], "title": e["title"]}
                for e in report["imminent_events"]
            ],
        }
    except Exception as exc:  # noqa: BLE001 - news is best-effort
        logger.warning("news endpoint failed: %s", exc)
        return {"events": [], "high_impact_imminent": False, "trading_blocked": False, "error": str(exc)}


@app.get("/api/risk")
def risk():
    """Daily/weekly loss usage, open risk and exposure from the risk manager."""
    with _lock:
        _require_connected()
    try:
        return risk_report()
    except Exception as exc:  # noqa: BLE001 - risk is best-effort
        logger.warning("risk endpoint failed: %s", exc)
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


# ---------- Research Agent ----------

@app.get("/api/research-report")
def api_research_report(days: int = 7, min_trades: int = 1, save: bool = True):
    """Periodic research report: stats + data-driven suggestions over the last N days."""
    try:
        return research_report(days=days, min_trades=min_trades, save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research report failed: %s", exc)
        return {"error": str(exc)}


@app.get("/api/weekly-research-report")
def api_weekly_research_report(save: bool = True):
    """Standard weekly report (last 7 days), saved to memory/reports/."""
    try:
        return weekly_report(save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly research report failed: %s", exc)
        return {"error": str(exc)}


# ---------- AI Coach ----------

@app.get("/api/coach-review/latest")
def coach_review_latest():
    """Full post-trade review for the most recent closed trade."""
    try:
        return coach_review(None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review latest failed: %s", exc)
        return {"error": str(exc)}


@app.get("/api/coach-review/{ticket}")
def coach_review_by_ticket(ticket: int):
    """Full post-trade review for a specific closed trade ticket."""
    try:
        return coach_review(ticket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review failed for %s: %s", ticket, exc)
        return {"error": str(exc)}
        logger.warning("coach review latest failed: %s", exc)
        return {"error": str(exc)}


@app.get("/api/sessions")
def sessions():
    """Best sessions/setups/pairs from the trade journal."""
    try:
        return {
            "sessions": best_sessions(),
            "setups": best_setups(),
            "pairs": best_pairs(),
        }
    except Exception as exc:  # noqa: BLE001 - learning is best-effort
        logger.warning("sessions endpoint failed: %s", exc)
        return {"sessions": [], "setups": [], "pairs": [], "error": str(exc)}


@app.get("/api/quote")
def quote(symbol: str = "EURUSD"):
    """Current bid/ask/spread for the chart header and live-candle updates."""
    with _lock:
        _require_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise HTTPException(status_code=404, detail=f"no tick for {symbol}")
        info = mt5.symbol_info(symbol)
        point = info.point if info else 0.0
        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": round((tick.ask - tick.bid) / point) if point else 0,
            "time": tick.time,
        }


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


# ---------------- Backtesting (blended from the mt5_mcp backtest tooling) ----------------

def _bt_rows(out_dir: str) -> list[dict]:
    """Result summary rows for one results directory (month/forward/top-level)."""
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        p = data.get("params", {}) or {}
        rows.append({
            "dir": os.path.basename(out_dir.rstrip("/\\")),
            "file": os.path.basename(path),
            "symbol": p.get("symbol", ""),
            "trend_tf": p.get("trend_timeframe", ""),
            "zone_tfs": "+".join(p.get("zone_timeframes", []) or []),
            "min_zone_score": p.get("min_zone_score", 0),
            "min_rr": p.get("min_rr", 0),
            "tp_min": p.get("tp_min", 0),
            "tp_max": p.get("tp_max", 0),
            "trades": data.get("n_trades", 0),
            "win_rate": data.get("win_rate", 0),
            "profit_factor": data.get("profit_factor", 0),
            "net_pnl": data.get("net_pnl", 0),
            "total_return_pct": data.get("total_return_pct", 0),
            "max_drawdown_pct": data.get("max_drawdown_pct", 0),
            "expectancy": data.get("expectancy", 0),
            "avg_rr": data.get("avg_rr", 0),
            "start": data.get("start", ""),
            "end": data.get("end", ""),
        })
    rows.sort(key=lambda r: float(r.get("net_pnl") or 0), reverse=True)
    return rows


def _bt_resolve(name: str) -> str | None:
    """Resolve a relative result name, refusing path traversal."""
    if not name or not name.endswith(".json"):
        return None
    root = os.path.realpath(_BT_RESULTS)
    path = os.path.realpath(os.path.join(root, name))
    if path == root or not path.startswith(root + os.sep):
        return None
    return path if os.path.isfile(path) else None


def _bt_equity_points(result: BacktestResult) -> list[dict]:
    equity = float(result.params.get("start_balance", 10000.0))
    points = [{"label": "start", "value": round(equity, 2)}]
    for t in result.trades:
        equity += float(t.get("pnl", 0.0))
        points.append({"label": str(t.get("exit_time", ""))[:10], "value": round(equity, 2)})
    return points


def _bt_detail(result: BacktestResult, name: str | None = None) -> dict:
    data = asdict(result)
    data["name"] = name or ""
    data["saved"] = name or ""
    data["equity"] = _bt_equity_points(result)
    data["trades"] = data["trades"][-1000:]
    return data


def _bt_monte_carlo() -> dict | None:
    now = time.perf_counter()
    if _BT_MC_CACHE["data"] is not None and now - _BT_MC_CACHE["ts"] < 300:
        return _BT_MC_CACHE["data"]
    try:
        trades = load_trades(_BT_MONTH)
        if not trades:
            _BT_MC_CACHE.update(ts=now, data=None)
            return None
        day_pool, month_weekdays = build_day_pool(trades)
        stats = simulate_month(day_pool, month_weekdays, sims=3000)
        stats["n_trades"] = len(trades)
        _BT_MC_CACHE.update(ts=now, data=stats)
        return stats
    except Exception as exc:  # noqa: BLE001
        logger.error("backtest Monte Carlo failed: %s", exc)
        _BT_MC_CACHE.update(ts=now, data=None)
        return None


def _bt_params_from(payload: dict) -> tuple[BacktestParams, str | None]:
    payload = payload or {}
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    if len(symbol) > 12:
        raise HTTPException(status_code=400, detail="invalid symbol")
    since = str(payload.get("since", "")).strip() or None
    if since:
        try:
            datetime.date.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="since must be YYYY-MM-DD") from exc

    def tf(name: str, default: str) -> str:
        value = str(payload.get(name) or default).upper()
        if value not in _BT_TFS:
            raise HTTPException(status_code=400, detail=f"invalid {name}")
        return value

    trend = tf("trend_tf", "H4")
    confirm = tf("confirm_tf", "H1")
    zone_tfs = [z for z in (z.strip().upper() for z in str(payload.get("zone_tfs") or "H1,H4").split(",")) if z in _BT_TFS]
    if not zone_tfs:
        raise HTTPException(status_code=400, detail="zone_tfs required (e.g. H1,H4)")
    try:
        bars = int(payload.get("bars") or 1500)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid bars") from exc
    if not (200 <= bars <= _BT_MAX_BARS):
        raise HTTPException(status_code=400, detail="bars must be 200..20000")

    def number(name: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(payload.get(name, default))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid {name}") from exc
        if not (lo <= v <= hi):
            raise HTTPException(status_code=400, detail=f"{name} out of range")
        return v

    params = BacktestParams(
        symbol=symbol,
        trend_timeframe=trend,
        zone_timeframes=zone_tfs,
        confirmation_timeframe=confirm,
        min_zone_score=number("min_zone_score", 40.0, 0.0, 100.0),
        min_rr=number("min_rr", 3.0, 0.5, 10.0),
        tp_min=number("tp_min", 80.0, 1.0, 1000.0),
        tp_max=number("tp_max", 100.0, 1.0, 1000.0),
        sl_min=number("sl_min", 20.0, 1.0, 500.0),
        risk_percent=number("risk", 0.5, 0.01, 10.0),
        start_balance=number("start_balance", 10000.0, 100.0, 1e9),
        bars=bars,
    )
    if params.tp_max < params.tp_min:
        raise HTTPException(status_code=400, detail="tp_max must be >= tp_min")
    return params, since


def _bt_run(
    params: BacktestParams, since: str | None, out_dir: str | None = None
) -> tuple[BacktestResult, str | None]:
    """Fetch frames (optionally sliced to a forward window), run and save."""
    with _BT_LOCK:
        tfs = sorted({params.trend_timeframe, params.confirmation_timeframe, *params.zone_timeframes})
        frames = load_frames(params.symbol, tfs, params.bars)
        if since:
            since_ts = pd.Timestamp(since)
            sliced: dict[str, pd.DataFrame] = {}
            for tf_name, frame in frames.items():
                col = frame["time"]
                if col.dt.tz is not None:
                    col = col.dt.tz_localize(None)
                frame = frame.assign(time=col)
                sliced[tf_name] = frame[frame["time"] >= since_ts]
            frames = sliced
        info: dict = {}
        try:
            info = get_client().symbol_info(params.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest symbol_info failed: %s", exc)
        result = Backtester(params, frames, info).run()
        saved: str | None = None
        if not result.errors or result.n_trades:
            out = Path(out_dir or _BT_MONTH)
            try:
                path = save_result(result, out)
                rebuild_summary(out)
                dir_name = os.path.basename(str(out).rstrip("/\\"))
                saved = dir_name + "/" + os.path.basename(str(path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("backtest save failed: %s", exc)
        return result, saved


@app.get("/api/backtests")
def backtests():
    """Backtest result browser: month/forward/all groups + Monte Carlo for month."""
    month = _bt_rows(_BT_MONTH)
    forward = _bt_rows(_BT_FORWARD)
    top = _bt_rows(_BT_RESULTS)
    seen: set = set()
    all_rows: list[dict] = []
    for group in (month, forward, top):
        for r in group:
            key = (r["file"], r["dir"])
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(r)
    return {
        "groups": {"month": month, "forward": forward, "all": all_rows},
        "mc": _bt_monte_carlo(),
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@app.get("/api/backtest/run")
def backtest_detail(name: str):
    """Full detail (stats, trades, equity points) of a saved backtest result."""
    path = _bt_resolve(name)
    if not path:
        raise HTTPException(status_code=404, detail="backtest run not found")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        result = BacktestResult(**data)
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"unreadable result: {exc}") from exc
    return _bt_detail(result, name)


@app.post("/api/backtest/run")
def backtest_run(payload: dict):
    """Run a backtest (optionally windowed from a 'since' date) and save it."""
    params, since = _bt_params_from(payload)
    out_dir = _BT_FORWARD if since or str(payload.get("out", "month")) == "forward" else _BT_MONTH
    started = time.perf_counter()
    result, saved = _bt_run(params, since, out_dir=out_dir)
    detail = _bt_detail(result, saved)
    detail["elapsed_s"] = round(time.perf_counter() - started, 1)
    return detail


@app.post("/api/backtest/forward")
def backtest_forward(payload: dict):
    """Forward test: windowed (out-of-sample) run + full backtest comparison."""
    params, since = _bt_params_from(payload)
    if not since:
        raise HTTPException(status_code=400, detail="since (YYYY-MM-DD) required for forward test")
    started = time.perf_counter()
    full, full_saved = _bt_run(params, None, out_dir=_BT_FORWARD)
    windowed, w_saved = _bt_run(params, since, out_dir=_BT_FORWARD)
    return {
        "windowed": _bt_detail(windowed, w_saved),
        "full": _bt_detail(full, full_saved),
        "elapsed_s": round(time.perf_counter() - started, 1),
    }


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
_ws_lock = threading.Lock()
WS_INTERVAL = 5

# Notification state shared with the broadcast loop.
_ws_state: dict = {"positions": set(), "plan_key": None, "plan_action": None}
_active_plan_key: dict = {"key": None}


def _summary_snapshot() -> dict:
    """Quick account + positions snapshot for WebSocket push. Not locked:
    runs in a worker thread while other requests are served."""
    if not mt5_connection.is_connected():
        return {"type": "summary", "connected": False}
    try:
        with _lock:
            info = mt5.account_info()
            if info is None:
                return {"type": "summary", "connected": False}
            positions = mt5.positions_get() or []
            deals = mt5.history_deals_get(_server_midnight_epoch(), _server_epoch()) or []
            realized = round(sum(d.profit + d.swap + d.fee for d in deals), 2)
            floating = round(sum(p.profit + p.swap for p in positions), 2)
            return {
                "type": "summary",
                "connected": True,
                "account": {
                    "login": info.login,
                    "balance": info.balance,
                    "equity": info.equity,
                    "free_margin": info.margin_free,
                    "margin_level": info.margin_level,
                    "currency": info.currency,
                    "floating_pnl": floating,
                    "daily_realized": realized,
                    "daily_pnl": round(realized + floating, 2),
                    "daily_trades": sum(
                        1 for d in deals if d.entry == CLOSED_ENTRY
                    ),
                    "server_time": _server_now().strftime("%H:%M:%S"),
                },
                "positions": [
                    {
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                        "volume": p.volume,
                        "price_open": p.price_open,
                        "sl": p.sl,
                        "tp": p.tp,
                        "price_current": p.price_current,
                        "total": round(p.profit + p.swap, 2),
                    }
                    for p in positions
                ],
            }
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        logger.warning("ws snapshot failed: %s", exc)
        return {"type": "summary", "connected": False}


async def _ws_broadcast_loop():
    while True:
        await asyncio.sleep(WS_INTERVAL)
        if not _ws_clients:
            continue
        payload = await asyncio.to_thread(_summary_snapshot)

        notify: list[dict] = []
        if payload.get("connected"):
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
                        notify.append(
                            {
                                "type": "notify",
                                "kind": "plan_changed",
                                "symbol": key[0],
                                "action": action,
                            }
                        )
                        _ws_state["plan_action"] = action

        for msg in notify:
            for ws in list(_ws_clients):
                try:
                    await ws.send_json(msg)
                except Exception:  # noqa: BLE001
                    with _ws_lock:
                        _ws_clients.discard(ws)

        for ws in list(_ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                with _ws_lock:
                    _ws_clients.discard(ws)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    with _ws_lock:
        _ws_clients.add(ws)
    logger.info("ws client connected (%d total)", len(_ws_clients))
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)
        logger.info("ws client disconnected (%d total)", len(_ws_clients))


if __name__ == "__main__":
    import uvicorn

    print(f"MT5 Web Dashboard -> http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
