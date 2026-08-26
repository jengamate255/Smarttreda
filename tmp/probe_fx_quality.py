"""Check FX data quality on prop SIM terminal."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import numpy as np
import pandas as pd
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config

client = MT5Client(MT5Config(terminal_path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"))
if not client.connect():
    print("CONNECT FAILED:", client.last_error()); sys.exit(1)

for sym, tf in [("EURUSD", "M15"), ("XAUUSD", "M15")]:
    df = client.rates(sym, tf, 40000)
    hi, lo, cl, op = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float), df["open"].to_numpy(float)
    rng_ = hi - lo
    flat = np.mean(rng_ == 0)
    body = np.abs(cl - op)
    tr = np.maximum(rng_, np.maximum(np.abs(hi - np.roll(cl, 1)), np.abs(lo - np.roll(cl, 1))))
    atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().to_numpy()
    spr = df["spread"].to_numpy(float)
    print(f"{sym} {tf}: n={len(df)} flat_range_bars={flat*100:.1f}% | median range {np.median(rng_):.5f} "
          f"median body {np.median(body):.5f} | median TR {np.median(tr[1:]):.5f}")
    print(f"   ATR14: p50={np.median(atr14[200:]):.5f} p90={np.percentile(atr14[200:],90):.5f} | "
          f"spread pts: p50={np.median(spr):.0f} p90={np.percentile(spr,90):.0f} | "
          f"spread/ATR50 = {np.median(spr)/np.median(atr14[200:]) if np.median(atr14[200:])>0 else float('nan'):.2f}")
client.shutdown()
print("done")