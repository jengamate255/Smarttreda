"""Probe both MT5 terminals for available history depth per symbol/timeframe."""
import sys
import os

sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config

PROP = {
    "name": "PROP",
    "path": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    "symbols": ["EURUSD", "GBPUSD", "XAUUSD"],
    "tfs": ["M15", "H1"],
}
DERIV = {
    "name": "DERIV",
    "path": r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe",
    "symbols": [
        "Boom 500 Index",
        "Boom 100 Index",
        "Crash 500 Index",
        "Crash 100 Index",
        "Boom 1000 Index",
        "Crash 1000 Index",
        "Boom 900 Index",
        "Volatility 75 Index",
    ],
    "tfs": ["M1", "M5"],
}

which = sys.argv[1] if len(sys.argv) > 1 else "PROP"
cfg = PROP if which == "PROP" else DERIV
print(f"=== {cfg['name']} terminal: {cfg['path']} ===", flush=True)
client = MT5Client(MT5Config(terminal_path=cfg["path"]))
if not client.connect():
    print("CONNECT FAILED:", client.last_error())
    sys.exit(1)
acct = client.account_info()
print(f"account: {acct.get('login')} @ {acct.get('server')}", flush=True)

# total cached bars via copy_rates_from_pos with large count
import MetaTrader5 as mt5
for sym in cfg["symbols"]:
    if not client.symbol_exists(sym):
        print(f"{sym}: NOT AVAILABLE")
        continue
    info = client.symbol_info(sym)
    print(f"{sym}: point={info.get('point')} digits={info.get('digits')} trade_mode={info.get('trade_mode')}", flush=True)
    for tf in cfg["tfs"]:
        tf_map = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
                  "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
        data = client._call("copy_rates_from_pos", sym, tf_map[tf], 0, 200000)
        if data is None or len(data) == 0:
            print(f"  {tf}: NO DATA")
            continue
        import pandas as pd
        df = pd.DataFrame(data)
        t0 = pd.to_datetime(df["time"].min(), unit="s", utc=True)
        t1 = pd.to_datetime(df["time"].max(), unit="s", utc=True)
        days = (t1 - t0).days
        vol = df["tick_volume"].sum()
        print(f"  {tf}: {len(df)} bars, {t0:%Y-%m-%d} -> {t1:%Y-%m-%d} ({days}d), tick_vol={vol}", flush=True)
    # spread info
    tick = client.tick(sym)
    point = info.get("point")
    print(f"  spread={round((tick.get('ask',0)-tick.get('bid',0))/point, 2) if point else 'n/a'} pts", flush=True)
client.shutdown()
print("done", flush=True)