"""Fast higher-timeframe BTCUSD Donchian/regime backtest.

This is a temporary research runner.  It uses only pandas/numpy indicators on
H4 candles and does not call the supply/demand detector.  It runs one variant
per process and keeps the core strategy/backtest code untouched.

Signal model:
    * breakout of the prior N completed H4 bars;
    * direction filtered by completed H4 or D1 EMA50/EMA200 regime;
    * next H4 bar entry;
    * ATR stop and fixed R-multiple target.

Historical candle spread is charged at entry/exit.  Commission is configurable
because the BTCUSD symbol metadata does not expose a commission schedule.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.common import pip_size, pip_value_per_lot, round_to_step
from mt5_mcp.indicators import atr, ema, volume


DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results" / "btc_htf_validation"


@dataclass
class HTFParams:
    symbol: str = "BTCUSD"
    regime_tf: str = "D1"
    donchian_n: int = 24
    atr_period: int = 14
    atr_stop: float = 2.0
    rr: float = 3.0
    volume_min_ratio: float = 0.0
    atr_min_percentile: float = 0.0
    risk_percent: float = 0.5
    start_balance: float = 10000.0
    max_hold_bars: int = 100
    warmup_bars: int = 250
    commission_per_lot_side: float = 0.0
    fallback_spread_points: float = 2000.0
    end_time: str = "2026-07-31T23:00:00"


def _spread(row: pd.Series, fallback: float) -> float:
    value = float(row.get("spread", 0.0) or 0.0)
    return value if math.isfinite(value) and value > 0 else fallback


def _metrics(trades: list[dict[str, Any]], balance: float) -> dict[str, Any]:
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) <= 0]
    gp = sum(float(t["pnl"]) for t in wins)
    gl = abs(sum(float(t["pnl"]) for t in losses))
    equity = balance
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
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "net_pnl": round(sum(float(t["pnl"]) for t in trades), 2),
        "profit_factor": round(gp / gl, 3) if gl else 0.0,
        "expectancy": round(sum(float(t["pnl"]) for t in trades) / n, 2) if n else 0.0,
        "return_pct": round((equity - balance) / balance * 100.0, 2),
        "max_drawdown_pct": round(max_dd, 2),
    }


def _load(p: HTFParams) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
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

    cutoff = pd.Timestamp(p.end_time) if p.end_time else None
    h4 = client.rates(p.symbol, "H4", 10000)
    d1 = client.rates(p.symbol, "D1", 10000)
    if cutoff is not None:
        h4 = h4.loc[h4["time"] <= cutoff].reset_index(drop=True)
        d1 = d1.loc[d1["time"] <= cutoff].reset_index(drop=True)
    if len(h4) < 300 or len(d1) < 250:
        raise RuntimeError(f"insufficient BTCUSD data: H4={len(h4)}, D1={len(d1)}")
    return h4, d1, info


def _prepare(h4: pd.DataFrame, d1: pd.DataFrame, p: HTFParams) -> pd.DataFrame:
    frame = h4.sort_values("time").reset_index(drop=True).copy()
    frame["atr"] = atr(frame, p.atr_period)
    frame["volume_ratio"] = volume(frame, 20)["ratio"]
    if p.atr_min_percentile > 0:
        # Quantile threshold uses only prior/current ATR observations.
        threshold = frame["atr"].rolling(250, min_periods=100).quantile(
            p.atr_min_percentile
        )
        frame["atr_filter"] = frame["atr"] >= threshold
    else:
        frame["atr_filter"] = True

    frame["h4_ema50"] = ema(frame["close"], 50).shift(1)
    frame["h4_ema200"] = ema(frame["close"], 200).shift(1)

    daily = d1.sort_values("time").reset_index(drop=True).copy()
    daily["d1_ema50"] = ema(daily["close"], 50).shift(1)
    daily["d1_ema200"] = ema(daily["close"], 200).shift(1)
    context = daily[["time", "d1_ema50", "d1_ema200"]]
    # At every H4 bar, the merged D1 values belong to the preceding completed
    # daily candle, not the still-forming current day.
    frame = pd.merge_asof(frame, context, on="time", direction="backward")
    if p.regime_tf.upper() == "D1":
        frame["regime_bull"] = frame["d1_ema50"] > frame["d1_ema200"]
        frame["regime_bear"] = frame["d1_ema50"] < frame["d1_ema200"]
    else:
        frame["regime_bull"] = frame["h4_ema50"] > frame["h4_ema200"]
        frame["regime_bear"] = frame["h4_ema50"] < frame["h4_ema200"]
    return frame


def _signals(frame: pd.DataFrame, p: HTFParams) -> pd.Series:
    upper = frame["high"].rolling(p.donchian_n, min_periods=p.donchian_n).max().shift(1)
    lower = frame["low"].rolling(p.donchian_n, min_periods=p.donchian_n).min().shift(1)
    prev_close = frame["close"].shift(1)
    long_signal = (frame["close"] > upper) & (prev_close <= upper.shift(1))
    short_signal = (frame["close"] < lower) & (prev_close >= lower.shift(1))
    long_signal &= frame["regime_bull"] & frame["atr_filter"]
    short_signal &= frame["regime_bear"] & frame["atr_filter"]
    if p.volume_min_ratio > 0:
        long_signal &= frame["volume_ratio"] >= p.volume_min_ratio
        short_signal &= frame["volume_ratio"] >= p.volume_min_ratio
    return pd.Series(
        np.where(long_signal, 1, np.where(short_signal, -1, 0)), index=frame.index
    )


def _lots(
    equity: float,
    p: HTFParams,
    sl_distance: float,
    info: dict[str, Any],
    pip: float,
    pip_value: float,
) -> float | None:
    if sl_distance <= 0 or pip <= 0 or pip_value <= 0:
        return None
    risk_amount = equity * p.risk_percent / 100.0
    raw = risk_amount / ((sl_distance / pip) * pip_value)
    step = float(info.get("volume_step", 0.01) or 0.01)
    minimum = float(info.get("volume_min", 0.01) or 0.01)
    maximum = float(info.get("volume_max", 100.0) or 100.0)
    result = round_to_step(raw, step, floor=minimum, ceil=maximum)
    return result if result >= minimum else None


def _simulate(
    frame: pd.DataFrame,
    signals: pd.Series,
    p: HTFParams,
    info: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    point = float(info.get("point", 0.01) or 0.01)
    per_price = pip_value / pip if pip else 0.0
    signal_indices = np.flatnonzero(signals.to_numpy() != 0)
    signal_indices = signal_indices[signal_indices >= p.warmup_bars]
    equity = p.start_balance
    last_exit = -1
    trades: list[dict[str, Any]] = []

    for i_raw in signal_indices:
        i = int(i_raw)
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
        # Signal-bar spread avoids using an unknown next-bar spread.
        entry_spread_points = _spread(frame.iloc[i], p.fallback_spread_points)
        entry = entry_bid + entry_spread_points * point if action == "BUY" else entry_bid
        sl_distance = atr_value * p.atr_stop
        tp_distance = sl_distance * p.rr
        lots = _lots(equity, p, sl_distance, info, pip, pip_value)
        if lots is None:
            continue
        sl = entry - sl_distance if action == "BUY" else entry + sl_distance
        tp = entry + tp_distance if action == "BUY" else entry - tp_distance

        exit_idx = min(len(frame) - 1, entry_idx + p.max_hold_bars - 1)
        exit_bid = float(frame.iloc[exit_idx]["close"])
        exit_spread_points = _spread(frame.iloc[exit_idx], p.fallback_spread_points)
        reason = "timeout"
        for j in range(entry_idx, exit_idx + 1):
            bar = frame.iloc[j]
            hit_sl = float(bar["low"]) <= sl if action == "BUY" else float(bar["high"]) >= sl
            hit_tp = float(bar["high"]) >= tp if action == "BUY" else float(bar["low"]) <= tp
            if hit_sl:
                exit_idx = j
                exit_bid = float(sl)
                exit_spread_points = _spread(bar, p.fallback_spread_points)
                reason = "SL"
                break
            if hit_tp:
                exit_idx = j
                exit_bid = float(tp)
                exit_spread_points = _spread(bar, p.fallback_spread_points)
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
            {
                "signal_time": str(frame.iloc[i]["time"]),
                "entry_time": str(frame.iloc[entry_idx]["time"]),
                "exit_time": str(frame.iloc[exit_idx]["time"]),
                "action": action,
                "entry": round(entry, 6),
                "sl": round(sl, 6),
                "tp": round(tp, 6),
                "lots": round(lots, 2),
                "atr": round(atr_value, 6),
                "sl_distance": round(sl_distance, 6),
                "tp_distance": round(tp_distance, 6),
                "rr": p.rr,
                "entry_spread_points": round(entry_spread_points, 1),
                "exit_spread_points": round(exit_spread_points, 1),
                "exit_price": round(exit_bid, 6),
                "pnl": round(pnl, 2),
                "reason": reason,
            }
        )
        last_exit = exit_idx
    return trades, len(signal_indices)


def _periods(trades: list[dict[str, Any]], start: str, end: str, balance: float) -> dict[str, Any]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    one = start_ts + (end_ts - start_ts) / 3
    two = start_ts + (end_ts - start_ts) * 2 / 3
    groups = {
        "early": (start_ts, one),
        "middle": (one, two),
        "late": (two, end_ts),
    }
    result: dict[str, Any] = {}
    for name, (lo, hi) in groups.items():
        subset = [
            t
            for t in trades
            if lo <= pd.Timestamp(t["entry_time"]) < hi
        ]
        result[name] = {
            "start": str(lo),
            "end": str(hi),
            **_metrics(subset, balance),
        }
    result["boundary_1"] = str(one)
    result["boundary_2"] = str(two)
    return result


def _name(p: HTFParams) -> str:
    return (
        f"BTCUSD_h4break_{p.regime_tf}_n{p.donchian_n}"
        f"_atr{p.atr_stop:g}_rr{p.rr:g}"
        f"_vol{p.volume_min_ratio:g}_ap{p.atr_min_percentile:g}"
        f"_hold{p.max_hold_bars}"
    )


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Fast H4 BTCUSD breakout/regime backtest")
    ap.add_argument("--regime-tf", choices=("H4", "D1"), default="D1")
    ap.add_argument("--donchian-n", type=int, required=True)
    ap.add_argument("--atr-stop", type=float, required=True)
    ap.add_argument("--rr", type=float, required=True)
    ap.add_argument("--volume-min-ratio", type=float, default=0.0)
    ap.add_argument("--atr-min-percentile", type=float, default=0.0)
    ap.add_argument("--max-hold", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=250)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--commission-per-lot-side", type=float, default=0.0)
    ap.add_argument("--fallback-spread-points", type=float, default=2000.0)
    ap.add_argument("--end-time", default="2026-07-31T23:00:00")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    p = HTFParams(
        regime_tf=args.regime_tf,
        donchian_n=args.donchian_n,
        atr_stop=args.atr_stop,
        rr=args.rr,
        volume_min_ratio=args.volume_min_ratio,
        atr_min_percentile=args.atr_min_percentile,
        max_hold_bars=args.max_hold,
        warmup_bars=args.warmup,
        risk_percent=args.risk,
        commission_per_lot_side=args.commission_per_lot_side,
        fallback_spread_points=args.fallback_spread_points,
        end_time=args.end_time,
    )
    started = time.perf_counter()
    h4, d1, info = _load(p)
    frame = _prepare(h4, d1, p)
    signals = _signals(frame, p)
    trades, signal_count = _simulate(frame, signals, p, info)
    start = str(frame.iloc[min(p.warmup_bars, len(frame) - 1)]["time"])
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
            "spread": "historical H4 rates.spread points at entry/exit",
            "fallback_spread_points": p.fallback_spread_points,
            "commission_per_lot_side": p.commission_per_lot_side,
            "entry": "next H4 bar open; BUY adds signal-bar spread",
            "same_bar_priority": "SL before TP",
        },
        "signal_count": signal_count,
        "start": start,
        "end": end,
        **overall,
        "periods": _periods(trades, start, end, p.start_balance),
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
                "periods": result["periods"],
                "result": str(path),
                "seconds": round(time.perf_counter() - started, 2),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
