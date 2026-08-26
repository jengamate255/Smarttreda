"""Probe prop terminal via client.rates() path + raw cached timeframes."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config
import MetaTrader5 as mt5
import pandas as pd

client = MT5Client(MT5Config(terminal_path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"))
if not client.connect():
    print("CONNECT FAILED:", client.last_error())
    sys.exit(1)
print("account:", client.account_info().get("login"))

for sym in ["EURUSD", "GBPUSD", "XAUUSD"]:
    # raw cached bars per timeframe
    for tf_name, tf in [("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5),
                        ("M15", mt5.TIMEFRAME_M15), ("M30", mt5.TIMEFRAME_M30),
                        ("H1", mt5.TIMEFRAME_H1)]:
        try:
            data = client._call("copy_rates_from_pos", sym, tf, 0, 200000)
            n = len(data) if data is not None else 0
            if n:
                t0 = pd.to_datetime(data[0]["time"], unit="s")
                t1 = pd.to_datetime(data[-1]["time"], unit="s")
                print(f"{sym} {tf_name}: raw {n} bars {t0:%Y-%m-%d} -> {t1:%Y-%m-%d} ({(t1-t0).days}d)", flush=True)
            else:
                print(f"{sym} {tf_name}: raw 0 bars", flush=True)
        except Exception as e:
            print(f"{sym} {tf_name}: err {e}", flush=True)
    # client.rates() attempts
    for tf in ["M15", "H1"]:
        try:
            df = client.rates(sym, tf, 5000)
            print(f"{sym} {tf}: rates() -> {len(df)} bars, {df['time'].iloc[0]:%Y-%m-%d} -> {df['time'].iloc[-1]:%Y-%m-%d}, last close {df['close'].iloc[-1]:.5f}", flush=True)
        except Exception as e:
            print(f"{sym} {tf}: rates() err {e}", flush=True)
client.shutdown()
print("done")