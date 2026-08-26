"""Probe Deriv terminal via client.rates() path for M1/M5 depth."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config
import pandas as pd

client = MT5Client(MT5Config(terminal_path=r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"))
if not client.connect():
    print("CONNECT FAILED:", client.last_error())
    sys.exit(1)
print("account:", client.account_info().get("login"))

for sym in ["Boom 500 Index", "Boom 100 Index", "Crash 500 Index", "Crash 100 Index",
            "Boom 1000 Index", "Crash 1000 Index", "Boom 900 Index", "Volatility 75 Index"]:
    for tf in ["M1", "M5"]:
        try:
            df = client.rates(sym, tf, 80000)
            print(f"{sym} {tf}: rates() -> {len(df)} bars, {df['time'].iloc[0]:%Y-%m-%d %H:%M} -> {df['time'].iloc[-1]:%Y-%m-%d %H:%M} ({(df['time'].iloc[-1]-df['time'].iloc[0]).days}d), tick_vol sum={df['tick_volume'].sum():.0f}", flush=True)
        except Exception as e:
            print(f"{sym} {tf}: rates() err {type(e).__name__}: {e}", flush=True)
client.shutdown()
print("done")