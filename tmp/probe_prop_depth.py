"""Check max history depth available for FX symbols on prop terminal."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config

client = MT5Client(MT5Config(terminal_path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"))
if not client.connect():
    print("CONNECT FAILED:", client.last_error())
    sys.exit(1)
for sym in ["EURUSD", "GBPUSD", "XAUUSD"]:
    for tf, name in [("M15", "M15"), ("H1", "H1")]:
        try:
            df = client.rates(sym, tf, 80000)
            print(f"{sym} {name}: {len(df)} bars, {df['time'].iloc[0]:%Y-%m-%d} -> {df['time'].iloc[-1]:%Y-%m-%d} ({(df['time'].iloc[-1]-df['time'].iloc[0]).days}d)", flush=True)
        except Exception as e:
            print(f"{sym} {name}: err {type(e).__name__} {e}", flush=True)
client.shutdown()
print("done")