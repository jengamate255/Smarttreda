"""Event-driven backtesting engine.

Iterates bars in time order. For each bar:
  1. Process open positions against the bar's high/low (SL before TP when both
     are touched in the same bar — conservative).
  2. Evaluate new signals from the strategy; apply spread/commission/slippage
     at entry; enforce max simultaneous trades and per-symbol cooldown.

A signal is a dict: {time, side: BUY|SELL, sl, tp} with sl/tp as absolute
prices. PnL is expressed per unit of position; risk-normalized R multiples are
recorded for every trade (position sizing is handled by the risk engine, not
here).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.utils import pip_size

Signal = dict


@dataclass
class TradeRecord:
    time: pd.Timestamp
    side: str
    entry: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str  # TP | SL | EOF
    sl: float
    tp: float
    pnl: float
    pnl_r: float
    bars_held: int
    spread_pips: float
    commission_pips: float
    slippage_pips: float
    symbol: str = ""
    timeframe: str = ""


@dataclass
class EngineResult:
    trades: list[TradeRecord]
    equity_curve: pd.DataFrame
    signals_generated: int = 0


def run_backtest(
    df: pd.DataFrame,
    strategy: Callable[[pd.DataFrame], list[Signal]],
    cfg: dict | None = None,
    symbol: str = "",
    timeframe: str = "",
) -> EngineResult:
    cfg = cfg or {}
    max_trades = int(cfg.get("max_simultaneous_trades", 1))
    cooldown = int(cfg.get("cooldown_bars", 5))
    commission_pips = float(cfg.get("commission_pips", 0.0))
    slippage_pips = float(cfg.get("slippage_pips", 0.0))
    spread_mode = str(cfg.get("spread_mode", "signal")).lower()
    session_whitelist = list(cfg.get("session_whitelist", []) or [])
    session_ranges = dict(cfg.get("sessions", {}) or {})
    start_equity = float(cfg.get("start_equity", 100_000.0))
    ps = pip_size(symbol) if symbol else 0.0001

    frame = df.reset_index(drop=True)
    times = pd.to_datetime(frame["time"], utc=True)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    n = len(frame)
    if "atr" in frame.columns:
        atr = frame["atr"].to_numpy(dtype=float)
    else:
        tr = pd.concat(
            [frame["high"] - frame["low"],
             (frame["high"] - frame["close"].shift(1)).abs(),
             (frame["low"] - frame["close"].shift(1)).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().fillna(
            frame["high"] - frame["low"]
        ).to_numpy(dtype=float)

    signals_by_bar: dict[int, list[Signal]] = {}
    times_ns = times.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    for sig in strategy(frame):
        t = pd.Timestamp(sig["time"])
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        sig_ns = int(t.tz_convert("UTC").value)
        match = np.where(times_ns >= sig_ns)[0]
        if len(match) == 0:
            continue
        signals_by_bar.setdefault(int(match[0]), []).append(sig)

    positions: list[dict] = []
    trades: list[TradeRecord] = []
    last_close_bar: dict[str, int] = {}
    closed_pnl = 0.0
    equity_curve = []
    signals_generated = sum(len(v) for v in signals_by_bar.values())

    def open_pnl(pos: dict, price: float) -> float:
        return (price - pos["entry"]) * pos["dir"]

    for i in range(n):
        t = times.iloc[i]
        h, l, c = high[i], low[i], close[i]

        for pos in list(positions):
            if pos["side"] == "BUY":
                hit_tp = h >= pos["tp"]
                hit_sl = l <= pos["sl"]
            else:
                hit_tp = l <= pos["tp"]
                hit_sl = h >= pos["sl"]
            if not (hit_tp or hit_sl):
                continue
            if hit_sl and hit_tp:
                exit_price = pos["sl"]
                reason = "SL"
            elif hit_tp:
                exit_price = pos["tp"]
                reason = "TP"
            else:
                exit_price = pos["sl"]
                reason = "SL"

            pnl = (exit_price - pos["entry"]) * pos["dir"]
            closed_pnl += pnl
            positions.remove(pos)
            trades.append(TradeRecord(
                time=pos["time"], side=pos["side"], entry=pos["entry"],
                exit_time=t, exit_price=exit_price, exit_reason=reason,
                sl=pos["sl"], tp=pos["tp"], pnl=pnl,
                pnl_r=pnl / pos["risk"] if pos["risk"] else 0.0,
                bars_held=i - pos["bar"], spread_pips=pos["spread_pips"],
                commission_pips=pos["commission_pips"], slippage_pips=pos["slippage_pips"],
                symbol=symbol, timeframe=timeframe,
            ))
            last_close_bar[pos["side"]] = i

        for sig in signals_by_bar.get(i, []):
            if len(positions) >= max_trades:
                continue
            side = str(sig["side"]).upper()
            if side not in ("BUY", "SELL"):
                continue
            if session_whitelist:
                hour = int(t.hour)
                allowed = False
                for name in session_whitelist:
                    rng = session_ranges.get(name)
                    if rng is not None and len(rng) == 2 and int(rng[0]) <= hour < int(rng[1]):
                        allowed = True
                        break
                if not allowed:
                    continue
            if i - last_close_bar.get(side, -10**6) < cooldown:
                continue
            dir_ = 1.0 if side == "BUY" else -1.0
            if spread_mode == "historical" and "spread" in frame.columns:
                spread_p = float(frame["spread"].iloc[i])
            else:
                spread_p = float(sig.get("spread_pips", 0.0))
            cost_price = (spread_p + commission_pips + slippage_pips) * ps
            entry = c + dir_ * cost_price
            sl = sig.get("sl")
            tp = sig.get("tp")
            if sl is None or tp is None:
                # Default ATR-based stops (1.5 ATR stop, 3:1 TP) for baselines
                # that do not specify them.
                a = float(atr[i])
                sl = entry - dir_ * 1.5 * a
                tp = entry + dir_ * 4.5 * a
            sl = float(sl)
            tp = float(tp)
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            positions.append({
                "time": t, "bar": i, "side": side, "entry": entry, "sl": sl, "tp": tp,
                "dir": dir_, "risk": risk, "spread_pips": spread_p,
                "commission_pips": commission_pips, "slippage_pips": slippage_pips,
            })

        unrealized = sum(open_pnl(p, c) for p in positions)
        equity_curve.append({"time": t, "equity": start_equity + closed_pnl + unrealized})

    for pos in list(positions):
        pnl = (close[n - 1] - pos["entry"]) * pos["dir"]
        closed_pnl += pnl
        trades.append(TradeRecord(
            time=pos["time"], side=pos["side"], entry=pos["entry"],
            exit_time=times.iloc[n - 1], exit_price=close[n - 1], exit_reason="EOF",
            sl=pos["sl"], tp=pos["tp"], pnl=pnl,
            pnl_r=pnl / pos["risk"] if pos["risk"] else 0.0,
            bars_held=n - 1 - pos["bar"], spread_pips=pos["spread_pips"],
            commission_pips=pos["commission_pips"], slippage_pips=pos["slippage_pips"],
            symbol=symbol, timeframe=timeframe,
        ))

    curve = pd.DataFrame(equity_curve)
    return EngineResult(trades=trades, equity_curve=curve, signals_generated=signals_generated)
