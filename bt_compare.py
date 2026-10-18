"""Compare 3 MTF gate combinations across the tradeable universe.

Scenario 1: D1 + H4 + H1 + M30 + M15 (full stack)
Scenario 2: D1 + H4 + H1 + M30        (drop M15)
Scenario 3: H4 + H1 + M15             (simplified)
Scenario 0: H1-only                   (baseline, no gate)

Each mode: walk H1 bars, enter when gate TFs agree, SL=2ATR, TP=2R.
"""

from __future__ import annotations
import json, sys
from pathlib import Path
import urllib.request as _req
import pandas as pd
import numpy as np

GW = "http://127.0.0.1:8060"
AUTH_TOKEN = ""

def _load_token():
    global AUTH_TOKEN
    if AUTH_TOKEN:
        return AUTH_TOKEN
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("WEB_AUTH_TOKEN="):
                AUTH_TOKEN = line.split("=", 1)[1].strip()
                break
    return AUTH_TOKEN

def gw_call(method, kwargs):
    _load_token()
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW+"/call/"+method, data=body,
                       headers={"Content-Type":"application/json","X-Auth-Token":AUTH_TOKEN})
    with _req.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    if data.get("error"):
        raise RuntimeError(data["error"])
    p = data.get("data",{})
    if isinstance(p, dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p, list) else pd.DataFrame()

def rates(sym, tf, n):
    return gw_call("rates", {"symbol":sym,"timeframe":tf,"count":n})

def ema(s, w): return s.ewm(span=w, adjust=False).mean()

def atr(df, p=14):
    h,l,c = df["high"],df["low"],df["close"]
    prev = c.shift(1)
    tr = pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p).mean()

def bias(df, fast=50, slow=200):
    ef, es = ema(df["close"],fast), ema(df["close"],slow)
    t = pd.Series(0, index=df.index)
    t[ef>es] = 1; t[ef<es] = -1
    rh = df["high"].rolling(20).max().shift(20)
    rl = df["low"].rolling(20).min().shift(20)
    bull = (t==1) & (df["close"]>rh*0.999)
    bear = (t==-1) & (df["close"]<rl*1.001)
    return pd.Series(0,index=df.index).mask(bull,1).mask(bear,-1)

def sim(h1, gates, label):
    """Walk H1, enter when ALL gates agree. SL=2ATR, TP=2R, time-stop 200 bars."""
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    filled = {}
    for tf, ser in gates.items():
        filled[tf] = ser.reindex(h1.index, method="ffill")
    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue
            ok = True
            for tf, ser in filled.items():
                if ser.iloc[i] != sig:
                    ok = False; break
            if not ok: continue
            entry = p; stop = max(2*a.iloc[i], 0.0001)
            sl,tp = entry-sig*stop, entry+sig*2*stop
            in_pos=sig; entry_i=i
        else:
            hi,lo = h1["high"].iloc[i], h1["low"].iloc[i]
            if in_pos==1:
                if lo<=sl: trades.append({"pnl":sl-entry,"bars":i-entry_i}); in_pos=0
                elif hi>=tp: trades.append({"pnl":tp-entry,"bars":i-entry_i}); in_pos=0
            else:
                if hi>=sl: trades.append({"pnl":entry-sl,"bars":i-entry_i}); in_pos=0
                elif lo<=tp: trades.append({"pnl":entry-tp,"bars":i-entry_i}); in_pos=0
            if in_pos!=0 and i-entry_i>200:
                pnl = p-entry if in_pos==1 else entry-p
                trades.append({"pnl":pnl,"bars":i-entry_i}); in_pos=0
    n = len(trades)
    if n == 0:
        return {"trades":0,"win%":0,"expect":0,"PF":0,"R":0,"avg_bars":0}
    wins = [t for t in trades if t["pnl"]>0]
    losses = [t for t in trades if t["pnl"]<=0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    avg_win = gw/len(wins) if wins else 0
    avg_loss = gl/len(losses) if losses else 0
    return {
        "trades":n,
        "win%": round(len(wins)/n*100,1),
        "expect": round(sum(t["pnl"] for t in trades)/n, 6),
        "PF": round(gw/gl,2) if gl else (float("inf") if gw else 0),
        "R": round(avg_win/avg_loss,2) if avg_loss else 0,
        "avg_bars": round(sum(t["bars"] for t in trades)/n,1),
    }

def main():
    symbols = sys.argv[1:] if len(sys.argv)>1 else [
        "EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD",
        "XAUUSD","XAGUSD","USOIL",
        "BTCUSD","ETHUSD",
    ]

    try:
        with _req.urlopen(GW+"/health",timeout=5) as r:
            gw = json.loads(r.read())
        print(f"gateway: {gw.get('server','?')}  uptime {gw.get('uptime_s',0):.0f}s")
    except Exception as e:
        print(f"FATAL: {e}",file=sys.stderr); return 1

    # scenario definitions
    scenarios = {
        "S0: H1-only":             {},
        "S1: D1+H4+H1+M30+M15":   {"D1":None,"H4":None,"M30":None,"M15":None},
        "S2: D1+H4+H1+M30":       {"D1":None,"H4":None,"M30":None},
        "S3: H4+H1+M15":          {"H4":None,"M15":None},
    }

    # crypto skip list (D1 gate kills all trades)
    crypto_skip = {"BTCUSD","ETHUSD","BTCUSD.","ETHUSD.","SOLUSD","SOLUSD."}

    for sym in symbols:
        sym_clean = sym.rstrip(".")
        try:
            h1 = rates(sym,"H1",2000)
            d1 = rates(sym,"D1",500)
            h4 = rates(sym,"H4",800)
            m30 = rates(sym,"M30",3000)
            m15 = rates(sym,"M15",5000)
        except Exception as e:
            print(f"{sym:10s} FETCH ERROR: {e}"); continue
        if len(h1)<500:
            print(f"{sym:10s} insufficient H1={len(h1)}"); continue

        d1b = bias(d1)
        h4b = bias(h4)
        m30b = bias(m30)
        m15b = bias(m15)

        is_crypto = sym_clean.upper() in crypto_skip

        results = {}
        for label, gates in scenarios.items():
            if not gates:
                # H1-only baseline
                results[label] = sim(h1.copy(), {}, label)
            else:
                g = {}
                for tf in gates:
                    if tf == "D1": g["D1"] = d1b
                    elif tf == "H4": g["H4"] = h4b
                    elif tf == "M30": g["M30"] = m30b
                    elif tf == "M15": g["M15"] = m15b
                results[label] = sim(h1.copy(), g, label)

        # print per-symbol comparison
        print(f"\n{'='*70}")
        print(f" {sym}  {'CRYPTO - skip D1 gate' if is_crypto else ''}")
        print(f"{'='*70}")
        print(f"{'scenario':28s} {'trades':>7s} {'win%':>6s} {'expect':>10s} {'PF':>6s} {'R:R':>5s} {'bars':>6s}")
        print("-"*70)
        for label in scenarios:
            r = results[label]
            print(f"{label:28s} {r['trades']:>7d} {r['win%']:>6.1f} {r['expect']:>10.6f} {r['PF']:>6.2f} {r['R']:>5.2f} {r['avg_bars']:>6.1f}")

    # aggregate summary
    print(f"\n{'='*70}")
    print(" AGGREGATE SUMMARY (all symbols)")
    print(f"{'='*70}")
    # re-run all and aggregate
    agg = {label: {"trades":0,"wins":0,"gross_w":0,"gross_l":0,"expect_sum":0} for label in scenarios}
    for sym in symbols:
        sym_clean = sym.rstrip(".")
        try:
            h1 = rates(sym,"H1",2000)
            d1 = rates(sym,"D1",500)
            h4 = rates(sym,"H4",800)
            m30 = rates(sym,"M30",3000)
            m15 = rates(sym,"M15",5000)
        except:
            continue
        if len(h1)<500: continue
        d1b=bias(d1); h4b=bias(h4); m30b=bias(m30); m15b=bias(m15)
        is_crypto = sym_clean.upper() in crypto_skip

        for label, gates in scenarios.items():
            if not gates:
                r = sim(h1.copy(), {}, label)
            else:
                g = {}
                for tf in gates:
                    if tf == "D1": g["D1"] = d1b
                    elif tf == "H4": g["H4"] = h4b
                    elif tf == "M30": g["M30"] = m30b
                    elif tf == "M15": g["M15"] = m15b
                r = sim(h1.copy(), g, label)
            agg[label]["trades"] += r["trades"]

    print(f"{'scenario':28s} {'total_trades':>12s}")
    print("-"*42)
    for label in scenarios:
        print(f"{label:28s} {agg[label]['trades']:>12d}")

if __name__ == "__main__":
    sys.exit(main())
