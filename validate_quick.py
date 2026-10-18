"""Quick validation — 70/30 walk-forward split on top research combos."""
import json, sys, datetime, time as _t
from pathlib import Path
import urllib.request as _req
import pandas as pd, numpy as np

PROJECT = Path(r"E:\Project\Mt5 Ea")
GW = "http://127.0.0.1:8060"
VALIDATED_DIR = PROJECT / "memory" / "validated"

token = ""
for line in (PROJECT / ".env").read_text().splitlines():
    if line.startswith("WEB_AUTH_TOKEN="):
        token = line.split("=",1)[1].strip(); break

def gw_call(method, kwargs):
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW+"/call/"+method, data=body,
                       headers={"Content-Type":"application/json","X-Auth-Token":token})
    with _req.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    if data.get("error"): raise RuntimeError(data["error"])
    p = data.get("data",{})
    if isinstance(p,dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p,list) else pd.DataFrame()

def rates(sym, tf, n):
    for i in range(3):
        try: return gw_call("rates", {"symbol":sym,"timeframe":tf,"count":n})
        except: _t.sleep(2)
    raise RuntimeError(f"failed: {sym} {tf}")

def ema(s, w): return s.ewm(span=w, adjust=False).mean()
def atr(df, p=14):
    h,l,c = df["high"],df["low"],df["close"]; prev=c.shift(1)
    tr = pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(p).mean()
def bias(df, fast=50, slow=200):
    ef,es = ema(df["close"],fast),ema(df["close"],slow)
    t = pd.Series(0,index=df.index); t[ef>es]=1; t[ef<es]=-1
    rh=df["high"].rolling(20).max().shift(20); rl=df["low"].rolling(20).min().shift(20)
    bull=(t==1)&(df["close"]>rh*0.999); bear=(t==-1)&(df["close"]<rl*1.001)
    return pd.Series(0,index=df.index).mask(bull,1).mask(bear,-1)

def pip_size(sym):
    s=sym.upper().rstrip(".")
    if "JPY" in s: return 0.01
    return 0.0001

def sim(h1, entry_tf, gates, sl_tp=2.0, use_energy=False, use_zone=False, pv=0.0001):
    ef,es = ema(h1["close"],50),ema(h1["close"],200)
    a = atr(h1); atr_avg = a.rolling(50).mean()
    eb = gates.get(entry_tf)
    if eb is not None: eb = eb.reindex(h1.index,method="ffill")
    filled = {tf:s.reindex(h1.index,method="ffill") for tf,s in gates.items() if tf!=entry_tf}
    trades, in_pos, ei = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos==0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig==0: continue
            if eb is not None and eb.iloc[i]!=sig: continue
            if any(s.iloc[i]!=sig for s in filled.values()): continue
            if use_energy and pd.notna(atr_avg.iloc[i]) and atr_avg.iloc[i]>0:
                if a.iloc[i]/atr_avg.iloc[i]<1.5: continue
            if use_zone:
                rh=h1["high"].rolling(20).max().iloc[i]; rl=h1["low"].rolling(20).min().iloc[i]
                za=a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
                if sig==1 and p>rl+2*za: continue
                if sig==-1 and p<rh-2*za: continue
            entry=p; stop=max(2*a.iloc[i],0.0001)
            sl,tp=entry-sig*stop,entry+sig*sl_tp*stop
            in_pos=sig; ei=i
        else:
            hi,lo=h1["high"].iloc[i],h1["low"].iloc[i]
            if in_pos==1:
                if lo<=sl: trades.append({"pnl":(sl-entry)/pv,"bars":i-ei}); in_pos=0
                elif hi>=tp: trades.append({"pnl":(tp-entry)/pv,"bars":i-ei}); in_pos=0
            else:
                if hi>=sl: trades.append({"pnl":(entry-sl)/pv,"bars":i-ei}); in_pos=0
                elif lo<=tp: trades.append({"pnl":(entry-tp)/pv,"bars":i-ei}); in_pos=0
            if in_pos!=0 and i-ei>200:
                pnl=(p-entry if in_pos==1 else entry-p)/pv
                trades.append({"pnl":pnl,"bars":i-ei}); in_pos=0
    return trades

def stats(trades):
    n=len(trades)
    if n==0: return {"trades":0,"win%":0,"expect":0,"PF":0,"sharpe":0}
    pnls=[t["pnl"] for t in trades]
    wins=[t for t in trades if t["pnl"]>0]; losses=[t for t in trades if t["pnl"]<=0]
    gw=sum(t["pnl"] for t in wins); gl=abs(sum(t["pnl"] for t in losses))
    arr=np.array(pnls)
    sharpe=float(arr.mean()/arr.std()*np.sqrt(252)) if arr.std()>0 else 0
    return {"trades":n,"win%":round(len(wins)/n*100,1),"expect":round(arr.mean(),4),
            "PF":round(gw/gl,2) if gl else (float("inf") if gw else 0),"sharpe":round(sharpe,2)}

# load research report
reports = sorted((PROJECT/"memory"/"research").glob("research_*.json"))
with open(reports[-1]) as f:
    rep = json.load(f)
symbols = rep["symbols"]
top = [r for r in rep["results"] if r["trades"]>=10][:8]
print(f"[VALIDATE] testing top {len(top)} combos, 70/30 walk-forward")

print("loading data...")
data = {}
for sym in symbols:
    try:
        data[sym] = {"H1":rates(sym,"H1",2000),"H4":rates(sym,"H4",800),
                     "M1":rates(sym,"M1",10000),"M5":rates(sym,"M5",5000),"M15":rates(sym,"M15",3000)}
        print(f"  {sym}: ok")
    except Exception as e:
        print(f"  {sym}: ERROR {e}")

gate_map = {"none":{},"H4":{"H4":None},"H4+H1":{"H4":None,"H1":None},"D1+H4+H1":{"D1":None,"H4":None,"H1":None}}

validated = []
for r in top:
    etf, gn, st, fn = r["entry_tf"], r["gates"], r["sl_tp"], r["filters"]
    ue, uz = "energy" in fn, "zone" in fn
    gt = gate_map.get(gn, {})

    tt, ts = [], []  # train/test trades
    for sym, dfs in data.items():
        h1 = dfs["H1"]
        if len(h1)<500: continue
        split = int(len(h1)*0.7)
        h1_tr = h1.iloc[:split].copy().reset_index(drop=True)
        h1_te = h1.iloc[split:].copy().reset_index(drop=True)
        g_tr, g_te = {}, {}
        if etf in dfs:
            fb = bias(dfs[etf])
            g_tr[etf] = fb.iloc[:split]; g_te[etf] = fb.iloc[split:]
        for tf in gt:
            if tf in dfs:
                fb = bias(dfs[tf])
                g_tr[tf] = fb.iloc[:split]; g_te[tf] = fb.iloc[split:]
        pv = pip_size(sym)
        tt.extend(sim(h1_tr, etf, g_tr, st, ue, uz, pv))
        ts.extend(sim(h1_te, etf, g_te, st, ue, uz, pv))

    tr_s, te_s = stats(tt), stats(ts)
    passes = (tr_s["trades"]>=10 and te_s["trades"]>=3 and te_s["expect"]>0
              and te_s["PF"]>=0.8 and te_s["sharpe"]>0.3)
    validated.append({**r, "train":tr_s, "test":te_s, "passes":passes})
    status = "PASS" if passes else "FAIL"
    print(f"\n  {etf} {gn} {st:.1f} {fn}: {status}")
    print(f"    train: {tr_s['trades']}t {tr_s['win%']}% exp {tr_s['expect']:.2f} PF {tr_s['PF']:.2f} sh {tr_s['sharpe']:.2f}")
    print(f"    test:  {te_s['trades']}t {te_s['win%']}% exp {te_s['expect']:.2f} PF {te_s['PF']:.2f} sh {te_s['sharpe']:.2f}")

VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
today = datetime.date.today().isoformat()
vp = VALIDATED_DIR / f"validated_{today}.json"
with open(vp,"w") as f: json.dump({"date":today,"results":validated}, f, indent=2)

passed = [v for v in validated if v["passes"]]
print(f"\n[VALIDATE] {len(passed)}/{len(validated)} passed")
print(f"saved: {vp}")
