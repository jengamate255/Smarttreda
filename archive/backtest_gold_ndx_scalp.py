"""Quick historical backtest for Gold & NDX Scalper.

Uses MT5 M5/H1 data from the prop account to simulate the scalper strategy
and produce trade-level metrics (win rate, profit factor, avg R multiple).

Run with: python backtest_gold_ndx_scalp.py --days 7
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.client import get_client, reset_client
from mt5_mcp.tools.supply_demand import detect_zones as detect_sd_zones
from mt5_mcp.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest_gold_ndx")


def get_rates_client(symbol: str, timeframe: str, count: int) -> pd.DataFrame | None:
    env = os.environ.copy()
    env["MT5_MCP_MT5_TERMINAL_PATH"] = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
    env["MT5_MCP_MT5_LOGIN"] = "11911448"
    env["MT5_MCP_MT5_PASSWORD"] = ""
    env["MT5_MCP_MT5_SERVER"] = "FundingPips2-SIM"
    try:
        proc = subprocess.run(
            [sys.executable, "-c",
             f"import sys;sys.path.insert(0,r'E:\\Project\\Mt5 Ea');"
             f"from mt5_mcp.client import get_client,reset_client;"
             f"reset_client();c=get_client();c.ensure_connected();"
             f"f=c.rates('{symbol}', '{timeframe}', {count});"
             f"import pandas as pd;"
             f"df=pd.DataFrame(f);"
             f"if df is not None and len(df):"
             f"  df['close']=df['close'].astype(float);"
             f"  df['high']=df['high'].astype(float);"
             f"  df['low']=df['low'].astype(float);"
             f"  df['open']=df['open'].astype(float);"
             f"  df['time']=pd.to_datetime(df['time'], unit='s', utc=True);"
             f"  print(df.to_json(orient='records')[:2000])"],
            capture_output=True,text=True,timeout=60,cwd=str(ROOT),env=env,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.strip())
        if not data:
            return None
        return pd.DataFrame(data)
    except Exception as exc:
        logger.warning("rates fetch error %s: %s", symbol, exc)
        return None


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def ema_series(df: pd.Series, period: int = 200) -> pd.Series:
    return df.ewm(span=period, adjust=False).mean()


def run_backtest(days: int = 7, risk_percent: float = 1.0, rr_min: float = 1.0) -> dict:
    """Run historical backtest for XAUUSD + NDX100 scalper.

    Returns dict with trades list and metrics.
    """
    # Connect to prop account
    env = os.environ.copy()
    env["MT5_MCP_MT5_TERMINAL_PATH"] = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
    env["MT5_MCP_MT5_LOGIN"] = "11911448"
    env["MT5_MCP_MT5_PASSWORD"] = ""
    env["MT5_MCP_MT5_SERVER"] = "FundingPips2-SIM"

    # Get historical data: M5 for entries, H1 for trend
    count_m5 = max(200, days * 24 * 12)  # ~12 M5 bars per day
    count_h1 = max(200, days * 24)

    xau_data = get_rates_client("XAUUSD", "M5", count_m5)
    ndx_data = get_rates_client("NDX100", "M5", count_m5)
    xau_h1 = get_rates_client("XAUUSD", "H1", count_h1)
    ndx_h1 = get_rates_client("NDX100", "H1", count_h1)

    if xau_data is None or xau_data.empty or len(xau_data) < 100:
        logger.error("Not enough XAUUSD M5 data")
        return {"trades": [], "metrics": {}}

    # We'll do a simple bar-by-bar simulation
    # For each new bar, check if there's a valid entry signal
    # This is simplified - real implementation would check each bar

    # For now, let's scan the last portion of data for signals
    # using the same logic as the scalper but simplified

    trades = []
    equity_curve = [9435.64]  # starting balance
    starting_equity = 9435.64

    # Determine trend for each symbol
    def get_trend(h1_df, symbol):
        if h1_df is None or h1_df.empty or len(h1_df) < 200:
            return None
        closes = h1_df["close"].astype(float)
        ema200 = ema_series(closes, 200)
        last_close = closes.iloc[-1]
        last_ema = ema200.iloc[-1]
        if last_close > last_ema:
            return "bull"
        if last_close < last_ema:
            return "bear"
        return None

    trend_xau = get_trend(xau_h1, "XAUUSD")
    trend_ndx = get_trend(ndx_h1, "NDX100")

    logger.info("Trends - XAUUSD: %s, NDX100: %s", trend_xau, trend_ndx)

    # Simple signal scan: look for M5 pullback to zone + trend alignment
    # We'll use a very basic approach: identify zones from S/D detector,
    # then check if price pulls back to zone in trend direction

    # Since full zone detection on large datasets is heavy, let's do a focused scan
    # on the most recent portion
    scan_start = max(len(xau_data) - 200, 0)
    xau_slice = xau_data.iloc[scan_start:]
    ndx_slice = ndx_data.iloc[scan_start:] if ndx_data is not None and not ndx_data.empty else None

    # Basic scan: look for M5 bars where price is near recent high/low (zone-like)
    # and trend aligns
    for i in range(20, min(len(xau_slice), 80)):
        try:
            # Get M5 bar i
            bar = xau_slice.iloc[i]
            bar_next = xau_slice.iloc[i+1] if i+1 < len(xau_slice) else None

            if bar_next is None:
                break

            # Basic trend check
            if trend_xau != "bull":
                continue  # only long in bull trend

            # Basic zone: recent 20-bar high/low as pseudo-S/D
            recent_high = xau_slice.iloc[max(0, i-20):i+1]["high"].max()
            recent_low = xau_slice.iloc[max(0, i-20):i+1]["low"].min()
            bar_high = bar["high"]
            bar_low = bar["low"]

            # If bar_low is near recent low (within 0.5% = zone candidate)
            zone_distance = (bar_low - recent_low) / max(recent_low, 0.01)
            if zone_distance > 0 and zone_distance < 0.005:
                # Potential demand zone pullback entry
                entry = bar_low
                sl = bar_low * 0.99  # 1% below zone = protective SL
                tp = entry + (entry - sl) * rr_min  # 1:1 RR minimum

                # Simulate next bar close
                if bar_next is not None:
                    close_next = float(bar_next["close"])
                    # Check if hit SL or TP
                    risk = entry - sl
                    if risk <= 0:
                        continue
                    pnl = None
                    exit_reason = None
                    if close_next <= sl:
                        pnl = -risk
                        exit_reason = "sl"
                    elif close_next >= tp:
                        pnl = risk  # 1:1
                        exit_reason = "tp"
                    else:
                        pnl = 0
                        exit_reason = "open"

                    if pnl is not None:
                        # Size: 1% equity risk
                        risk_amount = starting_equity * risk_percent / 100.0
                        # Gold pip value approx: $10 per pip for 0.1 lot at 1000$ entry... 
                        # Simplified: assume $1 per pip per 0.1 lot near current price
                        pip_value = 1.0  # approximate
                        lots = risk_amount / (risk * pip_value)
                        lots = round(lots, 2)

                        pnl_usd = pnl * pip_value * lots
                        equity_curve.append(starting_equity + sum([t["pnl_usd"] for t in trades] + [pnl_usd]))
                        trades.append({
                            "symbol": "XAUUSD",
                            "entry_time": bar.name,
                            "entry_price": entry,
                            "sl_price": sl,
                            "tp_price": tp,
                            "direction": "long",
                            "risk_price": risk,
                            "exit_time": bar_next.name,
                            "exit_price": close_next if exit_reason != "open" else None,
                            "exit_reason": exit_reason,
                            "pnl_price": pnl_usd,
                            "spike_age": 0,
                            "match_retracement": zone_distance,
                        })
                        starting_equity = starting_equity + pnl_usd  # update for next trade
        except Exception as e:
            logger.debug("scan bar error: %s", e)
            continue

    # Same for NDX100
    if ndx_slice is not None and not ndx_slice.empty:
        scan_start_n = max(len(ndx_slice) - 200, 0)
        ndx_slice2 = ndx_slice.iloc[scan_start_n:]

        for i in range(20, min(len(ndx_slice2), 80)):
            try:
                bar = ndx_slice2.iloc[i]
                bar_next = ndx_slice2.iloc[i+1] if i+1 < len(ndx_slice2) else None
                if bar_next is None:
                    break

                if trend_ndx != "bull":
                    continue

                recent_high = ndx_slice2.iloc[max(0, i-20):i+1]["high"].max()
                recent_low = ndx_slice2.iloc[max(0, i-20):i+1]["low"].min()
                bar_high = bar["high"]
                bar_low = bar["low"]

                zone_distance = (bar_low - recent_low) / max(recent_low, 0.01)
                if zone_distance > 0 and zone_distance < 0.005:
                    entry = bar_low
                    sl = bar_low * 0.99
                    risk = entry - sl
                    if risk <= 0:
                        continue
                    tp = entry + risk * rr_min

                    bar_next = ndx_slice2.iloc[i+1] if i+1 < len(ndx_slice2) else None
                    if bar_next is None:
                        break

                    close_next = float(bar_next["close"])
                    if close_next <= sl:
                        pnl = -risk
                        exit_reason = "sl"
                    elif close_next >= tp:
                        pnl = risk
                        exit_reason = "tp"
                    else:
                        pnl = 0
                        exit_reason = "open"

                    if pnl is not None:
                        risk_amount = starting_equity * risk_percent / 100.0
                        pip_value = 0.1  # NDX multiplier approx
                        lots = risk_amount / (risk * pip_value)
                        lots = round(lots, 2)

                        pnl_usd = pnl * pip_value * lots
                        equity_curve.append(starting_equity + sum([t["pnl_usd"] for t in trades] + [pnl_usd]))
                        trades.append({
                            "symbol": "NDX100",
                            "entry_time": bar.name,
                            "entry_price": entry,
                            "sl_price": sl,
                            "tp_price": tp,
                            "direction": "long",
                            "risk_price": risk,
                            "exit_time": bar_next.name,
                            "exit_price": close_next if exit_reason != "open" else None,
                            "exit_reason": exit_reason,
                            "pnl_price": pnl_usd,
                            "spike_age": 0,
                            "match_retracement": zone_distance,
                        })
                        starting_equity = starting_equity + pnl_usd
            except Exception as e:
                logger.debug("NDX scan bar error: %s", e)
                continue

    # Compute metrics
    winning = [t for t in trades if t["pnl_price"] and t["pnl_price"] > 0]
    losing = [t for t in trades if t["pnl_price"] and t["pnl_price"] < 0]
    total = len([t for t in trades if t.get("pnl_price")])

    win_rate = len(winning) / total * 100 if total else 0
    gross_win = sum(t["pnl_price"] for t in winning) if winning else 0
    gross_loss = abs(sum(t["pnl_price"] for t in losing)) if losing else 0
    profit_factor = gross_win / gross_loss if gross_loss else float("inf")
    avg_r = sum(t["pnl_price"] / max(t["risk_price"], 1e-9) for t in trades) / total if total else 0

    # Final equity
    final_equity = starting_equity + sum(t["pnl_price"] for t in trades) if trades else starting_equity
    total_dd = (starting_equity - final_equity) / starting_equity * 100 if starting_equity else 0

    metrics = {
        "total_trades": total,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": win_rate,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_r_multiple": avg_r,
        "final_equity": final_equity,
        "total_dd_pct": total_dd,
        "starting_equity": starting_equity,
        "trades": trades,
    }

    logger.info("Backtest complete: %d trades, win rate %.1f%%, PF %.2f, avg R %.2f",
                total, win_rate, profit_factor, avg_r)
    logger.info("Final equity: %.2f (DD %.1f%%)", final_equity, total_dd)

    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold & NDX Scalper Backtest")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk percent per trade")
    parser.add_argument("--rr", type=float, default=1.0, help="Minimum RR")
    args = parser.parse_args()

    logger.info("Starting backtest: days=%d risk=%%s rr=%.1f", args.days, args.risk)
    metrics = run_backtest(args.days, args.risk, args.rr)

    # Save results
    out = ROOT / "logs" / f"backtest_gold_ndx_{args.days}d_{args.risk}pct_{args.rr}rr.json"
    out.write_text(json.dumps(metrics, indent=2, default=str))
    logger.info("Results written to %s", out)

    # Print summary
    m = metrics
    print(f"\n=== BACKTEST SUMMARY ===")
    print(f"Period: ~{args.days} days historical")
    print(f"Total trades: {m['total_trades']}")
    print(f"Win rate: {m['win_rate']:.1f}% ({m['winning_trades']}/{m['losing_trades']}+)")
    print(f"Profit factor: {m['profit_factor']:.2f}")
    print(f"Avg R multiple: {m['avg_r_multiple']:.2f}")
    print(f"Final equity: ${m['final_equity']:.2f} (from ${m['starting_equity']:.2f})")
    print(f"Max drawdown: {m['total_dd_pct']:.1f}%")
    print(f"Total P&L: ${m['final_equity'] - m['starting_equity']:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())