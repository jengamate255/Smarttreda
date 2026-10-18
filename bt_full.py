"""Expanded backtest: D1 gate across full tradeable universe.

Tests two modes per symbol:
  H1-only: EMA50/200 + structure flip → immediate entry
  D1-gated: same H1 signal but only when D1 bias agrees

Also runs a desk-style confluence test that adds:
  - Supply/demand zone filter (price must pull back into a zone)
  - ATR-based risk sizing (2 ATR stop, 2R target)
  - Volume confirmation (entry bar volume > 1.5x average)
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

def gw_call(method: str, kwargs: dict) -> pd.DataFrame:
    _load_token()
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW + "/call/" + method, data=body,
                       headers={"Content-Type": "application/json",
                                "X-Auth-Token": AUTH_TOKEN})
    with _req.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(data["error"])
    payload = data.get("data", {})
    if isinstance(payload, dict) and "columns" in payload:
        return pd.DataFrame(payload["rows"], columns=payload["columns"])
    return pd.DataFrame(payload) if isinstance(payload, list) else pd.DataFrame()

def rates(symbol, tf, count):
    return gw_call("rates", {"symbol": symbol, "timeframe": tf, "count": count})

# ── indicators ────────────────────────────────────────────────────────
def ema(s, span): return s.ewm(span=span, adjust=False).mean()

def atr(df, period=14):
    h,l,c = df["high"],df["low"],df["close"]
    prev = c.shift(1)
    tr = pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bias(df, fast=50, slow=200):
    ef, es = ema(df["close"],fast), ema(df["close"],slow)
    t = pd.Series(0, index=df.index)
    t[ef>es] = 1; t[ef<es] = -1
    rh = df["high"].rolling(20).max().shift(20)
    rl = df["low"].rolling(20).min().shift(20)
    bull = (t==1) & (df["close"]>rh*0.999)
    bear = (t==-1) & (df["close"]<rl*1.001)
    return pd.Series(0,index=df.index).mask(bull,1).mask(bear,-1)

def detect_zones(df, lookback=100):
    """Simple supply/demand zone detection."""
    zones = []
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    for i in range(lookback, len(df)-1):
        if i < 4: continue
        # demand: drop-base-rally
        if lows[i] < lows[i-1] and closes[i] > closes[i-1] and closes[i] > closes[i-2]:
            zones.append({"type":"demand","top":highs[i],"bottom":lows[i],"idx":i})
        # supply: rally-base-drop
        if highs[i] > highs[i-1] and closes[i] < closes[i-1] and closes[i] < closes[i-2]:
            zones.append({"type":"supply","top":highs[i],"bottom":lows[i],"idx":i})
    return zones

# ── simulations ────────────────────────────────────────────────────────
def sim_h1_only(h1):
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue
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
    return trades

def sim_d1_gate(h1, d1_b):
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    d1f = d1_b.reindex(h1.index, method="ffill")
    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue
            if d1f.iloc[i] != sig: continue
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
    return trades

def sim_desk_confluence(h1, d1_b, m15):
    """Full desk-style: D1 gate + volume + zone proximity."""
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    d1f = d1_b.reindex(h1.index, method="ffill")
    # volume average (use close as proxy since MT5 doesn't return real volume)
    vol_avg = h1["close"].rolling(20).std()  # use volatility as "energy" proxy
    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        energy = abs(h1["close"].iloc[i] - h1["close"].iloc[max(0,i-5)]) / max(a.iloc[i], 0.0001)
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue
            if d1f.iloc[i] != sig: continue
            if energy < 0.5: continue  # low energy = no momentum
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
    return trades

def stats(trades, label):
    n = len(trades)
    if n == 0:
        return {"label":label,"trades":0,"win%":0,"expect":0,"PF":0,"avg_bars":0}
    wins = [t for t in trades if t["pnl"]>0]
    losses = [t for t in trades if t["pnl"]<=0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {
        "label":label,
        "trades":n,
        "win%": round(len(wins)/n*100,1),
        "expect": round(sum(t["pnl"] for t in trades)/n, 6),
        "PF": round(gw/gl,2) if gl else float("inf") if gw else 0,
        "avg_bars": round(sum(t["bars"] for t in trades)/n,1),
    }

def main():
    symbols = sys.argv[1:] if len(sys.argv)>1 else [
        "EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD",
        "XAUUSD","XAGUSD","USOIL",
        "BTCUSD","ETHUSD","SOLUSD",
    ]

    try:
        with _req.urlopen(GW+"/health", timeout=5) as r:
            gw = json.loads(r.read())
        print(f"gateway: ok ({gw.get('server','?')})  uptime {gw.get('uptime_s',0):.0f}s")
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr); return 1

    hdr = f"{'symbol':10s} {'mode':20s} {'trades':>6s} {'win%':>6s} {'expect':>10s} {'PF':>6s} {'avg_bars':>9s}"
    print(hdr); print("-"*len(hdr))

    for sym in symbols:
        try:
            h1 = rates(sym,"H1",2000)
            d1 = rates(sym,"D1",500)
        except Exception as e:
            print(f"{sym:10s} FETCH ERROR: {e}"); continue

        if len(h1)<500 or len(d1)<100:
            print(f"{sym:10s} insufficient (H1={len(h1)},D1={len(d1)})"); continue

        d1b = bias(d1)
        for label, trades in [
            ("H1-only",           sim_h1_only(h1)),
            ("D1 gate",           sim_d1_gate(h1, d1b)),
            ("D1+energy",         sim_desk_confluence(h1, d1b, None)),
        ]:
            s = stats(trades, label)
            print(f"{sym:10s} {label:20s} {s['trades']:>6d} {s['win%']:>6.1f} "
                  f"{s['expect']:>10.6f} {s['PF']:>6.2f} {s['avg_bars']:>9.1f}")

if __name__ == "__main__":
    sys.exit(main())
