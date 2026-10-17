"""Backtest for Boom/Crash Spike + Drift Strategy.

Data: MT5 terminal M1 history (newest-anchored), Deriv API as fallback.
Signals mirror the live ``detect_spike`` logic: spike window of 30 M1 bars,
entry at the 38.2%/50% Fibonacci retracement, SL beyond the spike extreme
plus an ATR buffer.  Exits use the chandelier trailing stop (arm at +1R,
trail at 1.0 ATR behind the extreme, 50% partial at +1R) matching the live
watcher — no fixed TP.

Execution mirrors the live watcher: a BUY/SELL STOP is left at the entry
level and only fills when price touches it (within a deadline); after the
fill the trade runs to SL, chandelier trail exit, or time exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.indicators import atr
from mt5_mcp.tools.boom_crash_strategy import mt5_symbol


@dataclass
class Trade:
    symbol: str
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp_price: float
    direction: str  # "long" / "short"
    risk_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "tp", "sl", "time"
    pnl_price: float = 0.0
    spike_age: int = 0
    match_retracement: float = 0.0

    @property
    def r_multiple(self) -> float:
        if not self.risk_price:
            return 0.0
        return self.pnl_price / self.risk_price


def _normalize(data: Any) -> pd.DataFrame:
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    rename = {"time": "timestamp", "tick_volume": "volume"}
    frame = frame.rename(columns=rename)
    if "timestamp" not in frame.columns or "open" not in frame.columns:
        raise ValueError("unexpected candle frame shape")
    col = frame["timestamp"]
    if col.dtype.kind in "iuf":  # epoch seconds given as raw numbers
        ts = pd.to_datetime(col, unit="s", utc=True)
    else:  # datetime64 at whatever resolution (ns/ms/us/s)
        ts = pd.to_datetime(col, utc=True)
    # Pandas/MT5 can return datetime64[s], datetime64[ms], or datetime64[ns].
    # Timestamp.timestamp() avoids treating the stored resolution as ns.
    frame["timestamp"] = ts.map(lambda value: int(value.timestamp()))
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype(float)
    return frame.reset_index(drop=True)


def get_historical_mt5(symbol: str, count: int) -> pd.DataFrame | None:
    """Newest ``count`` M1 bars from the MT5 terminal (or None on failure)."""
    try:
        from mt5_mcp.client import get_client

        client = get_client()
        client.ensure_connected()
        frame = client.rates(mt5_symbol(symbol), "M1", count)
        if frame is None or len(frame) == 0:
            return None
        return _normalize(frame)
    except Exception:
        return None


def get_historical_deriv(symbol: str, count: int) -> pd.DataFrame | None:
    """Chunked Deriv API fallback."""
    try:
        from mt5_mcp.tools.deriv import deriv_candles

        all_candles: list[dict[str, Any]] = []
        end: str | int = "latest"
        remaining = count
        while remaining > 0:
            chunk = min(5000, remaining)
            data = deriv_candles(symbol, count=chunk, granularity=60, end=end)
            if not data:
                break
            all_candles = data + all_candles
            end = data[0]["time"]  # step back to the oldest bar of the chunk
            remaining -= len(data)
            if len(data) < chunk:
                break
        if not all_candles:
            return None
        frame = pd.DataFrame(all_candles).drop_duplicates(subset="time", keep="last")
        return _normalize(frame)
    except Exception:
        return None


def get_historical_data(symbol: str, count: int) -> pd.DataFrame:
    """Newest ``count`` M1 bars, MT5 first with Deriv API fallback."""
    df = get_historical_mt5(symbol, count)
    if df is None or len(df) < 100:
        df = get_historical_deriv(symbol, count)
    if df is None or len(df) == 0:
        raise RuntimeError(f"No historical data available for {symbol}")
    return df.sort_values("timestamp").reset_index(drop=True)


def detect_spike_at_index(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    times: np.ndarray,
    idx: int,
    symbol: str,
    lookback: int = 30,
    spike_threshold_atr: float = 2.5,
    min_spike_ticks: int = 8,
    reward_risk_ratio: float = 1.5,
    sl_atr_buffer: float = 2.0,
    entry_buffer_atrs: float = 1.5,
    atr_value: float | None = None,
) -> dict | None:
    """Detect spike at a specific index, mirroring the live spot detector."""
    if idx < lookback or idx >= len(closes):
        return None

    current_atr = float(atr_value) if atr_value is not None else 0.001
    if current_atr <= 0:
        return None

    hw = highs[idx - lookback : idx]
    lw = lows[idx - lookback : idx]
    cw = closes[idx - lookback : idx]

    is_boom = symbol.startswith("BOOM")
    is_crash = symbol.startswith("CRASH")
    if not (is_boom or is_crash):
        return None

    # Data-driven spike direction: detect which extreme is farther from the
    # current close, matching the live detector logic in boom_crash_strategy.py.
    current_price = float(cw[-1])
    max_high = float(max(hw))
    min_low = float(min(lw))
    high_dist = abs(max_high - current_price)
    low_dist = abs(current_price - min_low)
    spike_up_expected = high_dist > low_dist

    if spike_up_expected:
        max_high_idx = int(np.argmax(hw))
        spike_high = float(hw[max_high_idx])
        spike_low = float(np.min(lw[: max_high_idx + 1])) if max_high_idx > 0 else float(lw[0])
        spike_size = spike_high - spike_low
        direction = "short"
    else:
        min_low_idx = int(np.argmin(lw))
        spike_low = float(lw[min_low_idx])
        spike_high = float(np.max(hw[: min_low_idx + 1])) if min_low_idx > 0 else float(hw[0])
        spike_size = spike_high - spike_low
        direction = "long"

    if spike_size < spike_threshold_atr * current_atr:
        return None
    if spike_size < min_spike_ticks * 0.01:
        return None

    spike_age = lookback - 1 - (max_high_idx if spike_up_expected else min_low_idx)
    current_price = float(cw[-1])

    if spike_up_expected:
        entry_p = spike_high - spike_size * 0.382
        entry_s = spike_high - spike_size * 0.5
        sl_price = spike_high + current_atr * sl_atr_buffer
        risk_38 = sl_price - entry_p
        tp_p = entry_p - risk_38 * reward_risk_ratio
        dir_sign = -1.0
    else:
        entry_p = spike_low + spike_size * 0.382
        entry_s = spike_low + spike_size * 0.5
        sl_price = spike_low - current_atr * sl_atr_buffer
        risk_38 = entry_p - sl_price
        tp_p = entry_p + risk_38 * reward_risk_ratio
        dir_sign = 1.0

    # Which retracement level is closer to current price?
    level = entry_p
    retracement = 0.382
    if abs(current_price - entry_s) < abs(current_price - entry_p):
        level = entry_s
        retracement = 0.5

    entry_buffer = current_atr * entry_buffer_atrs
    near_entry = abs(current_price - level) <= entry_buffer

    return {
        "symbol": symbol,
        "idx": idx,
        "timestamp": float(times[idx]),
        "direction": direction,
        "spike_high": float(spike_high),
        "spike_low": float(spike_low),
        "spike_size": float(spike_size),
        "atr": current_atr,
        "entry_price": float(level),
        "sl_price": float(sl_price),
        "tp_price": float(tp_p),
        "current_price": current_price,
        "near_entry": near_entry,
        "spike_age": spike_age,
        "risk_price": risk_38 if retracement == 0.382 else (sl_price - entry_s if spike_up_expected else entry_s - sl_price),
        "retracement": retracement,
        "rr": reward_risk_ratio,
        "dir_sign": dir_sign,
    }


def simulate_trade(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    times: np.ndarray,
    signal: dict,
    max_hold_bars: int = 120,
    fill_deadline_bars: int = 30,
    atr_values: np.ndarray | None = None,
    trail_arm_r: float = 1.0,
    trail_distance_atr: float = 1.0,
    partial_exit_fraction: float = 0.5,
) -> Trade | None:
    """Leave a stop order at the entry level; fill when touched; then run to chandelier exit.

    Exit model (matches live boom_crash_exits.py):
    - No fixed TP.  The SL is trailed via a chandelier stop.
    - Arm the chandelier when unrealized profit reaches +1R.
    - Trail the SL at ``trail_distance_atr`` x ATR behind the extreme
      (highest-high for longs, lowest-low for shorts).
    - Partially close ``partial_exit_fraction`` (50%) at +1R.
    """
    idx = signal["idx"]
    entry = signal["entry_price"]
    sl = signal["sl_price"]
    direction = signal["direction"]
    sign = signal["dir_sign"]  # +1 long, -1 short
    risk = signal["risk_price"]
    arm_level = entry + sign * risk * trail_arm_r  # price that triggers chandelier arm

    trade = Trade(
        symbol=signal["symbol"],
        entry_time=datetime.fromtimestamp(signal["timestamp"], tz=timezone.utc),
        entry_price=entry,
        sl_price=sl,
        tp_price=0.0,  # no fixed TP with chandelier trail
        direction=direction,
        risk_price=risk,
        spike_age=signal["spike_age"],
        match_retracement=signal["retracement"],
    )

    horizon = idx + 1 + max(fill_deadline_bars, max_hold_bars)
    horizon = min(horizon, len(closes))
    filled = False

    armed = False
    partial_done = False
    highest_since_entry = -np.inf
    lowest_since_entry = np.inf
    current_sl = sl

    for i in range(idx + 1, horizon):
        high, low, close = highs[i], lows[i], closes[i]
        bar_atr = float(atr_values[i]) if atr_values is not None and i < len(atr_values) else 0.001
        if bar_atr <= 0:
            bar_atr = 0.001

        if not filled:
            if sign > 0:  # BUY STOP triggers on an up-cross of the entry
                if high >= entry:
                    filled = True
            elif low <= entry:
                filled = True
            if not filled:
                continue

        # Track extreme since entry
        if sign > 0:
            highest_since_entry = max(highest_since_entry, high)
        else:
            lowest_since_entry = min(lowest_since_entry, low)

        # Check SL hit (original or trailed)
        if sign > 0:
            if low <= current_sl:
                trade.exit_time = datetime.fromtimestamp(times[i], tz=timezone.utc)
                trade.exit_price = current_sl
                trade.exit_reason = "sl"
                trade.pnl_price = current_sl - entry
                return trade
        else:
            if high >= current_sl:
                trade.exit_time = datetime.fromtimestamp(times[i], tz=timezone.utc)
                trade.exit_price = current_sl
                trade.exit_reason = "sl"
                trade.pnl_price = entry - current_sl
                return trade

        # Compute unrealized P&L for arming check
        unrealized = (close - entry) * sign
        crossed_arm = unrealized >= risk * trail_arm_r

        # Arm the chandelier at +1R
        if not armed and crossed_arm:
            armed = True

        # 1R partial exit
        if armed and not partial_done and partial_exit_fraction > 0:
            partial_done = True  # fires once

        # Trail the SL if armed
        if armed:
            distance = trail_distance_atr * bar_atr
            if sign > 0:
                candidate = highest_since_entry - distance
                current_sl = max(candidate, current_sl)
            else:
                candidate = lowest_since_entry + distance
                current_sl = min(candidate, current_sl)

    if not filled:
        return None  # stop never triggered within the deadline

    last_idx = min(idx + 1 + max_hold_bars, len(closes) - 1)
    exit_price = float(closes[last_idx])
    trade.exit_time = datetime.fromtimestamp(times[last_idx], tz=timezone.utc)
    trade.exit_price = exit_price
    trade.exit_reason = "time"
    trade.pnl_price = (exit_price - entry) * sign
    return trade


def run_one(
    symbol: str,
    df: pd.DataFrame,
    spike_threshold_atr: float = 2.5,
    min_spike_ticks: int = 8,
    max_hold_bars: int = 120,
    fill_deadline_bars: int = 30,
    min_bars_between_trades: int = 60,
    reward_risk_ratio: float = 1.5,
    sl_atr_buffer: float = 2.0,
    max_spike_age: int = 15,
    lookback: int = 60,
    atr_series: pd.Series | None = None,
) -> tuple[list[Trade], dict[str, Any]]:
    """Run the backtest for a single pre-loaded symbol frame.

    Defaults mirror the live watcher: max_spike_age 15, lookback 60.
    """
    if atr_series is None:
        atr_series = atr(df, period=14)

    info: dict[str, Any] = {}
    try:
        from mt5_mcp.client import get_client

        sinfo = get_client().symbol_info(mt5_symbol(symbol))
        info = {
            "tick_value": float(sinfo.get("trade_tick_value", 0.001)),
            "tick_size": max(float(sinfo.get("trade_tick_size", 0.001)), 1e-9),
        }
    except Exception:
        info = {"tick_value": 0.001, "tick_size": 0.001}
    usd_per_price_unit = info["tick_value"] / info["tick_size"]

    trades: list[Trade] = []
    last_trade_idx = -min_bars_between_trades
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["timestamp"].to_numpy(dtype="float64")
    atr_arr = (
        atr_series.to_numpy()
        if atr_series is not None
        else atr(df, period=14).to_numpy()
    )
    for i in range(30, len(df) - 1):
        if i - last_trade_idx < min_bars_between_trades:
            continue
        signal = detect_spike_at_index(
            highs,
            lows,
            closes,
            times,
            i,
            symbol,
            lookback=lookback,
            spike_threshold_atr=spike_threshold_atr,
            min_spike_ticks=min_spike_ticks,
            reward_risk_ratio=reward_risk_ratio,
            sl_atr_buffer=sl_atr_buffer,
            atr_value=float(atr_arr[max(0, i - 1)]),
        )
        if signal is None:
            continue
        if max_spike_age < 30 and signal["spike_age"] > max_spike_age:
            continue
        if not signal["near_entry"]:
            continue

        trade = simulate_trade(
            highs,
            lows,
            closes,
            times,
            signal,
            max_hold_bars=max_hold_bars,
            fill_deadline_bars=fill_deadline_bars,
            atr_values=atr_arr,
        )
        if trade:
            trade.pnl_price = trade.pnl_price * usd_per_price_unit
            trade.risk_price = trade.risk_price * usd_per_price_unit
            trades.append(trade)
            last_trade_idx = i
    return trades, info


def run_backtest(
    symbols: list[str] | None = None,
    days: int = 10,
    spike_threshold_atr: float = 2.5,
    min_spike_ticks: int = 8,
    max_hold_bars: int = 120,
    fill_deadline_bars: int = 30,
    min_bars_between_trades: int = 60,
    reward_risk_ratio: float = 1.5,
    sl_atr_buffer: float = 2.0,
    max_spike_age: int = 30,
) -> dict:
    """Run the backtest over symbols with the given parameters."""
    if symbols is None:
        symbols = list(BOOM_CRASH_SYMBOLS)

    trades: list[Trade] = []
    per_symbol_info: dict[str, dict] = {}

    for symbol in symbols:
        try:
            count = days * 1440
            df = get_historical_data(symbol, count)
        except Exception as e:
            print(f"{symbol}: data unavailable ({e})")
            continue
        if len(df) < 200:
            print(f"{symbol}: only {len(df)} bars")
            continue
        if len(df) < days * 1440:
            print(f"{symbol}: {len(df)} bars for {days}d")

        symbol_trades, info = run_one(
            symbol,
            df,
            spike_threshold_atr=spike_threshold_atr,
            min_spike_ticks=min_spike_ticks,
            max_hold_bars=max_hold_bars,
            fill_deadline_bars=fill_deadline_bars,
            min_bars_between_trades=min_bars_between_trades,
            reward_risk_ratio=reward_risk_ratio,
            sl_atr_buffer=sl_atr_buffer,
            max_spike_age=max_spike_age,
        )
        per_symbol_info[symbol] = info
        trades.extend(symbol_trades)
        if symbol_trades:
            print(f"{symbol}: {len(symbol_trades)} trades")

    return _metrics(trades, symbols, per_symbol_info)


def _metrics(
    trades: list[Trade], symbols: list[str], symbol_info: dict[str, dict]
) -> dict:
    if not trades:
        return {"trades": [], "metrics": {}, "by_symbol": {}, "exit_reasons": {}}

    rows = [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_usd": t.pnl_price,
            "risk_usd": t.risk_price,
            "r_multiple": t.r_multiple,
            "exit_reason": t.exit_reason,
            "spike_age": t.spike_age,
        }
        for t in trades
    ]
    table = pd.DataFrame(rows)
    usd = table["pnl_usd"]
    winners = table[usd > 0]
    losers = table[usd < 0]
    total = len(table)

    win_rate = len(winners) / total * 100 if total else 0.0
    gross_win = float(winners["pnl_usd"].sum()) if len(winners) else 0.0
    gross_loss = float(abs(losers["pnl_usd"].sum())) if len(losers) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss else float("inf")
    avg_r = float(table["r_multiple"].mean())

    entry = pd.to_datetime(table["entry_time"], utc=True)
    exit_ = pd.to_datetime(table["exit_time"], utc=True)
    avg_hold_minutes = (
        round(float((exit_ - entry).dt.total_seconds().mean()) / 60.0, 1)
        if total
        else 0.0
    )

    # Max drawdown over the closed-trade equity curve (ordered by exit time).
    eq_sorted = table.sort_values("exit_time")
    equity_curve = eq_sorted["pnl_usd"].cumsum()
    running_peak = equity_curve.cummax()
    drawdowns = running_peak - equity_curve
    max_drawdown_usd = float(drawdowns.max()) if len(drawdowns) else 0.0
    max_dd_idx = int(drawdowns.idxmax()) if len(drawdowns) and drawdowns.max() > 0 else None
    if max_dd_idx is not None:
        dd_end = pd.to_datetime(eq_sorted.loc[max_dd_idx, "exit_time"], utc=True)
        dd_start_mask = eq_sorted["pnl_usd"].cumsum() == running_peak.loc[max_dd_idx]
        dd_start = pd.to_datetime(eq_sorted.loc[dd_start_mask, "exit_time"], utc=True).iloc[-1]
        max_dd_days = round((dd_end - dd_start).total_seconds() / 86400.0, 1)
    else:
        max_dd_days = 0.0

    by_symbol = {}
    for sym in symbols:
        sub = table[table["symbol"] == sym]
        if len(sub) == 0:
            continue
        w = sub[sub["pnl_usd"] > 0]
        by_symbol[sym] = {
            "trades": len(sub),
            "win_rate": round(len(w) / len(sub) * 100, 1),
            "net_usd": round(float(sub["pnl_usd"].sum()), 2),
            "avg_r": round(float(sub["r_multiple"].mean()), 3),
            "avg_risk_usd": round(float(sub["risk_usd"].mean()), 2),
        }

    return {
        "trades": rows,
        "metrics": {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "avg_win_usd": round(float(winners["pnl_usd"].mean()), 2) if len(winners) else 0.0,
            "avg_loss_usd": round(float(losers["pnl_usd"].mean()), 2) if len(losers) else 0.0,
            "profit_factor": round(profit_factor, 2),
            "net_usd": round(float(usd.sum()), 2),
            "avg_r": round(avg_r, 3),
            "avg_hold_minutes": avg_hold_minutes,
            "max_drawdown_usd": round(max_drawdown_usd, 2),
            "max_drawdown_days": max_dd_days,
        },
        "by_symbol": by_symbol,
        "exit_reasons": table["exit_reason"].value_counts().to_dict(),
    }


def sweep(symbols: list[str] | None = None, days: int = 10) -> dict[str, Any]:
    """Grid sweep over the most decision-relevant parameters.

    Each symbol's candle frame (and its full ATR series) is loaded exactly
    once and reused across all grid combos.
    """
    if symbols is None:
        symbols = list(BOOM_CRASH_SYMBOLS)

    grids = {
        "spike_threshold_atr": [2.0, 2.5, 3.0],
        "sl_atr_buffer": [1.0, 2.0, 3.0],
        "reward_risk_ratio": [1.0, 1.5, 2.0],
        "max_spike_age": [10, 15, 30],
    }
    combos = [
        (thr, buf, rr, age)
        for thr in grids["spike_threshold_atr"]
        for buf in grids["sl_atr_buffer"]
        for rr in grids["reward_risk_ratio"]
        for age in grids["max_spike_age"]
    ]

    loaded: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    for symbol in symbols:
        try:
            df = get_historical_data(symbol, days * 1440)
            if len(df) < 500:
                continue
            loaded[symbol] = (df, atr(df, period=14))
            print(f"loaded {symbol}: {len(df)} bars")
        except Exception as e:
            print(f"{symbol}: data unavailable ({e})")

    results: list[dict[str, Any]] = []
    for thr, buf, rr, age in combos:
        trades: list[Trade] = []
        for symbol, (df, atr_series) in loaded.items():
            t, _ = run_one(
                symbol,
                df,
                spike_threshold_atr=thr,
                sl_atr_buffer=buf,
                reward_risk_ratio=rr,
                max_spike_age=age,
                atr_series=atr_series,
            )
            trades.extend(t)
        m = _metrics(trades, list(loaded.keys()), {})["metrics"]
        if not m.get("total_trades"):
            continue
        results.append(
            {
                "params": {
                    "spike_threshold_atr": thr,
                    "sl_atr_buffer": buf,
                    "reward_risk_ratio": rr,
                    "max_spike_age": age,
                },
                **{k: m[k] for k in ("total_trades", "win_rate", "profit_factor", "net_usd", "avg_r")},
            }
        )
    return {
        "results": sorted(results, key=lambda r: r["avg_r"], reverse=True),
        "grid_size": len(results),
    }


def _summary(res: dict, title: str = "BACKTEST RESULTS") -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    m = res["metrics"]
    if not m.get("total_trades"):
        print("No trades in this run.")
        return
    print(
        f"Trades: {m['total_trades']}  WR: {m['win_rate']}%  PF: {m['profit_factor']:.2f}  "
        f"Net (1 lot equiv): ${m['net_usd']:.2f}  Avg R: {m['avg_r']:+.3f}"
    )
    print(f"Avg win ${m['avg_win_usd']:.2f} / avg loss ${m['avg_loss_usd']:.2f} / avg hold {m['avg_hold_minutes']}m")
    print("Exit reasons:", {k: v for k, v in res["exit_reasons"].items()})
    print("\nBy symbol:")
    for sym, d in res["by_symbol"].items():
        print(f"  {sym}: {d['trades']} trades, {d['win_rate']}% WR, ${d['net_usd']:.2f} net, {d['avg_r']:+.3f} avg R")


if __name__ == "__main__":
    import json
    import sys as _sys

    mode = "baseline"
    if len(_sys.argv) > 1:
        mode = _sys.argv[1]

    if mode == "sweep":
        result = sweep(days=10)
        print(f"\nSweep grid: {result['grid_size']} configs evaluated")
        print("\nTop 12 configs by avg R:")
        for r in result["results"][:12]:
            p = r["params"]
            print(
                f"  thr={p['spike_threshold_atr']} buf={p['sl_atr_buffer']} rr={p['reward_risk_ratio']} "
                f"age={p['max_spike_age']}: {r['total_trades']} trades, WR {r['win_rate']}%, "
                f"PF {r['profit_factor']:.2f}, ${r['net_usd']:.2f}, {r['avg_r']:+.3f} R"
            )
        with open("backtest_sweep.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        print("\nSaved to backtest_sweep.json")
    else:
        result = run_backtest(days=10, spike_threshold_atr=2.5, min_spike_ticks=8)
        _summary(result)
        with open("backtest_results.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        print("\nDetailed results saved to backtest_results.json")
