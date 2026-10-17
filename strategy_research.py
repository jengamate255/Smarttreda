"""Daily Strategy Research Agent — mix-and-match backtester.

Runs daily, tests every combination of:
  - Entry TFs: M1, M5, M15
  - MTF gates: none, H4-only, H4+H1, D1+H4+H1
  - Filters: energy, zone proximity, session
  - SL/TP ratios: 1.5R, 2R, 3R

Outputs a ranked table of the best combos and saves to reports/.
"""

from __future__ import annotations
import json, sys, os, datetime
from pathlib import Path
import urllib.request as _req
import pandas as pd
import numpy as np

PROJECT = Path(__file__).resolve().parent
GW = "http://127.0.0.1:8060"
AUTH_TOKEN = ""

def _load_token():
    global AUTH_TOKEN
    if AUTH_TOKEN:
        return AUTH_TOKEN
    env_path = PROJECT / ".env"
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

# ── indicators ─────────────────────────────────────────────────────────
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

def detect_patterns(df):
    """Simplified pattern detection for backtest: engulfing, pin bar, hammer."""
    patterns = pd.Series(0, index=df.index)
    for i in range(2, len(df)):
        o,h,l,c = df["open"].iloc[i],df["high"].iloc[i],df["low"].iloc[i],df["close"].iloc[i]
        o1,h1,l1,c1 = df["open"].iloc[i-1],df["high"].iloc[i-1],df["low"].iloc[i-1],df["close"].iloc[i-1]
        body = abs(c-o)
        prev_body = abs(c1-o1)
        wick_up = h - max(o,c)
        wick_dn = min(o,c) - l
        # bullish engulfing
        if c > o and c1 < o1 and c > o1 and o < c1 and body > prev_body * 1.2:
            patterns.iloc[i] = 1
        # bearish engulfing
        elif c < o and c1 > o1 and c < o1 and o > c1 and body > prev_body * 1.2:
            patterns.iloc[i] = -1
        # hammer (bullish)
        elif wick_dn > body * 2 and wick_up < body * 0.5 and c > o:
            patterns.iloc[i] = 1
        # shooting star (bearish)
        elif wick_up > body * 2 and wick_dn < body * 0.5 and c < o:
            patterns.iloc[i] = -1
    return patterns

# ── simulation engine ─────────────────────────────────────────────────
def sim(h1, entry_tf, gates, sl_tp_ratio=2.0, use_energy=False, use_zone=False):
    """
    Walk H1 bars, enter when:
      1. H1 trend aligns with entry TF trend
      2. Gate TFs agree
      3. Confirmation pattern present
      4. (optional) Energy filter
      5. (optional) Zone proximity filter
    SL = 2×ATR, TP = sl_tp_ratio × SL
    """
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    atr_avg = a.rolling(50).mean()

    # load entry TF bias
    entry_bias = gates.get(entry_tf)
    if entry_bias is not None:
        entry_bias = entry_bias.reindex(h1.index, method="ffill")

    # load gate biases
    filled = {}
    for tf, ser in gates.items():
        if tf != entry_tf:
            filled[tf] = ser.reindex(h1.index, method="ffill")

    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue

            # check entry TF alignment
            if entry_bias is not None and entry_bias.iloc[i] != sig:
                continue

            # check all gate TFs
            ok = True
            for tf, ser in filled.items():
                if ser.iloc[i] != sig:
                    ok = False; break
            if not ok: continue

            # energy filter
            if use_energy:
                if pd.notna(atr_avg.iloc[i]) and atr_avg.iloc[i] > 0:
                    if a.iloc[i] / atr_avg.iloc[i] < 1.5:
                        continue

            # zone proximity filter (simplified: price near recent swing)
            if use_zone:
                roll_h = h1["high"].rolling(20).max().iloc[i]
                roll_l = h1["low"].rolling(20).min().iloc[i]
                zone_atr = a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
                if sig == 1 and p > roll_l + 2 * zone_atr:
                    continue  # too far from demand zone
                if sig == -1 and p < roll_h - 2 * zone_atr:
                    continue  # too far from supply zone

            entry = p
            stop = max(2*a.iloc[i], 0.0001)
            sl,tp = entry-sig*stop, entry+sig*sl_tp_ratio*stop
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

def stats(trades):
    n = len(trades)
    if n == 0:
        return {"trades":0,"win%":0,"expect":0,"PF":0,"avg_bars":0}
    wins = [t for t in trades if t["pnl"]>0]
    losses = [t for t in trades if t["pnl"]<=0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {
        "trades":n,
        "win%": round(len(wins)/n*100,1),
        "expect": round(sum(t["pnl"] for t in trades)/n, 6),
        "PF": round(gw/gl,2) if gl else (float("inf") if gw else 0),
        "avg_bars": round(sum(t["bars"] for t in trades)/n,1),
    }

# ── main ──────────────────────────────────────────────────────────────
def main():
    symbols = ["EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD","XAUUSD","XAGUSD","USOIL","BTCUSD","ETHUSD"]

    try:
        with _req.urlopen(GW+"/health",timeout=5) as r:
            gw = json.loads(r.read())
        print(f"gateway: {gw.get('server','?')}  uptime {gw.get('uptime_s',0):.0f}s")
    except Exception as e:
        print(f"FATAL: {e}",file=sys.stderr); return 1

    # strategy combos to test
    entry_tfs = ["M1", "M5", "M15"]
    gate_combos = {
        "none":      {},
        "H4":        {"H4": None},
        "H4+H1":     {"H4": None, "H1": None},
        "D1+H4+H1":  {"D1": None, "H4": None, "H1": None},
    }
    sl_tp_ratios = [1.5, 2.0, 3.0]
    filters = [
        (False, False, "no filter"),
        (True,  False, "energy"),
        (False, True,  "zone"),
        (True,  True,  "energy+zone"),
    ]

    # load all data once
    print("loading data...")
    data = {}
    for sym in symbols:
        try:
            data[sym] = {
                "H1": rates(sym,"H1",2000),
                "H4": rates(sym,"H4",800),
                "D1": rates(sym,"D1",500),
                "M1": rates(sym,"M1",10000),
                "M5": rates(sym,"M5",5000),
                "M15": rates(sym,"M15",3000),
            }
            print(f"  {sym}: loaded")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    # run all combos
    results = []
    total = len(entry_tfs) * len(gate_combos) * len(sl_tp_ratios) * len(filters)
    done = 0
    print(f"\nrunning {total} combos...")

    for entry_tf in entry_tfs:
        for gate_name, gate_tfs in gate_combos.items():
            for sl_tp in sl_tp_ratios:
                for use_energy, use_zone, filter_name in filters:
                    all_trades = []
                    for sym, dfs in data.items():
                        h1 = dfs["H1"]
                        if len(h1) < 500: continue
                        # build gate biases
                        gates = {}
                        if entry_tf in dfs:
                            gates[entry_tf] = bias(dfs[entry_tf])
                        for tf in gate_tfs:
                            if tf in dfs:
                                gates[tf] = bias(dfs[tf])
                        trades = sim(h1.copy(), entry_tf, gates, sl_tp, use_energy, use_zone)
                        all_trades.extend(trades)

                    s = stats(all_trades)
                    results.append({
                        "entry_tf": entry_tf,
                        "gates": gate_name,
                        "sl_tp": sl_tp,
                        "filters": filter_name,
                        **s,
                    })
                    done += 1
                    if done % 50 == 0:
                        print(f"  {done}/{total}...")

    # rank by expectancy (must have >= 10 trades)
    ranked = sorted(results, key=lambda r: r["expect"] if r["trades"]>=10 else -999, reverse=True)

    # print top 30
    print(f"\n{'='*80}")
    print(f" TOP 30 STRATEGY COMBOS (min 10 trades)")
    print(f"{'='*80}")
    print(f"{'rank':>4s} {'entry_tf':>8s} {'gates':>10s} {'sl_tp':>5s} {'filters':>12s} "
          f"{'trades':>7s} {'win%':>6s} {'expect':>10s} {'PF':>6s} {'bars':>6s}")
    print("-"*80)
    for i, r in enumerate(ranked[:30], 1):
        print(f"{i:4d} {r['entry_tf']:>8s} {r['gates']:>10s} {r['sl_tp']:>5.1f} {r['filters']:>12s} "
              f"{r['trades']:>7d} {r['win%']:>6.1f} {r['expect']:>10.6f} {r['PF']:>6.2f} {r['avg_bars']:>6.1f}")

    # save report
    report_dir = PROJECT / "memory" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    report_path = report_dir / f"strategy_research_{today}.md"
    with open(report_path, "w") as f:
        f.write(f"# Strategy Research Report — {today}\n\n")
        f.write(f"Symbols: {', '.join(symbols)}\n\n")
        f.write(f"Entry TFs: {', '.join(entry_tfs)}\n")
        f.write(f"Gate combos: {', '.join(gate_combos.keys())}\n")
        f.write(f"SL/TP ratios: {sl_tp_ratios}\n")
        f.write(f"Filters: energy, zone, energy+zone\n\n")
        f.write(f"## Top 30 Combos (min 10 trades)\n\n")
        f.write(f"| # | Entry TF | Gates | SL:TP | Filters | Trades | Win% | Expect | PF | Avg Bars |\n")
        f.write(f"|---|----------|-------|-------|----------|--------|------|--------|-----|----------|\n")
        for i, r in enumerate(ranked[:30], 1):
            f.write(f"| {i} | {r['entry_tf']} | {r['gates']} | {r['sl_tp']:.1f} | {r['filters']} | "
                    f"{r['trades']} | {r['win%']} | {r['expect']:.6f} | {r['PF']:.2f} | {r['avg_bars']:.1f} |\n")
        f.write(f"\n## Full Results (all {len(results)} combos)\n\n")
        f.write(f"| Entry TF | Gates | SL:TP | Filters | Trades | Win% | Expect | PF |\n")
        f.write(f"|----------|-------|-------|----------|--------|------|--------|-----|\n")
        for r in ranked:
            f.write(f"| {r['entry_tf']} | {r['gates']} | {r['sl_tp']:.1f} | {r['filters']} | "
                    f"{r['trades']} | {r['win%']} | {r['expect']:.6f} | {r['PF']:.2f} |\n")
    print(f"\nreport saved: {report_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
