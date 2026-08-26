"""Backtesting API routes (extracted from web_dashboard.py).

Self-contained FastAPI APIRouter covering:
    GET  /api/backtest/trades   - trades pre-converted for chart overlay
    GET  /api/backtests         - result browser + Monte Carlo summary
    GET  /api/backtest/run      - saved run detail
    POST /api/backtest/run      - run + save a backtest
    POST /api/backtest/forward  - windowed forward test vs full backtest
"""

from __future__ import annotations

import datetime
import glob
import json
import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException

from backtest import (
    Backtester,
    BacktestParams,
    BacktestResult,
    load_frames,
    rebuild_summary,
    save_result,
)
from monte_carlo import build_day_pool, load_trades, simulate_month

logger = logging.getLogger("web_dashboard")

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
_BT_RESULTS = str(BASE_DIR / "mt5_mcp" / "backtest" / "results")
_BT_MONTH = os.path.join(_BT_RESULTS, "month")
_BT_FORWARD = os.path.join(_BT_RESULTS, "forward")
_BT_LOCK = threading.Lock()
_BT_MC_CACHE: dict = {"ts": 0.0, "data": None}
_BT_TFS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")
_BT_MAX_BARS = 20000


def _bt_rows(out_dir: str) -> list[dict]:
    """Result summary rows for one results directory (month/forward/top-level)."""
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
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


def _ts_utc(iso: str) -> int | None:
    """Parse an ISO timestamp (naive => UTC, MT5 bars are UTC) to epoch seconds."""
    try:
        ts = pd.Timestamp(iso)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        return int(ts.timestamp())
    except (ValueError, TypeError):
        return None


@router.get("/api/backtest/trades")
def backtest_trades(name: str):
    """Backtest trades pre-converted for the chart overlay (epoch UTC timestamps)."""
    path = _bt_resolve(name)
    if not path:
        raise HTTPException(status_code=404, detail="backtest run not found")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"unreadable result: {exc}") from exc
    trades = []
    for t in data.get("trades", []):
        trades.append(
            {
                "action": t.get("action"),
                "entry_ts": _ts_utc(t["entry_time"]) if t.get("entry_time") else None,
                "exit_ts": _ts_utc(t["exit_time"]) if t.get("exit_time") else None,
                "entry": t.get("entry"),
                "sl": t.get("sl"),
                "tp": t.get("tp"),
                "exit_price": t.get("exit_price"),
                "pnl": t.get("pnl"),
                "reason": t.get("reason"),
                "rr": t.get("rr"),
            }
        )
    return {"symbol": data.get("params", {}).get("symbol"), "trades": trades}


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
    from client import get_client

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


@router.get("/api/backtests")
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


@router.get("/api/backtest/run")
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


@router.post("/api/backtest/run")
def backtest_run(payload: dict):
    """Run a backtest (optionally windowed from a 'since' date) and save it."""
    params, since = _bt_params_from(payload)
    out_dir = _BT_FORWARD if since or str(payload.get("out", "month")) == "forward" else _BT_MONTH
    started = time.perf_counter()
    result, saved = _bt_run(params, since, out_dir=out_dir)
    detail = _bt_detail(result, saved)
    detail["elapsed_s"] = round(time.perf_counter() - started, 1)
    return detail


@router.post("/api/backtest/forward")
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
