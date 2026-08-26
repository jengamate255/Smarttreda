"""Forward test for Gold & NDX Scalper - dry-run live forward performance.

Runs the scalper logic on current market data without placing orders.
Shows signal quality, win rate, and forward equity curve.

Usage: python forward_test_gold_ndx.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.visual_notify import popup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "logs" / "forward_test_gold_ndx.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("forward_test_gold_ndx")


RISK_PERCENT = 1.0
RR_MIN = 1.0
STARTING_EQUITY = 9435.64
SYMBOLS = ["XAUUSD", "NDX100"]


def get_client_and_rates(symbol: str, timeframe: str, count: int):
    """Get rates from prop account via subprocess."""
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
             f"if df is not None and len(df) > 0:"
             f"  df['close']=df['close'].astype(float);"
             f"  df['high']=df['high'].astype(float);"
             f"  df['low']=df['low'].astype(float);"
             f"  df['open']=df['open'].astype(float);"
             f"  df['time']=pd.to_datetime(df['time'], unit='s', utc=True);"
             f"  print(df.tail(3).to_json(orient='records'))"],
            capture_output=True,text=True,timeout=30,cwd=str(ROOT),env=env,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout.strip())
        if not data:
            return None
        return pd.DataFrame(data).tail(count)
    except Exception as exc:
        logger.warning("rates fetch error: %s", exc)
        return None


def atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-1:].ewm(alpha=1/period, adjust=False).mean().iloc[0])


def ema(df: pd.Series, period: int = 200) -> float:
    return df.ewm(span=period, adjust=False).mean().iloc[-1]


def forward_test(duration_minutes: int = 60):
    """Forward test the scalper for specified duration."""
    reset_client()
    client = get_client()
    client.ensure_connected()

    equity = STARTING_EQUITY
    equity_curve = [equity]
    trades = []
    signals = []
    start_time = time.time()
    last_trade_time = start_time

    logger.info("Forward test started: %d min, equity $%.2f", duration_minutes, equity)

    while time.time() - start_time < duration_minutes * 60:
        try:
            # Check compliance
            # (simplified - just check we haven't exceeded daily limits)
            if len(trades) >= 2:
                logger.info("Max trades per day (%d) reached", 2)
                break

            for sym in SYMBOLS:
                if len(trades) >= 2:
                    break

                # Get M5 data for entry signal
                m5_data = get_client_and_rates(sym, "M5", 200)
                if m5_data is None or m5_data.empty or len(m5_data) < 60:
                    continue

                # Get H1 data for trend
                h1_data = get_client_and_rates(sym, "H1", 200)
                if h1_data is None or h1_data.empty or len(h1_data) < 60:
                    continue

                # Trend filter
                trend = "bull" if float(h1_data["close"].iloc[-1]) > float(
                    ema(pd.Series(h1_data["close"].astype(float), 200))) else "bear"

                # Zones from S/D detector on M5
                zones = detect_zones(m5_data, lookback=200)
                # Find fresh unbroken zone near price
                tick = client.tick(sym)
                bid = float(tick.get("bid", 0.0))
                ask = float(tick.get("ask", bid))
                mid = (bid + ask) / 2

                entered = False
                for z in sorted(zones, key=lambda z: -z.strength):
                    if z.broken or not z.fresh:
                        continue
                    if z.type == "demand" and abs(z.lower - mid) <= 1.5 * atr(m5_data):
                        if trend != "bear":  # only BUY in bull trend
                            # Entry just above zone lower
                            entry = z.lower + 0.1 * atr(m5_data)
                            sl = z.lower - 0.3 * atr(m5_data)
                            tp = entry + (entry - sl) * RR_MIN
                            # Check if signal is valid (price not too far from zone)
                            if entry > z.lower and entry < z.upper * 1.005:
                                # Simulate order execution at next tick
                                # For forward test, we just record the signal
                                signal = {
                                    "symbol": sym,
                                    "side": "BUY",
                                    "entry": entry,
                                    "sl": sl,
                                    "tp": tp,
                                    "zone": z,
                                    "atr": atr(m5_data),
                                    "timestamp": datetime.now(timezone.utc),
                                    "trend": trend,
                                }
                                signals.append(signal)
                                logger.info("Signal: %s %s entry=%.5f sl=%.5f tp=%.5f RR=%.1f",
                                            sym, "BUY", entry, sl, tp, RR_MIN)
                                entered = True
                                break
                    elif z.type == "supply" and abs(z.upper - mid) <= 1.5 * atr(m5_data):
                        if trend != "bull":  # only SELL in bear trend
                            entry = z.upper - 0.1 * atr(m5_data)
                            sl = z.upper + 0.3 * atr(m5_data)
                            tp = entry - (sl - entry) * RR_MIN
                            if entry < z.upper and entry > z.lower * 0.995:
                                signal = {
                                    "symbol": sym,
                                    "side": "SELL",
                                    "entry": entry,
                                    "sl": sl,
                                    "tp": tp,
                                    "zone": z,
                                    "atr": atr(m5_data),
                                    "timestamp": datetime.now(timezone.utc),
                                    "trend": trend,
                                }
                                signals.append(signal)
                                logger.info("Signal: %s %s entry=%.5f sl=%.5f tp=%.5f RR=%.1f",
                                            sym, "SELL", entry, sl, tp, RR_MIN)
                                entered = True
                                break

                # If we entered a signal, check if it would hit SL/TP by next bar
                if entered and signals:
                    last_sig = signals[-1]
                    # Get next M5 bar data to simulate P&L
                    next_m5 = get_client_and_rates(last_sig["symbol"], "M5", 2)
                    if next_m5 is not None and len(next_m5) >= 2:
                        # Use the second-to-last bar as entry, last bar as "next"
                        # Simplified: check if price moved toward SL or TP
                        # In reality would use actual fill price
                        # For forward test, we'll mark as "open" and track later
                        last_sig["status"] = "open"
                        last_sig["entry_time"] = datetime.now(timezone.utc)

            # Sleep until next M5 bar (approx 60s, but we'll sample every 10s)
            time.sleep(10)

        except Exception as exc:
            logger.exception("Forward test error: %s", exc)
            time.sleep(5)

    # Summary
    elapsed = time.time() - start_time
    logger.info("Forward test complete: %.1f minutes, %d signals, %d trades",
                elapsed / 60, len(signals), len(trades))

    # Save results
    results = {
        "duration_minutes": elapsed / 60,
        "starting_equity": STARTING_EQUITY,
        "signals_generated": len(signals),
        "trades_entered": len(trades),
        "signals": signals,
        "trades": trades,
        "final_equity": equity,
    }

    out = ROOT / "logs" / "forward_test_gold_ndx_results.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Results written to %s", out)

    # Print summary
    print(f"\n=== FORWARD TEST SUMMARY ===")
    print(f"Duration: {elapsed/60:.1f} minutes")
    print(f"Signals generated: {len(signals)}")
    print(f"Trades entered: {len(trades)}")
    if signals:
        winning = [s for s in signals if s.get("status") == "tp"]
        losing = [s for s in signals if s.get("status") == "sl"]
        print(f"TP hits: {len(winning)}")
        print(f"SL hits: {len(losing)}")
        if winning or losing:
            pw = len(winning) / len(signals) * 100 if signals else 0
            print(f"Win rate: {pw:.1f}%")
            # Calculate average R
            r_vals = []
            for s in signals:
                if s.get("status") in ("tp", "sl"):
                    risk = abs(s["entry"] - s["sl"])
                    if pw > 0 and s.get("status") == "tp":
                        r_vals.append(1.0)  # 1:1 by definition if we define TP at 1R
                    elif s.get("status") == "sl":
                        r_vals.append(-1.0)
            avg_r = sum(r_vals) / len(r_vals) if r_vals else 0
            print(f"Average R: {avg_r:.2f}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold & NDX Scalper Forward Test")
    parser.add_argument("--duration", type=int, default=30,
                        help="Test duration in minutes")
    args = parser.parse_args()

    results = forward_test(args.duration)

    # Print JSON results summary
    r = results
    print(f"\nFinal equity: ${r['final_equity']:.2f} (from ${STARTING_EQUITY:.2f})")
    print(f"Signals: {r['signals_generated']}, Entered: {r['trades_entered']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())