"""Inspect price levels, ATR and spread distribution on Deriv symbols."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import numpy as np
import pandas as pd
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config

client = MT5Client(MT5Config(terminal_path=r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"))
if not client.connect():
    print("CONNECT FAILED:", client.last_error()); sys.exit(1)

for sym in ["Boom 500 Index", "Boom 100 Index", "Crash 500 Index", "Crash 100 Index"]:
    info = client.symbol_info(sym)
    tick = client.tick(sym)
    df = client.rates(sym, "M5", 20000)
    spr = df["spread"].to_numpy(float)
    hi, lo, cl = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    tr = np.maximum(hi - lo, np.maximum(abs(hi - np.roll(cl, 1)), abs(lo - np.roll(cl, 1))))
    atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().to_numpy()
    print(f"{sym}: last close {cl[-1]:.3f} | tick bid {tick['bid']:.3f} ask {tick['ask']:.3f} "
          f"spread_tick {tick['ask']-tick['bid']:.4f} ({round((tick['ask']-tick['bid'])/info['point'])} pts)")
    print(f"  spread col: p10={np.percentile(spr,10):.0f} p50={np.percentile(spr,50):.0f} p90={np.percentile(spr,90):.0f} pts "
          f"| ATR14 p50={np.percentile(atr14[200:],50):.3f} pts | p50 spread/ATR = {np.percentile(spr,50)/np.percentile(atr14[200:],50):.1f}")
    print(f"  price range {lo.min():.1f}-{hi.max():.1f} | point={info['point']}")
client.shutdown()
print("done")