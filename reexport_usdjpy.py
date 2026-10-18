"""Re-export only the failed symbols (USDJPY) from the gateway."""
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
LOG = EXPORT_DIR / "reexport_log.txt"

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

def rates(sym, tf, n, attempts=5):
    for i in range(attempts):
        try:
            df = gw_call("rates", symbol=sym, timeframe=tf, count=n)
            if len(df) > 0:
                df["time"] = df["time"].astype(str)
                return df.sort_values("time").reset_index(drop=True)
        except Exception as e:
            log(f"  retry {i+1} {sym} {tf}: {str(e)[:100]}")
            time.sleep(5 * (i + 1))  # longer backoff: gateway was overloaded
    return pd.DataFrame()

TF_CONFIG = {
    "H1": 2400, "W1": 270, "MN1": 255, "D1": 350, "H12": 450, "H8": 550,
    "H6": 650, "H4": 850, "H3": 1000, "H2": 1500,
    "M30": 10000, "M15": 10000, "M5": 29500, "M1": 30000,
}
SYMBOLS = ["USDJPY"]

log("=== USDJPY re-export start (5 attempts, long backoff) ===")
ok = 0
for sym in SYMBOLS:
    for tf, n in TF_CONFIG.items():
        df = rates(sym, tf, n)
        if len(df) == 0:
            log(f"  {tf}: FAILED after all retries")
            continue
        df.to_parquet(EXPORT_DIR / f"{sym}_{tf}.parquet", index=False)
        ok += 1
        log(f"  {tf}: {len(df)} bars")
log(f"=== done: {ok}/14 ok ===")
