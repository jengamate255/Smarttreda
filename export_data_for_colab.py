"""Export all MT5 rate data for Colab backtesting (detached-safe).

Writes a progress log to data/colab_export/export_log.txt so a poller can
see how far it is. Light TFs first so data banks early; heavy M1/M5/M15 last.
"""
import os, sys, json, time
from pathlib import Path

PROJECT = Path(r"E:\Project\Mt5 Ea")
GW = "http://127.0.0.1:8060"

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "mt5_mcp"))
from dotenv import load_dotenv
load_dotenv(PROJECT / ".env")

import urllib.request as _req
import json as _json
import pandas as pd

TOKEN = os.getenv("WEB_AUTH_TOKEN", "")
EXPORT_DIR = PROJECT / "data" / "colab_export"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LOG = EXPORT_DIR / "export_log.txt"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

def gw_call(method, **kwargs):
    body = _json.dumps(kwargs).encode()
    req = _req.Request(f"{GW}/call/" + method, data=body,
                       headers={"Content-Type": "application/json", "X-Auth-Token": TOKEN})
    with _req.urlopen(req, timeout=600) as r:
        data = _json.loads(r.read().decode())
    if data.get("error"): raise RuntimeError(data["error"])
    p = data.get("data", {})
    if isinstance(p, dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p, list) else pd.DataFrame()

def rates(sym, tf, n):
    for i in range(3):
        try:
            df = gw_call("rates", symbol=sym, timeframe=tf, count=n)
            if len(df) > 0:
                df["time"] = df["time"].astype(str)
                df = df.sort_values("time").reset_index(drop=True)
            return df
        except Exception as e:
            log(f"  retry {i+1} for {sym} {tf}: {str(e)[:120]}")
            time.sleep(3)
    return pd.DataFrame()

# Light TFs first (bank data early), heavy M-series last.
TF_CONFIG = {
    "H1": 2400,    # sim base, ~100 trading days
    "W1": 270,
    "MN1": 255,
    "D1": 350,
    "H12": 450,
    "H8": 550,
    "H6": 650,
    "H4": 850,
    "H3": 1000,
    "H2": 1500,
    "M30": 10000,  # ~208 days
    "M15": 10000,  # ~104 days
    "M5": 29500,   # ~102 days
    "M1": 30000,   # ~20 days (best available)
}

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD"]

def pip_size(sym):
    s = sym.upper().rstrip(".")
    return 0.01 if "JPY" in s else 0.0001

log(f"=== export start: {len(SYMBOLS)} symbols x {len(TF_CONFIG)} timeframes ===")
t0 = time.time()
ok, fail = 0, 0
for sym in SYMBOLS:
    log(f"--- {sym} ---")
    for tf, n in TF_CONFIG.items():
        try:
            df = rates(sym, tf, n)
            if len(df) == 0:
                log(f"  {tf}: NO DATA")
                fail += 1
                continue
            out = EXPORT_DIR / f"{sym}_{tf}.parquet"
            df.to_parquet(out, index=False)
            ok += 1
            log(f"  {tf}: {len(df)} bars ({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")
        except Exception as e:
            fail += 1
            log(f"  {tf}: FAILED - {str(e)[:150]}")

with open(EXPORT_DIR / "pip_sizes.json", "w") as f:
    _json.dump({s: pip_size(s) for s in SYMBOLS}, f, indent=2)

with open(EXPORT_DIR / "manifest.json", "w") as f:
    _json.dump({
        "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "symbols": SYMBOLS,
        "timeframes": list(TF_CONFIG),
        "config": TF_CONFIG,
        "files_ok": ok,
        "files_failed": fail,
    }, f, indent=2)

log(f"=== export done: {ok} ok, {fail} failed, {time.time()-t0:.0f}s ===")
