"""Frozen price-rule counterfactual, not a claim to replay the full live executor.

Reuses live quality checks and the tested paper fill engine. Never sends orders,
updates the journal, publishes a model, or changes symbol/setup restrictions.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/".env",override=False)
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_mcp.config import get_settings, MT5Config, PerformanceRiskConfig
from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.chart_patterns import analyze_chart
from mt5_mcp.supply_demand import detect_zones
from mt5_mcp.strategy import analyze_trend
from mt5_mcp.price_action import detect_patterns
from mt5_mcp.indicators import atr
from mt5_mcp import entry_quality, paper_learning, performance_risk
from mt5_mcp.pipeline import MIN_SL_FLOOR

OUT = ROOT/"reports/quality_price_rules_20261013"
OUT.mkdir(parents=True,exist_ok=True)
cfg = get_settings()
connection = MT5Config(**{key:os.getenv("MT5_"+env) or os.getenv("MT5_MCP_MT5_"+env) or getattr(cfg.mt5,key)
    for key,env in (("login","LOGIN"),("password","PASSWORD"),("server","SERVER"),("terminal_path","TERMINAL_PATH"))})
if not connection.is_configured:
    raise RuntimeError("Backtest connection not configured")
client = MT5Client(connection)
if not client.connect():
    raise RuntimeError("Read-only backtest connection unavailable")
account = client.account_info()
if account["login"] != connection.login:
    raise RuntimeError("Unexpected backtest account")
offset = cfg.quality.broker_time_offsets.get(account["server"],0)
START = int(datetime(2026,4,1,tzinfo=timezone.utc).timestamp())
end_stamp = int(client.tick("EURUSD")["time"])
HOLDOUT = START + int((end_stamp-START)*.7)
policy = PerformanceRiskConfig(enabled=True)
clock = {"stamp":0}
entry_quality.time = SimpleNamespace(time=lambda:clock["stamp"]-offset)
sl_atr = float(os.getenv("MT5_DESK_SL_ATR","4.5"))
tp_r = float(os.getenv("MT5_DESK_TP_R","2.0"))
symbols = json.loads((ROOT/"mt5_mcp/settings.json").read_text(encoding="utf-8"))["consensus"]["allowed_symbols"]


def frame(symbol,tf,start):
    if client.account_info()["login"] != account["login"]:
        raise RuntimeError("Account changed during history fetch")
    data = mt5.copy_rates_range(symbol,getattr(mt5,"TIMEFRAME_"+tf),
        datetime.fromtimestamp(start,timezone.utc),datetime.fromtimestamp(end_stamp,timezone.utc))
    if data is None or len(data)<300:
        raise RuntimeError(f"Insufficient real {symbol} {tf} history")
    result = pd.DataFrame(data).sort_values("time").drop_duplicates("time")
    result["time"] = pd.to_datetime(result["time"],unit="s",utc=True)
    return result.reset_index(drop=True)


def stats(rows):
    values = np.array([r["net_r"] for r in rows],dtype=float)
    gp,gl = float(values[values>0].sum()),float(-values[values<0].sum())
    return {"trades":len(rows),"net_r":round(float(values.sum()),3),
        "mean_r":round(float(values.mean()),3) if len(rows) else None,
        "pf":round(gp/gl,3) if gl else None,"wins":int((values>0).sum()),
        "ambiguous":sum(r["ambiguous"] for r in rows)}


def replay(symbol):
    m5 = frame(symbol,"M5",START-10*86400)
    h1 = frame(symbol,"H1",START-180*86400)
    h4 = frame(symbol,"H4",START-365*86400)
    info = client.symbol_info(symbol)
    point = float(info["point"])
    fallback = max(1,float(info.get("spread") or 1))
    m5["spread"] = m5["spread"].replace(0,np.nan).ffill().fillna(fallback)
    times = (m5["time"].astype("int64")//10**9).to_numpy()
    t1 = (h1["time"].astype("int64")//10**9).to_numpy()+3600
    t4 = (h4["time"].astype("int64")//10**9).to_numpy()+14400
    at = atr(m5).to_numpy(float)
    bars = [{**row,"time":int(times[i])} for i,row in enumerate(m5.to_dict("records"))]
    rows, misses, checks = [],0,0
    h1_cache,h4_cache = {},{}
    available = 0
    for i in range(300,len(m5)-cfg.quality.shadow_horizon_bars-2):
        stamp = int(times[i])+300
        if stamp < START or stamp < available or stamp % 1800:
            continue
        hour = datetime.fromtimestamp(stamp-offset,timezone.utc).hour
        if cfg.risk.allowed_hours_utc and hour not in cfg.risk.allowed_hours_utc:
            continue
        n1,n4 = int(np.searchsorted(t1,stamp,side="right")),int(np.searchsorted(t4,stamp,side="right"))
        if min(n1,n4)<200 or not np.isfinite(at[i]) or at[i]<=0:
            continue
        if n1 not in h1_cache:
            prefix = h1.iloc[max(0,n1-400):n1].reset_index(drop=True)
            h1_cache[n1] = (entry_quality._side(analyze_trend(prefix).direction),
                analyze_chart(prefix,symbol=symbol,timeframe="H1",lookback=350).get("patterns",[]),
                [{"lower":float(z.lower),"upper":float(z.upper),"fresh":bool(z.fresh),
                  "broken":bool(z.broken),"side":"BUY" if z.type=="demand" else "SELL"}
                 for z in detect_zones(prefix,lookback=300)])
        if n4 not in h4_cache:
            h4_cache[n4] = entry_quality._side(analyze_trend(h4.iloc[max(0,n4-250):n4].reset_index(drop=True)).direction)
        side,patterns,zones = h1_cache[n1]
        if side is None or h4_cache[n4] != side:
            continue
        confirmed = [p for p in patterns if p.get("status")=="confirmed" and entry_quality._side(p.get("direction"))==side]
        bid = float(m5.iloc[i]["close"])
        if not confirmed or not any(z["side"]==side and z["fresh"] and not z["broken"] and
            max(z["lower"]-bid,bid-z["upper"],0)<=cfg.quality.max_zone_distance_atr*at[i] for z in zones):
            continue
        selected = max(confirmed,key=lambda p:p.get("ai_score",p.get("quality_score",0)))
        candles = detect_patterns(m5.iloc[i-119:i+1].reset_index(drop=True),lookback=120,check_last=1)
        spread = float(m5.iloc[i]["spread"])*point
        entry = bid+spread if side=="BUY" else bid
        stop = max(sl_atr*at[i],MIN_SL_FLOOR.get(symbol,0))+spread*.5
        if selected.get("invalidation") is not None:
            stop = max(stop,abs(entry-float(selected["invalidation"])))
        sign = 1 if side=="BUY" else -1
        request = {"symbol":symbol,"action":1,"type":0 if side=="BUY" else 1,"price":entry,
                   "sl":entry-sign*stop,"tp":entry+sign*stop*tp_r,"comment":"desk:"+selected["pattern"]}
        context = {"side":side,"trends":{"H1":side,"H4":h4_cache[n4]},"patterns":confirmed,
            "confirmation":any(entry_quality._side(c.direction)==side and c.confidence>=.6 for c in candles),
            "zones":zones,"atr":float(at[i]),"signal_bar":int(times[i]),"next_bar":stamp,"timeframe":"M5",
            "broker_time_offset_seconds":offset}
        clock["stamp"] = stamp
        checks += 1
        verdict = entry_quality.evaluate(request,context,{"bid":bid,"ask":bid+spread,"time":stamp})
        if not verdict["allowed"]:
            continue
        target = stamp+300
        entry_hour = datetime.fromtimestamp(target-offset,timezone.utc).hour
        if cfg.risk.allowed_hours_utc and entry_hour not in cfg.risk.allowed_hours_utc:
            continue
        candidate = {"status":"waiting","side":side,"entry":entry,"sl":request["sl"],"tp":request["tp"],
                     "spread":spread,"entry_atr":float(at[i]),"target_bar":target,"entered_bar":None}
        result = paper_learning.settle_candidate(candidate,bars[i+2:i+2+cfg.quality.shadow_horizon_bars],point,
                                                 cfg.quality,include_exit_bar=True)
        if not result or result["status"] != "closed":
            misses += 1
            continue
        available = result["exit_bar"]+300
        rows.append({"symbol":symbol,"side":side,"entry_time":target-offset,"exit_time":available-offset,
            "entry":result["entry"],"sl":request["sl"],"net_r":result["r_multiple"],"ambiguous":result["ambiguous"],
            "spread":spread,"setup":selected["pattern"]})
    return {"symbol":symbol,"bars":len(m5),"checks":checks,"missed":misses,"full":stats(rows),
            "holdout":stats([r for r in rows if r["entry_time"]>=HOLDOUT-offset]),"trades":rows,"info":
            {k:info.get(k) for k in ("trade_tick_value","trade_tick_size","volume_min","volume_max","volume_step")}}


def equity_curve(trades,mode,info_by_symbol):
    equity,peak,dd = 10000.0,10000.0,0.0
    completed,queue = [],[]
    accepted,minimum_skips = 0,0
    for trade in sorted(trades,key=lambda row:(row["entry_time"],row["symbol"])):
        due = sorted([item for item in queue if item[0]<=trade["entry_time"]])
        queue = [item for item in queue if item[0]>trade["entry_time"]]
        for closed,net in due:
            equity += net
            completed.append({"net_pnl":net,"closed_at":closed})
            peak = max(peak,equity)
            dd = max(dd,(peak-equity)/peak*100)
        pct = performance_risk.decide(completed,policy)["risk_percent"] if mode=="adaptive" else float(mode)
        budget = max(0,equity)*pct/100
        info = info_by_symbol[trade["symbol"]]
        per_unit = float(info.get("trade_tick_value") or 0)/float(info.get("trade_tick_size") or 1)
        step = float(info.get("volume_step") or .01)
        distance = abs(trade["entry"]-trade["sl"])
        loss_per_lot = (distance+trade["spread"])*per_unit
        lots = np.floor(min(budget/max(loss_per_lot,1e-12),float(info.get("volume_max") or 100))/step+1e-10)*step
        if lots < float(info.get("volume_min") or .01) or loss_per_lot<=0:
            minimum_skips += 1
            continue
        accepted += 1
        queue.append((trade["exit_time"],trade["net_r"]*distance*per_unit*lots))
    for closed,net in sorted(queue):
        equity += net
        peak = max(peak,equity)
        dd = max(dd,(peak-equity)/peak*100)
    return {"final_equity":round(equity,2),"closed_equity_max_dd_percent":round(dd,3),"accepted":accepted,
            "minimum_lot_skips":minimum_skips,"scope":"Sizing illustration; not a full portfolio/compliance/mark-to-market replay"}


results,failures = [],[]
for symbol in symbols:
    began = time.perf_counter()
    try:
        row = replay(symbol)
        results.append(row)
        print(json.dumps({"symbol":symbol,"full":row["full"],"holdout":row["holdout"],"seconds":round(time.perf_counter()-began)}),flush=True)
    except Exception as exc:
        failures.append({"symbol":symbol,"error":str(exc)})
        print(f"Unavailable history/replay: {symbol}: {exc}",flush=True)
    (OUT/"results.json").write_text(json.dumps({"results":results,"failures":failures},indent=2),encoding="utf-8")
trades = [trade for row in results for trade in row["trades"]]
report = {"scope":"Frozen entry-quality price-rule counterfactual, not full live executor",
    "period_from":"2026-04-01","holdout_broker_epoch":HOLDOUT,"broker_offset_assumption":offset,
    "full":stats(trades),"holdout":stats([r for r in trades if r["entry_time"]>=HOLDOUT-offset]),
    "equity_curves":{mode:equity_curve(trades,mode,{r["symbol"]:r["info"] for r in results}) for mode in ("0.1","1.0","adaptive")},
    "results":results,"failures":failures,"limitations":["No historical news/health/consensus replay",
    "No confidence/confluence or live management replay","Fixed current broker contract metadata",
    "Additional assumed cost 0.05R; adverse gaps and ambiguous bars conservative","Explicit live desk blocks remain in place"],
    "live_edge_confirmed":False}
(OUT/"results.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k not in ("results","failures")},indent=2),flush=True)
