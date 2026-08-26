import json, glob, os, sys

def load_dir(d):
    rows = []
    for f in glob.glob(os.path.join(d, "*.json")):
        try:
            d_ = json.loads(open(f).read())
            p = d_.get("params", {})
            name = "|".join([
                p.get("symbol","?"),
                p.get("trend_timeframe","?"),
                "+".join(p.get("zone_timeframes",[]) or []),
                p.get("confirmation_timeframe","?"),
                "z"+str(p.get("min_zone_score",0)),
                "rr"+str(p.get("min_rr",0)),
                str(p.get("tp_min",0))+"-"+str(p.get("tp_max",0)),
                "b"+str(p.get("bars", d_.get("bars","?")))
            ])
            rows.append({
                "name": name,
                "symbol": p.get("symbol","?"),
                "n": d_.get("n_trades", d_.get("nTrades",0)),
                "win": d_.get("win_rate", d_.get("winRate",0)),
                "pf": d_.get("profit_factor", d_.get("profitFactor",0)),
                "ret": d_.get("total_return_pct", d_.get("totalReturnPct",0)),
                "dd": d_.get("max_drawdown_pct", d_.get("maxDrawdownPct",0)),
                "exp": d_.get("expectancy",0),
                "bars": d_.get("bars", d_.get("n_bars","?")),
            })
        except Exception as e:
            print(f"ERR {f}: {e}")
    return rows

dirs = [
    r"E:\Project\Mt5 Ea\backtest\results",
    r"E:\Project\Mt5 Ea\mt5_mcp\backtest\results",
]
all_rows = []
for d in dirs:
    all_rows += load_dir(d)

seen = set()
rows = []
for r in all_rows:
    k = r["name"]
    if k in seen:
        continue
    seen.add(k)
    rows.append(r)

rows.sort(key=lambda r: (r["symbol"], r["name"]))

cur = None
for r in rows:
    if r["symbol"] != cur:
        cur = r["symbol"]
        print(f"\n=== {cur} ===")
    print(f"  {r['name']}")
    print(f"    trades={r['n']}  win={r['win']}%  PF={r['pf']}  ret={r['ret']}%  maxDD={r['dd']}%  exp={r['exp']}")
