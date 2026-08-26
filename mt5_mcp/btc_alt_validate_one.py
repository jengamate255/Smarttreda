"""Fast, non-zone BTCUSD strategy backtest.

This is a temporary research runner for two alternative families:

* ``trend``: completed-H4 EMA50/200 regime plus an H1 EMA20 pullback/reclaim
  confirmation.
* ``breakout``: H1 prior-N-bar Donchian breakout, optionally aligned with the
  completed-H4 EMA regime and an H1 volume filter.

Signals are calculated with pandas.  Only the trade simulation is iterative,
so this avoids the expensive per-bar supply/demand detector.  One variant is
still run per process because the MT5 Python module is not reliable when
multiple heavy backtests share an interpreter.

Prices are bid OHLC.  A BUY pays the historical next-bar spread on entry and
a SELL pays the historical exit-bar spread.  The rates ``spread`` field is in
broker points.  Commission defaults to zero because this account's symbol
metadata does not expose a commission schedule; the assumption is recorded in
each result.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.common import pip_size, pip_value_per_lot, round_to_step
from mt5_mcp.indicators import atr, ema, macd, rsi, volume


DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results" / "btc_alt_validation"


@dataclass
class AltParams:
    symbol: str = "BTCUSD"
    family: str = "trend"
    bars: int = 5000
    lookback: int = 400
    trend_timeframe: str = "H4"
    signal_timeframe: str = "H1"
    pullback: str = "ema20"
    donchian_n: int = 48
    h4_filter: bool = True
    volume_min_ratio: float = 0.0
    rsi_macd_filter: bool = False
    atr_period: int = 14
    atr_stop: float = 2.0
    rr: float = 3.0
    risk_percent: float = 0.5
    start_balance: float = 10000.0
    max_hold_bars: int = 200
    commission_per_lot_side: float = 0.0
    fallback_spread_points: float = 2000.0
    start_time: str = ""
    end_time: str = "2026-07-31T23:00:00"
    filter_name: str = "none"


@dataclass
class SimTrade:
    signal_time: str
    entry_time: str
    exit_time: str
    action: str
    entry: float
    sl: float
    tp: float
    lots: float
    atr: float
    sl_distance: float
    tp_distance: float
    rr: float
    entry_spread_points: float
    exit_spread_points: float
    exit_price: float
    pnl: float
    reason: str


def _spread_points(row: pd.Series, fallback: float) -> float:
    value = float(row.get("spread", 0.0) or 0.0)
    return value if math.isfinite(value) and value > 0 else fallback


def _metrics(trades: list[dict[str, Any]], start_balance: float) -> dict[str, Any]:
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) <= 0]
    gross_profit = sum(float(t["pnl"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl"]) for t in losses))
    equity = float(start_balance)
    peak = equity
    max_dd = 0.0
    for trade in trades:
        equity += float(trade["pnl"])
        peak = max(peak, equity)
        if peak:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    n = len(trades)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 1) if n else 0.0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(sum(float(t["pnl"]) for t in trades), 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else 0.0,
        "expectancy": round(sum(float(t["pnl"]) for t in trades) / n, 2) if n else 0.0,
        "return_pct": round((equity - start_balance) / start_balance * 100.0, 2),
        "max_drawdown_pct": round(max_dd, 2),
    }


def _load_frames(p: AltParams) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(p.symbol)
    required = (
        "digits",
        "point",
        "trade_tick_size",
        "trade_tick_value",
        "volume_min",
        "volume_step",
    )
    missing = [key for key in required if info.get(key) in (None, "")]
    if missing:
        raise RuntimeError(f"{p.symbol} symbol_info missing required fields: {missing}")

    start_cutoff = pd.Timestamp(p.start_time) if p.start_time else None
    cutoff = pd.Timestamp(p.end_time) if p.end_time else None
    # A bounded date window may be earlier than the terminal's most recent
    # ``bars`` response, so request the full cached range before slicing.
    fetch = 10000 if start_cutoff is not None else (p.bars + 500 if cutoff is not None else p.bars)
    h1 = client.rates(p.symbol, "H1", fetch)
    h4 = client.rates(p.symbol, "H4", fetch)
    if start_cutoff is not None or cutoff is not None:
        h1 = h1.loc[
            (h1["time"] >= start_cutoff if start_cutoff is not None else True)
            & (h1["time"] <= cutoff if cutoff is not None else True)
        ].tail(p.bars).reset_index(drop=True)
        # Keep H4 pre-window history for a properly warmed EMA200 context.
        h4 = h4.loc[h4["time"] <= cutoff if cutoff is not None else True].tail(p.bars).reset_index(drop=True)
    if len(h1) < 300 or len(h4) < 250:
        raise RuntimeError(f"insufficient BTCUSD data: H1={len(h1)}, H4={len(h4)}")
    return h1, h4, info


def _prepare(h1: pd.DataFrame, h4: pd.DataFrame, p: AltParams) -> pd.DataFrame:
    frame = h1.copy().sort_values("time").reset_index(drop=True)
    frame["atr"] = atr(frame, p.atr_period)
    frame["ema20"] = ema(frame["close"], 20)
    frame["ema50"] = ema(frame["close"], 50)
    frame["rsi14"] = rsi(frame["close"], 14)
    frame["macd_hist"] = macd(frame["close"])["histogram"]
    frame["volume_ratio"] = volume(frame, 20)["ratio"]

    higher = h4.copy().sort_values("time").reset_index(drop=True)
    higher["h4_ema50"] = ema(higher["close"], 50)
    higher["h4_ema200"] = ema(higher["close"], 200)
    # The row at a H4 open is replaced with the preceding completed H4 bar.
    # This prevents an H1 signal during a forming H4 candle from seeing its
    # eventual close.
    context = higher[["time", "h4_ema50", "h4_ema200"]].copy()
    context["h4_ema50"] = context["h4_ema50"].shift(1)
    context["h4_ema200"] = context["h4_ema200"].shift(1)
    frame = pd.merge_asof(frame, context, on="time", direction="backward")
    frame["h4_bull"] = frame["h4_ema50"] > frame["h4_ema200"]
    frame["h4_bear"] = frame["h4_ema50"] < frame["h4_ema200"]
    return frame


def _signals(frame: pd.DataFrame, p: AltParams) -> pd.Series:
    close = frame["close"]
    prev_close = close.shift(1)
    if p.family == "trend":
        # H1 pullback/reclaim of EMA20 with EMA50 alignment.  The previous
        # close requirement makes this a crossing/reclaim event rather than a
        # signal on every bar that merely touches the average.
        long_signal = (
            frame["h4_bull"]
            & (close > frame["ema50"])
            & (frame["low"] <= frame["ema20"])
            & (close > frame["ema20"])
            & (prev_close <= frame["ema20"].shift(1))
            & (close > frame["open"])
        )
        short_signal = (
            frame["h4_bear"]
            & (close < frame["ema50"])
            & (frame["high"] >= frame["ema20"])
            & (close < frame["ema20"])
            & (prev_close >= frame["ema20"].shift(1))
            & (close < frame["open"])
        )
        if p.rsi_macd_filter:
            long_signal &= (frame["rsi14"] > 50.0) & (frame["rsi14"] < 72.0) & (
                frame["macd_hist"] > 0.0
            )
            short_signal &= (frame["rsi14"] < 50.0) & (frame["rsi14"] > 28.0) & (
                frame["macd_hist"] < 0.0
            )
        return pd.Series(np.where(long_signal, 1, np.where(short_signal, -1, 0)), index=frame.index)

    if p.family == "breakout":
        upper = frame["high"].rolling(p.donchian_n, min_periods=p.donchian_n).max().shift(1)
        lower = frame["low"].rolling(p.donchian_n, min_periods=p.donchian_n).min().shift(1)
        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)
        long_signal = (close > upper) & (prev_close <= prev_upper)
        short_signal = (close < lower) & (prev_close >= prev_lower)
        if p.h4_filter:
            long_signal &= frame["h4_bull"]
            short_signal &= frame["h4_bear"]
        # EMA alignment is always used as a light momentum sanity check for
        # breakouts; it does not use any future data.
        long_signal &= close > frame["ema50"]
        short_signal &= close < frame["ema50"]
        if p.volume_min_ratio > 0:
            long_signal &= frame["volume_ratio"] >= p.volume_min_ratio
            short_signal &= frame["volume_ratio"] >= p.volume_min_ratio
        return pd.Series(np.where(long_signal, 1, np.where(short_signal, -1, 0)), index=frame.index)

    raise ValueError(f"unknown family {p.family}")


def _size_lots(
    equity: float,
    p: AltParams,
    sl_distance: float,
    info: dict[str, Any],
    pip: float,
    pip_value: float,
) -> float | None:
    if sl_distance <= 0 or pip <= 0 or pip_value <= 0:
        return None
    risk_amount = equity * p.risk_percent / 100.0
    sl_pips = sl_distance / pip
    raw = risk_amount / (sl_pips * pip_value)
    step = float(info.get("volume_step", 0.01) or 0.01)
    minimum = float(info.get("volume_min", 0.01) or 0.01)
    maximum = float(info.get("volume_max", 100.0) or 100.0)
    lots = round_to_step(raw, step, floor=minimum, ceil=maximum)
    return lots if lots >= minimum else None


def _simulate(
    frame: pd.DataFrame,
    signals: pd.Series,
    p: AltParams,
    info: dict[str, Any],
    start_index: int,
) -> tuple[list[dict[str, Any]], int, float]:
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    point = float(info.get("point", 0.01) or 0.01)
    per_price = pip_value / pip if pip else 0.0
    signal_indices = np.flatnonzero(signals.to_numpy() != 0)
    signal_indices = signal_indices[signal_indices >= start_index]
    equity = p.start_balance
    trades: list[dict[str, Any]] = []
    last_exit = -1
    for i in signal_indices:
        i = int(i)
        if i <= last_exit or i + 1 >= len(frame):
            continue
        atr_value = float(frame.iloc[i]["atr"])
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        direction = int(signals.iloc[i])
        action = "BUY" if direction > 0 else "SELL"
        entry_idx = i + 1
        entry_bar = frame.iloc[entry_idx]
        entry_bid = float(entry_bar["open"])
        # Use the signal bar's known spread for the next-bar execution
        # estimate.  Reading the next bar's spread would be a small form of
        # future information, even though it only affects execution cost.
        entry_spread_points = _spread_points(frame.iloc[i], p.fallback_spread_points)
        entry_spread = entry_spread_points * point
        entry = entry_bid + entry_spread if action == "BUY" else entry_bid
        sl_distance = atr_value * p.atr_stop
        tp_distance = sl_distance * p.rr
        lots = _size_lots(equity, p, sl_distance, info, pip, pip_value)
        if lots is None:
            continue
        if action == "BUY":
            sl = entry - sl_distance
            tp = entry + tp_distance
        else:
            sl = entry + sl_distance
            tp = entry - tp_distance

        exit_idx = min(len(frame) - 1, entry_idx + p.max_hold_bars - 1)
        reason = "timeout"
        exit_bid = float(frame.iloc[exit_idx]["close"])
        exit_spread_points = _spread_points(frame.iloc[exit_idx], p.fallback_spread_points)
        for j in range(entry_idx, exit_idx + 1):
            bar = frame.iloc[j]
            hit_sl = float(bar["low"]) <= sl if action == "BUY" else float(bar["high"]) >= sl
            hit_tp = float(bar["high"]) >= tp if action == "BUY" else float(bar["low"]) <= tp
            if hit_sl:
                exit_idx = j
                exit_bid = float(sl)
                exit_spread_points = _spread_points(bar, p.fallback_spread_points)
                reason = "SL"
                break
            if hit_tp:
                exit_idx = j
                exit_bid = float(tp)
                exit_spread_points = _spread_points(bar, p.fallback_spread_points)
                reason = "TP"
                break

        exit_spread = exit_spread_points * point
        if action == "BUY":
            pnl = (exit_bid - entry) * per_price * lots
        else:
            pnl = (entry - (exit_bid - exit_spread)) * per_price * lots
        pnl -= p.commission_per_lot_side * lots * 2.0
        equity += pnl
        trades.append(
            asdict(
                SimTrade(
                    signal_time=str(frame.iloc[i]["time"]),
                    entry_time=str(frame.iloc[entry_idx]["time"]),
                    exit_time=str(frame.iloc[exit_idx]["time"]),
                    action=action,
                    entry=round(entry, 6),
                    sl=round(sl, 6),
                    tp=round(tp, 6),
                    lots=round(lots, 2),
                    atr=round(atr_value, 6),
                    sl_distance=round(sl_distance, 6),
                    tp_distance=round(tp_distance, 6),
                    rr=round(p.rr, 3),
                    entry_spread_points=round(entry_spread_points, 1),
                    exit_spread_points=round(exit_spread_points, 1),
                    exit_price=round(exit_bid, 6),
                    pnl=round(pnl, 2),
                    reason=reason,
                )
            )
        )
        last_exit = exit_idx
    return trades, len(signal_indices), equity


def _split_metrics(
    trades: list[dict[str, Any]], start: str, end: str, balance: float
) -> dict[str, Any]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    split_ts = start_ts + (end_ts - start_ts) / 2
    first = [t for t in trades if pd.Timestamp(t["entry_time"]) < split_ts]
    second = [t for t in trades if pd.Timestamp(t["entry_time"]) >= split_ts]
    return {
        "split_time": str(split_ts),
        "first": {"start": start, "end": str(split_ts), **_metrics(first, balance)},
        "second": {"start": str(split_ts), "end": end, **_metrics(second, balance)},
    }


def _name(p: AltParams) -> str:
    if p.family == "trend":
        detail = f"pull{p.pullback}_{p.filter_name}"
    else:
        detail = f"donchian{p.donchian_n}_h4{int(p.h4_filter)}_vol{p.volume_min_ratio:g}"
    return (
        f"BTCUSD_{p.family}_{detail}_atr{p.atr_stop:g}_rr{p.rr:g}"
        f"_b{p.bars}_lb{p.lookback}"
    )


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fast alternative BTCUSD backtest")
    ap.add_argument("--family", choices=("trend", "breakout"), required=True)
    ap.add_argument("--atr-stop", type=float, default=2.0)
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--pullback", default="ema20")
    ap.add_argument("--donchian-n", type=int, default=48)
    ap.add_argument("--no-h4-filter", action="store_true")
    ap.add_argument("--volume-min-ratio", type=float, default=0.0)
    ap.add_argument("--rsi-macd-filter", action="store_true")
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--max-hold", type=int, default=200)
    ap.add_argument("--commission-per-lot-side", type=float, default=0.0)
    ap.add_argument("--fallback-spread-points", type=float, default=2000.0)
    ap.add_argument("--start-time", default="")
    ap.add_argument("--end-time", default="2026-07-31T23:00:00")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    p = AltParams(
        family=args.family,
        atr_stop=args.atr_stop,
        rr=args.rr,
        bars=args.bars,
        lookback=args.lookback,
        pullback=args.pullback,
        donchian_n=args.donchian_n,
        h4_filter=not args.no_h4_filter,
        volume_min_ratio=args.volume_min_ratio,
        rsi_macd_filter=args.rsi_macd_filter,
        risk_percent=args.risk,
        max_hold_bars=args.max_hold,
        commission_per_lot_side=args.commission_per_lot_side,
        fallback_spread_points=args.fallback_spread_points,
        start_time=args.start_time,
        end_time=args.end_time,
        filter_name="rsi_macd" if args.rsi_macd_filter else "none",
    )
    started = time.perf_counter()
    h1, h4, info = _load_frames(p)
    frame = _prepare(h1, h4, p)
    signals = _signals(frame, p)
    warmup = min(max(p.lookback, 250) + 10, len(frame))
    trades, signal_count, _ = _simulate(frame, signals, p, info, warmup)
    start = str(frame.iloc[warmup]["time"]) if len(frame) > warmup else str(frame.iloc[0]["time"])
    end = str(frame.iloc[-1]["time"])
    overall = _metrics(trades, p.start_balance)
    result = {
        "params": asdict(p),
        "symbol_info": {
            key: info.get(key)
            for key in (
                "name",
                "digits",
                "point",
                "trade_tick_size",
                "trade_tick_value",
                "volume_min",
                "volume_step",
                "volume_max",
                "trade_contract_size",
            )
        },
        "assumptions": {
            "ohlc": "bid",
            "spread": "historical rates.spread points at entry/exit; fallback if zero",
            "fallback_spread_points": p.fallback_spread_points,
            "commission_per_lot_side": p.commission_per_lot_side,
            "risk_percent": p.risk_percent,
            "entry": "next H1 bar open; BUY adds spread",
            "same_bar_priority": "SL before TP",
        },
        "signal_count": signal_count,
        "start": start,
        "end": end,
        **overall,
        "split": _split_metrics(trades, start, end, p.start_balance),
        "trades_detail": trades,
        "errors": [],
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_name(p)}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "family": p.family,
                "variant": _name(p),
                "signal_count": signal_count,
                "trades": overall["trades"],
                "wins": overall["wins"],
                "losses": overall["losses"],
                "win_rate": overall["win_rate"],
                "profit_factor": overall["profit_factor"],
                "net_pnl": overall["net_pnl"],
                "return_pct": overall["return_pct"],
                "max_drawdown_pct": overall["max_drawdown_pct"],
                "expectancy": overall["expectancy"],
                "start": start,
                "end": end,
                "split": result["split"],
                "result": str(path),
                "seconds": round(time.perf_counter() - started, 2),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
