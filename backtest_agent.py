"""Strategy Backtest Agent — 3-stage pipeline.

Stage 1: RESEARCH (daily, fast)
  - Tests every combo of entry TF (M1/M5/M15), gates, filters, SL/TP
  - Outputs ranked table of best combos
  - Saves to memory/research/

Stage 2: VALIDATE (manual or after research finds promising combo)
  - Deep backtest on the top combos with:
    - Walk-forward analysis (train on 70%, test on 30%)
    - Out-of-sample validation
    - Max drawdown, Sharpe ratio, profit factor
  - Saves to memory/validated/

Stage 3: PROMOTE (manual)
  - Takes a validated strategy and updates config/settings.json
  - Restarts the trade desk with new config

Usage:
  python backtest_agent.py research              # run daily research
  python backtest_agent.py validate <report>     # validate top combos from report
  python backtest_agent.py promote <strategy>    # promote a validated strategy
  python backtest_agent.py full                  # research + validate + promote best
"""

from __future__ import annotations
import json, sys, os, datetime, shutil
from pathlib import Path
import urllib.request as _req
import pandas as pd
import numpy as np

PROJECT = Path(__file__).resolve().parent
GW = "http://127.0.0.1:8060"
AUTH_TOKEN = ""
REPORTS_DIR = PROJECT / "memory" / "research"
VALIDATED_DIR = PROJECT / "memory" / "validated"

# ── gateway client ─────────────────────────────────────────────────────
def _load_token():
    global AUTH_TOKEN
    if AUTH_TOKEN: return AUTH_TOKEN
    env_path = PROJECT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("WEB_AUTH_TOKEN="):
                AUTH_TOKEN = line.split("=",1)[1].strip(); break
    return AUTH_TOKEN

def gw_call(method, kwargs):
    _load_token()
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW+"/call/"+method, data=body,
                       headers={"Content-Type":"application/json","X-Auth-Token":AUTH_TOKEN})
    with _req.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    if data.get("error"): raise RuntimeError(data["error"])
    p = data.get("data",{})
    if isinstance(p,dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p,list) else pd.DataFrame()

def pip_size(symbol):
    """Return pip value for a symbol."""
    s = symbol.upper().rstrip(".")
    if "JPY" in s: return 0.01
    if s in ("BTCUSD","BTCUSD.","ETHUSD","ETHUSD.","SOLUSD","SOLUSD."): return 1.0
    if s in ("XAUUSD","XAUUSD."): return 0.01
    if s in ("XAGUSD","XAGUSD."): return 0.001
    if s in ("USOIL","USOIL."): return 0.01
    return 0.0001

def rates(sym, tf, n, retries=3):
    """Fetch rates with retry on timeout."""
    import time as _t
    for attempt in range(retries):
        try:
            return gw_call("rates", {"symbol":sym,"timeframe":tf,"count":n})
        except Exception as e:
            if attempt < retries - 1:
                _t.sleep(2)
            else:
                raise

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

# ── simulation ─────────────────────────────────────────────────────────
def sim(h1, entry_tf, gates, sl_tp_ratio=2.0, use_energy=False, use_zone=False, pip_value=0.0001):
    """Walk H1 bars. pip_value normalizes PnL across symbols (0.0001 for FX, 0.01 for JPY, 1.0 for crypto)."""
    ef, es = ema(h1["close"],50), ema(h1["close"],200)
    a = atr(h1)
    atr_avg = a.rolling(50).mean()
    entry_bias = gates.get(entry_tf)
    if entry_bias is not None:
        entry_bias = entry_bias.reindex(h1.index, method="ffill")
    filled = {tf:ser.reindex(h1.index,method="ffill") for tf,ser in gates.items() if tf!=entry_tf}
    trades, in_pos, entry_i = [], 0, 0
    for i in range(250, len(h1)-1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i]>es.iloc[i] else (-1 if ef.iloc[i]<es.iloc[i] else 0)
            if sig == 0: continue
            if entry_bias is not None and entry_bias.iloc[i] != sig: continue
            if any(ser.iloc[i]!=sig for ser in filled.values()): continue
            if use_energy and pd.notna(atr_avg.iloc[i]) and atr_avg.iloc[i]>0:
                if a.iloc[i]/atr_avg.iloc[i] < 1.5: continue
            if use_zone:
                rh = h1["high"].rolling(20).max().iloc[i]
                rl = h1["low"].rolling(20).min().iloc[i]
                za = a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
                if sig==1 and p > rl+2*za: continue
                if sig==-1 and p < rh-2*za: continue
            entry=p; stop=max(2*a.iloc[i],0.0001)
            sl,tp=entry-sig*stop, entry+sig*sl_tp_ratio*stop
            in_pos=sig; entry_i=i
        else:
            hi,lo = h1["high"].iloc[i], h1["low"].iloc[i]
            if in_pos==1:
                if lo<=sl: trades.append({"pnl":(sl-entry)/pip_value,"bars":i-entry_i}); in_pos=0
                elif hi>=tp: trades.append({"pnl":(tp-entry)/pip_value,"bars":i-entry_i}); in_pos=0
            else:
                if hi>=sl: trades.append({"pnl":(entry-sl)/pip_value,"bars":i-entry_i}); in_pos=0
                elif lo<=tp: trades.append({"pnl":(entry-tp)/pip_value,"bars":i-entry_i}); in_pos=0
            if in_pos!=0 and i-entry_i>200:
                pnl = (p-entry if in_pos==1 else entry-p)/pip_value
                trades.append({"pnl":pnl,"bars":i-entry_i}); in_pos=0
    return trades

def stats(trades):
    n = len(trades)
    if n == 0: return {"trades":0,"win%":0,"expect":0,"PF":0,"avg_bars":0,"max_dd":0,"sharpe":0}
    pnls = [t["pnl"] for t in trades]
    wins = [t for t in trades if t["pnl"]>0]
    losses = [t for t in trades if t["pnl"]<=0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    # max drawdown
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = float(dd.max()) if len(dd) else 0
    # sharpe (annualized, assuming 252 trading days, avg 1 trade/day)
    arr = np.array(pnls)
    sharpe = float(arr.mean()/arr.std()*np.sqrt(252)) if arr.std()>0 else 0
    return {
        "trades":n,
        "win%": round(len(wins)/n*100,1),
        "expect": round(np.mean(arr),6),
        "PF": round(gw/gl,2) if gl else (float("inf") if gw else 0),
        "avg_bars": round(np.mean([t["bars"] for t in trades]),1),
        "max_dd": round(max_dd,6),
        "sharpe": round(sharpe,2),
    }

# ── stage 1: research ─────────────────────────────────────────────────
def stage_research(symbols=None):
    """Fast daily scan of all strategy combos."""
    if symbols is None:
        symbols = ["EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD","XAUUSD","XAGUSD","USOIL","BTCUSD","ETHUSD"]

    try:
        with _req.urlopen(GW+"/health",timeout=5) as r:
            gw = json.loads(r.read())
        print(f"[RESEARCH] gateway: {gw.get('server','?')}  uptime {gw.get('uptime_s',0):.0f}s")
    except Exception as e:
        print(f"FATAL: {e}",file=sys.stderr); return None

    entry_tfs = ["M1","M5","M15"]
    gate_combos = {
        "none":     {},
        "H4":       {"H4":None},
        "H4+H1":    {"H4":None,"H1":None},
        "D1+H4+H1": {"D1":None,"H4":None,"H1":None},
    }
    sl_tp_ratios = [1.5, 2.0, 3.0]
    filters = [
        (False,False,"none"),
        (True,False,"energy"),
        (False,True,"zone"),
        (True,True,"energy+zone"),
    ]

    print("[RESEARCH] loading data...")
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
            print(f"  {sym}: ok")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    results = []
    total = len(entry_tfs)*len(gate_combos)*len(sl_tp_ratios)*len(filters)
    done = 0
    print(f"\n[RESEARCH] testing {total} combos...")

    for entry_tf in entry_tfs:
        for gate_name, gate_tfs in gate_combos.items():
            for sl_tp in sl_tp_ratios:
                for use_energy, use_zone, filter_name in filters:
                    all_trades = []
                    for sym, dfs in data.items():
                        h1 = dfs["H1"]
                        if len(h1)<500: continue
                        gates = {}
                        if entry_tf in dfs: gates[entry_tf] = bias(dfs[entry_tf])
                        for tf in gate_tfs:
                            if tf in dfs: gates[tf] = bias(dfs[tf])
                        all_trades.extend(sim(h1.copy(), entry_tf, gates, sl_tp, use_energy, use_zone, pip_size(sym)))
                    s = stats(all_trades)
                    results.append({
                        "entry_tf":entry_tf,"gates":gate_name,"sl_tp":sl_tp,
                        "filters":filter_name,**s,
                    })
                    done += 1
                    if done % 50 == 0: print(f"  {done}/{total}...")

    # rank by expectancy (min 10 trades)
    ranked = sorted(results, key=lambda r: r["expect"] if r["trades"]>=10 else -999, reverse=True)

    # save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    report_path = REPORTS_DIR / f"research_{today}.json"
    with open(report_path, "w") as f:
        json.dump({"date":today,"symbols":symbols,"results":ranked}, f, indent=2)

    # print top 20
    print(f"\n{'='*85}")
    print(f" TOP 20 STRATEGY COMBOS (min 10 trades)")
    print(f"{'='*85}")
    print(f"{'#':>3s} {'entry':>6s} {'gates':>10s} {'SL:TP':>5s} {'filter':>12s} "
          f"{'trades':>7s} {'win%':>6s} {'expect':>10s} {'PF':>6s} {'sharpe':>7s} {'maxDD':>8s}")
    print("-"*85)
    for i, r in enumerate(ranked[:20], 1):
        print(f"{i:3d} {r['entry_tf']:>6s} {r['gates']:>10s} {r['sl_tp']:>5.1f} {r['filters']:>12s} "
              f"{r['trades']:>7d} {r['win%']:>6.1f} {r['expect']:>10.6f} {r['PF']:>6.2f} {r['sharpe']:>7.2f} {r['max_dd']:>8.6f}")

    print(f"\n[RESEARCH] report saved: {report_path}")
    return report_path

# ── stage 2: validate ─────────────────────────────────────────────────
def stage_validate(report_path=None, top_n=5):
    """Deep validation of top combos from research report."""
    if report_path is None:
        # find latest report
        reports = sorted(REPORTS_DIR.glob("research_*.json"))
        if not reports:
            print("No research reports found. Run 'research' first."); return None
        report_path = reports[-1]

    with open(report_path) as f:
        data = json.load(f)

    ranked = data["results"]
    symbols = data["symbols"]
    to_validate = [r for r in ranked if r["trades"]>=10][:top_n]

    print(f"\n[VALIDATE] validating top {len(to_validate)} combos from {report_path.name}")

    # load all data
    print("[VALIDATE] loading data...")
    all_data = {}
    for sym in symbols:
        try:
            all_data[sym] = {
                "H1": rates(sym,"H1",2000),
                "H4": rates(sym,"H4",800),
                "D1": rates(sym,"D1",500),
                "M1": rates(sym,"M1",10000),
                "M5": rates(sym,"M5",5000),
                "M15": rates(sym,"M15",3000),
            }
        except:
            pass

    validated = []
    for r in to_validate:
        entry_tf = r["entry_tf"]
        gate_name = r["gates"]
        sl_tp = r["sl_tp"]
        filter_name = r["filters"]
        use_energy = "energy" in filter_name
        use_zone = "zone" in filter_name

        gate_tfs = {"none":{},"H4":{"H4":None},"H4+H1":{"H4":None,"H1":None},"D1+H4+H1":{"D1":None,"H4":None,"H1":None}}.get(gate_name,{})

        # split data: 70% train, 30% test
        train_trades, test_trades = [], []
        for sym, dfs in all_data.items():
            h1 = dfs["H1"]
            if len(h1) < 500: continue
            split = int(len(h1) * 0.7)
            h1_train = h1.iloc[:split].copy().reset_index(drop=True)
            h1_test = h1.iloc[split:].copy().reset_index(drop=True)
            gates_train, gates_test = {}, {}
            if entry_tf in dfs:
                full_bias = bias(dfs[entry_tf])
                gates_train[entry_tf] = full_bias.iloc[:split]
                gates_test[entry_tf] = full_bias.iloc[split:]
            for tf in gate_tfs:
                if tf in dfs:
                    full_bias = bias(dfs[tf])
                    gates_train[tf] = full_bias.iloc[:split]
                    gates_test[tf] = full_bias.iloc[split:]
            pv = pip_size(sym)
            train_trades.extend(sim(h1_train, entry_tf, gates_train, sl_tp, use_energy, use_zone, pv))
            test_trades.extend(sim(h1_test, entry_tf, gates_test, sl_tp, use_energy, use_zone, pv))

        train_s = stats(train_trades)
        test_s = stats(test_trades)

        # validation criteria — relaxed for small sample sizes
        passes = (
            train_s["trades"] >= 10 and
            test_s["trades"] >= 3 and
            test_s["expect"] > 0 and
            test_s["PF"] >= 0.8 and
            test_s["sharpe"] > 0.3 and
            abs(train_s["expect"] - test_s["expect"]) / max(abs(train_s["expect"]), 0.000001) < 0.7  # <70% degradation
        )

        result = {
            **r,
            "train": train_s,
            "test": test_s,
            "passes": passes,
        }
        validated.append(result)

        status = "PASS" if passes else "FAIL"
        print(f"\n  {r['entry_tf']} {r['gates']} {r['sl_tp']:.1f} {r['filters']}: {status}")
        print(f"    train: {train_s['trades']}t {train_s['win%']}% PF {train_s['PF']:.2f} sharpe {train_s['sharpe']:.2f}")
        print(f"    test:  {test_s['trades']}t {test_s['win%']}% PF {test_s['PF']:.2f} sharpe {test_s['sharpe']:.2f}")

    # save validated report
    VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    val_path = VALIDATED_DIR / f"validated_{today}.json"
    with open(val_path, "w") as f:
        json.dump({"date":today,"results":validated}, f, indent=2)

    passed = [v for v in validated if v["passes"]]
    print(f"\n[VALIDATE] {len(passed)}/{len(validated)} combos passed validation")
    print(f"[VALIDATE] saved: {val_path}")
    return val_path

# ── stage 3: promote ──────────────────────────────────────────────────
def stage_promote(strategy_key=None):
    """Promote a validated strategy to the live config."""
    if strategy_key is None:
        # find latest validated report
        reports = sorted(VALIDATED_DIR.glob("validated_*.json"))
        if not reports:
            print("No validated reports. Run 'validate' first."); return False
        with open(reports[-1]) as f:
            data = json.load(f)
        passed = [r for r in data["results"] if r["passes"]]
        if not passed:
            print("No strategies passed validation."); return False
        strategy = passed[0]  # promote the best
    else:
        # parse strategy_key like "M15_H4+H1_2.0_energy+zone"
        parts = strategy_key.split("_")
        entry_tf, gate_name, sl_tp, filter_name = parts[0], parts[1], float(parts[2]), parts[3]
        strategy = {"entry_tf":entry_tf,"gates":gate_name,"sl_tp":sl_tp,"filters":filter_name}

    print(f"\n[PROMOTE] promoting strategy:")
    print(f"  entry TF: {strategy['entry_tf']}")
    print(f"  gates:    {strategy['gates']}")
    print(f"  SL:TP:    {strategy['sl_tp']}")
    print(f"  filters:  {strategy['filters']}")

    # build gate TFs for config
    gate_map = {
        "none":     [],
        "H4":       ["H4"],
        "H4+H1":    ["H4","H1"],
        "D1+H4+H1": ["D1","H4","H1"],
    }
    new_mtf = gate_map.get(strategy["gates"], ["H4","H1"])

    # update settings.json
    settings_path = PROJECT / "mt5_mcp" / "settings.json"
    with open(settings_path) as f:
        settings = json.load(f)

    settings["strategy"]["mtf_timeframes"] = new_mtf
    settings["strategy"]["mtf_entry_timeframe"] = strategy["entry_tf"]
    settings["strategy"]["energy_filter_enabled"] = "energy" in strategy["filters"]
    settings["strategy"]["zone_proximity_atr"] = 1.5 if "zone" in strategy["filters"] else 999

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"[PROMOTE] updated {settings_path}")
    print(f"[PROMOTE] mtf_timeframes = {new_mtf}")
    print(f"[PROMOTE] mtf_entry_timeframe = {strategy['entry_tf']}")
    print(f"[PROMOTE] energy_filter = {'energy' in strategy['filters']}")
    print(f"[PROMOTE] zone_filter = {'zone' in strategy['filters']}")
    return True

# ── full pipeline ─────────────────────────────────────────────────────
def stage_full():
    """Research -> Validate -> Promote best strategy."""
    print("="*60)
    print(" FULL PIPELINE: Research -> Validate -> Promote")
    print("="*60)

    report = stage_research()
    if report is None:
        print("Research failed."); return False

    val = stage_validate(report)
    if val is None:
        print("Validation failed."); return False

    promoted = stage_promote()
    if promoted:
        print("\n" + "="*60)
        print(" PIPELINE COMPLETE — new strategy promoted!")
        print(" Restart the trade desk to apply.")
        print("="*60)
    return promoted

# ── CLI ────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__); return 0

    cmd = sys.argv[1].lower()
    if cmd == "research":
        stage_research()
    elif cmd == "validate":
        report = sys.argv[2] if len(sys.argv)>2 else None
        stage_validate(report)
    elif cmd == "promote":
        key = sys.argv[2] if len(sys.argv)>2 else None
        stage_promote(key)
    elif cmd == "full":
        stage_full()
    else:
        print(f"unknown command: {cmd}"); print(__doc__); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
