# -*- coding: utf-8 -*-
"""Parallel, resumable fetch of /api/rates. Skips files already on disk."""
import json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://localhost:8051/api/rates"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

SYMBOLS = [
    "EURUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "NZDJPY", "CHFJPY",
    "GBPCHF", "GBPCAD", "GBPNZD", "GBPAUD", "EURAUD", "EURCAD", "EURNZD", "AUDNZD",
    "XAUUSD", "XAGUSD", "USOIL", "UKOIL",
    "SPX500", "DJI30", "JP225", "GER40", "FTSE100", "STX50",
    "BTCUSD", "ETHUSD",
]
TIMEFRAMES = ["M15", "H1", "H4", "D1"]
COUNT = 1000
WORKERS = 8

def fetch(symbol, tf):
    url = f"{BASE}?symbol={symbol}&timeframe={tf}&count={COUNT}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 3:
                return {"error": "failed"}
            time.sleep(2.0)

def one_task(tf, sym):
    path = os.path.join(OUT, f"{sym}_{tf}.json")
    if os.path.exists(path):
        return f"skip {sym} {tf}"
    data = fetch(sym, tf)
    if "error" in data or "times" not in data:
        return f"FAIL {sym} {tf}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return f"OK   {sym} {tf} n={len(data['times'])}"

def main():
    tasks = [(tf, sym) for tf in TIMEFRAMES for sym in SYMBOLS]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one_task, tf, sym): (tf, sym) for tf, sym in tasks}
        for fut in as_completed(futs):
            print(fut.result(), flush=True)
    print("ALL DONE")

if __name__ == "__main__":
    main()