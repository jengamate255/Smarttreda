"""Matched, frozen price-rule replay. Broker access is history/metadata only.

No order, notification, journal, model, settings or service mutations. Results
are unit-R counterfactuals, not the complete live desk/account executor.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from mt5_mcp.config import get_settings, MT5Config
from mt5_mcp.chart_patterns import analyze_chart
from mt5_mcp.indicators import atr
from mt5_mcp.price_action import detect_patterns
from mt5_mcp.strategy import analyze_trend
from mt5_mcp.supply_demand import detect_zones
from mt5_mcp import entry_quality, paper_learning
from mt5_mcp.pipeline import MIN_SL_FLOOR
from mt5_mcp.indicators import swing_highs, swing_lows


def fast_market_structure(df, left=5, right=5):
    """Same pivot comparisons as production, avoiding repeated Series allocation."""
    highs,lows=swing_highs(df,left,right),swing_lows(df,left,right)
    high_values=df.high.to_numpy()[highs]
    low_values=df.low.to_numpy()[lows]
    hh=int(np.count_nonzero(high_values[1:]>high_values[:-1]))
    hl=int(np.count_nonzero(low_values[1:]>low_values[:-1]))
    return {"hh":hh,"lh":max(0,len(highs)-1)-hh,"hl":hl,"ll":max(0,len(lows)-1)-hl,"highs":highs,"lows":lows}


def epochs(frame):
    return (frame.time.astype("int64") // 10**9).to_numpy()


def stats(trades):
    values = np.array([row["net_r"] for row in trades], dtype=float)
    gains, losses = float(values[values > 0].sum()), float(-values[values < 0].sum())
    cumulative = np.r_[0., np.cumsum(values)]
    return {"trades": len(values), "wins": int((values > 0).sum()),
            "net_r": round(float(values.sum()), 5),
            "expectancy_r": round(float(values.mean()), 5) if len(values) else None,
            "profit_factor": round(gains / losses, 5) if losses else None,
            "no_losses": bool(len(values) and losses == 0),
            "max_closed_drawdown_r": round(float((np.maximum.accumulate(cumulative)-cumulative).max()), 5),
            "ambiguous": sum(row["ambiguous"] for row in trades)}


def clean_rates(data):
    if data is None or not len(data):
        raise ValueError("Broker history unavailable")
    frame = pd.DataFrame(data)
    needed = ["time", "open", "high", "low", "close", "spread", "tick_volume"]
    if not set(needed).issubset(frame):
        raise ValueError("History lacks required OHLC/spread/volume fields")
    frame = frame[needed].sort_values("time").reset_index(drop=True)
    if frame.time.duplicated().any():
        raise ValueError("Duplicate candle timestamps")
    if not np.isfinite(frame[needed].to_numpy(float)).all():
        raise ValueError("Nonfinite candle evidence")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC geometry")
    if (frame.spread < 0).any():
        raise ValueError("Negative broker spread")
    frame["time"] = pd.to_datetime(frame.time, unit="s", utc=True)
    return frame


def compare_symbol(symbol, frames, point, cfg, start, end, offset, sl_atr, tp_r, stress=1.):
    m5, h1, h4 = (frames[tf].copy() for tf in ("M5", "H1", "H4"))
    m5=m5.loc[epochs(m5)+300<=end].reset_index(drop=True)
    times, t1, t4 = epochs(m5), epochs(h1)+3600, epochs(h4)+14400
    # No future median/current quote is used to invent missing historical costs.
    m5["spread"] = m5.spread.replace(0, np.nan).ffill()*stress
    bars = [{**row, "time": int(times[i])} for i, row in enumerate(m5.to_dict("records"))]
    volatility = atr(m5).to_numpy(float)
    cache1, cache4, zones1, patterns1 = {}, {}, {}, {}
    funnel=Counter()
    branches = {name: {"trades": [], "reasons": Counter(), "until": 0, "days": set(), "checks": 0,
                       "missed": 0, "unresolved": 0} for name in ("previous", "updated")}
    original_clock, original_check = entry_quality.time, entry_quality.pattern_entry_check
    clock = {"stamp": 0}
    entry_quality.time = SimpleNamespace(time=lambda: clock["stamp"]-offset)
    try:
        for i in range(120, len(m5)-2):
            stamp = int(times[i])+300
            if not start <= stamp < end or stamp % 1800:
                continue
            funnel["scan_boundaries"]+=1
            hour = datetime.fromtimestamp(stamp-offset, timezone.utc).hour
            if cfg.risk.allowed_hours_utc and hour not in cfg.risk.allowed_hours_utc:
                continue
            funnel["allowed_hour_scans"]+=1
            n1, n4 = int(np.searchsorted(t1, stamp, side="right")), int(np.searchsorted(t4, stamp, side="right"))
            if min(n1, n4) < 200 or not np.isfinite(volatility[i]) or volatility[i] <= 0:
                continue
            if not np.isfinite(bars[i]["spread"]):
                continue
            funnel["history_atr_spread_ready"]+=1
            if n1 not in cache1:
                frame = h1.iloc[max(0, n1-400):n1].reset_index(drop=True)
                cache1[n1] = entry_quality._side(analyze_trend(frame).direction)
            if n4 not in cache4:
                cache4[n4] = entry_quality._side(analyze_trend(h4.iloc[max(0,n4-250):n4].reset_index(drop=True)).direction)
            side = cache1[n1]
            if side is None or side != cache4[n4]:
                continue
            funnel["closed_h1_h4_aligned"]+=1
            bid, spread = float(m5.close.iloc[i]), float(bars[i]["spread"])*point
            if n1 not in zones1:
                frame = h1.iloc[max(0,n1-400):n1].reset_index(drop=True)
                zones1[n1] = [{"lower": float(z.lower), "upper": float(z.upper), "fresh": bool(z.fresh),
                      "broken": bool(z.broken), "side": "BUY" if z.type == "demand" else "SELL"}
                     for z in detect_zones(frame, lookback=300)]
            zones = zones1[n1]
            # These are required by BOTH policies, independent of pattern ranking.
            # Fail them before expensive pattern analysis without changing fills.
            if not any(z["side"]==side and z["fresh"] and not z["broken"] and
                max(z["lower"]-bid,bid-z["upper"],0)<=cfg.quality.max_zone_distance_atr*volatility[i] for z in zones):
                continue
            funnel["fresh_zone_location_pass"]+=1
            candles = detect_patterns(m5.iloc[max(0,i-119):i+1].reset_index(drop=True), lookback=120, check_last=1)
            confirmation=any(entry_quality._side(c.direction)==side and c.confidence>=.6 for c in candles)
            if not confirmation:
                continue
            funnel["entry_confirmation_pass"]+=1
            if n1 not in patterns1:
                frame=h1.iloc[max(0,n1-400):n1].reset_index(drop=True)
                patterns1[n1]=analyze_chart(frame,symbol=symbol,timeframe="H1",lookback=350).get("patterns",[])
            all_patterns=patterns1[n1]
            patterns = [p for p in all_patterns if p.get("status") == "confirmed" and entry_quality._side(p.get("direction")) == side]
            if not patterns:
                continue
            funnel["confirmed_pattern_pass"]+=1
            entry = bid+spread if side == "BUY" else bid
            sign = 1 if side == "BUY" else -1
            ctx = {"symbol": symbol, "side": side, "trends": {"H1": side, "H4": cache4[n4]},
                "patterns": patterns, "zones": zones, "atr": float(volatility[i]),
                "confirmation": confirmation,
                "signal_bar": int(times[i]), "next_bar": stamp, "timeframe": "M5", "broker_time_offset_seconds": offset}
            clock["stamp"] = stamp
            day = datetime.fromtimestamp(stamp-offset,timezone.utc).strftime("%Y-%m-%d")
            for name, branch in branches.items():
                if stamp < branch["until"] or day in branch["days"]:
                    continue
                branch["checks"] += 1
                choices = patterns if name == "previous" else [p for p in patterns if original_check(p,side,entry)["allowed"]]
                if not choices:
                    branch["reasons"]["no executable confirmed pattern"] += 1
                    continue
                selected = max(choices, key=lambda p: min(100.,float(p.get("ai_score",p.get("quality_score",0)))*1.15))
                stop = max(sl_atr*volatility[i],MIN_SL_FLOOR.get(symbol,0))+spread*.5
                if selected.get("invalidation") is not None:
                    stop = max(stop,abs(entry-float(selected["invalidation"])))
                sl, tp = entry-sign*stop, entry+sign*stop*tp_r
                if name == "updated":
                    tp = min(tp,float(selected["target"])) if side == "BUY" else max(tp,float(selected["target"]))
                req = {"symbol":symbol,"action":1,"type":0 if side=="BUY" else 1,"price":entry,"sl":sl,"tp":tp,"comment":"desk:"+selected["pattern"]}
                # Baseline is the immediately preceding quality evaluator: same
                # checks except the newly added structural pattern check.
                entry_quality.pattern_entry_check = original_check if name == "updated" else lambda *a,**k: {"allowed":True,"reasons":[]}
                verdict = entry_quality.evaluate(req,ctx,{"bid":bid,"ask":bid+spread,"time":stamp})
                if not verdict["allowed"]:
                    branch["reasons"].update(verdict["violations"])
                    continue
                target_bar = stamp+300
                future = bars[i+2:min(len(bars),i+2+cfg.quality.shadow_horizon_bars)]
                if not future or future[0]["time"] != target_bar or any(not np.isfinite(b["spread"]) for b in future):
                    branch["missed"] += 1
                    continue
                # Reject incomplete/gapped outcome windows rather than inventing fills.
                if any(b["time"] != target_bar+j*300 for j,b in enumerate(future)):
                    branch["missed"] += 1
                    continue
                fill_spread = float(future[0]["spread"])*point
                fill = float(future[0]["open"])+(fill_spread if side=="BUY" else 0)
                if name == "updated" and not original_check(selected,side,fill,sl=sl,tp=tp)["allowed"]:
                    branch["missed"] += 1
                    continue
                candidate = {"status":"waiting","side":side,"entry":entry,"sl":sl,"tp":tp,"spread":spread,
                             "entry_atr":float(volatility[i]),"target_bar":target_bar,"entered_bar":None}
                settled = paper_learning.settle_candidate(candidate,future,point,cfg.quality,include_exit_bar=True)
                if not settled or settled["status"] == "missed":
                    branch["missed"] += 1
                    continue
                branch["days"].add(day)
                if settled["status"] != "closed":
                    branch["unresolved"] += 1
                    branch["until"] = end
                    continue
                branch["until"] = settled["exit_bar"]+300
                branch["trades"].append({"symbol":symbol,"side":side,"setup":selected["pattern"],
                    "decision_time":stamp-offset,"entry_time":target_bar-offset,"exit_time":branch["until"]-offset,
                    "entry":settled["entry"],"sl":sl,"tp":tp,"net_r":settled["r_multiple"],"ambiguous":settled["ambiguous"],
                    "h1_last_closed":int(t1[n1-1])-offset,"h4_last_closed":int(t4[n4-1])-offset})
    finally:
        entry_quality.time, entry_quality.pattern_entry_check = original_clock, original_check
    return {name:{**{k:v for k,v in b.items() if k not in ("until","days","reasons")},
                  "reasons":dict(b["reasons"]),"full":stats(b["trades"]),
                  "shared_funnel":{key:funnel[key] for key in ("scan_boundaries","allowed_hour_scans","history_atr_spread_ready",
                      "closed_h1_h4_aligned","fresh_zone_location_pass","entry_confirmation_pass","confirmed_pattern_pass")}}
            for name,b in branches.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"reports/entry_backtest_20261014")
    parser.add_argument("--start",default="2026-04-01")
    parser.add_argument("--cached",action="store_true")
    parser.add_argument("--resume",action="store_true",help="Reuse completed symbol rows from the same frozen run")
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    cfg = get_settings()
    disk = json.loads((ROOT/"mt5_mcp/settings.json").read_text(encoding="utf-8"))
    symbols = disk["consensus"]["allowed_symbols"]
    sl_atr, tp_r = float(os.getenv("MT5_DESK_SL_ATR","4.5")),float(os.getenv("MT5_DESK_TP_R","2.0"))
    manifest_path = args.output/"manifest.json"
    if not args.cached:
        from mt5_mcp.mt5_client import MT5Client
        connection = MT5Config(**{key:os.getenv("MT5_"+env) or os.getenv("MT5_MCP_MT5_"+env) or getattr(cfg.mt5,key)
            for key,env in (("login","LOGIN"),("password","PASSWORD"),("server","SERVER"),("terminal_path","TERMINAL_PATH"))})
        client = MT5Client(connection)
        client.order_send = lambda *a,**k: (_ for _ in ()).throw(RuntimeError("Backtest cannot send orders"))
        if not client.connect():
            raise RuntimeError("Read-only history connection unavailable")
        account = client.account_info()
        if account.get("login") != connection.login or account.get("server") != connection.server:
            raise RuntimeError("Unexpected history account")
        offset = cfg.quality.broker_time_offsets.get(account["server"])
        if offset is None:
            raise RuntimeError("Broker clock not verified")
        start_utc = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp())
        start, end = start_utc+offset,int(time.time())+offset
        manifest = {"symbols":symbols,"start":start,"end":end,"offset":offset,"frames":{},"point":{},"sl_atr":sl_atr,"tp_r":tp_r}
        try:
            import MetaTrader5 as mt5
            for symbol in symbols:
                manifest["point"][symbol] = float(client.symbol_info(symbol)["point"])
                for tf,warmup in (("M5",10), ("H1",100), ("H4",365)):
                    current = client.account_info()
                    if (current.get("login"),current.get("server")) != (account["login"],account["server"]):
                        raise RuntimeError("History account changed")
                    rates = client._call("copy_rates_range",symbol,getattr(mt5,"TIMEFRAME_"+tf),
                        datetime.fromtimestamp(start-warmup*86400,timezone.utc),datetime.fromtimestamp(end,timezone.utc))
                    frame = clean_rates(rates)
                    path = args.output/f"{symbol}_{tf}.parquet"
                    frame.to_parquet(path,index=False)
                    manifest["frames"][f"{symbol}:{tf}"]={"rows":len(frame),"first":str(frame.time.iloc[0]),"last":str(frame.time.iloc[-1]),
                        "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                    print(json.dumps({"history":symbol,"timeframe":tf,"rows":len(frame)}),flush=True)
        finally:
            client.shutdown()
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    else:
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    symbols,start,end,offset = (manifest[k] for k in ("symbols","start","end","offset"))
    holdout = start+int((end-start)*.7)
    report={"scope":"Frozen price-rule comparison; not complete live desk or account replay", "manifest":manifest,
        "frozen_policy":{"min_risk_reward":cfg.risk.min_risk_reward,"allowed_hours_utc":cfg.risk.allowed_hours_utc,
            "max_entry_drift_atr":cfg.quality.max_entry_drift_atr,"max_zone_distance_atr":cfg.quality.max_zone_distance_atr,
            "shadow_cost_r":cfg.quality.shadow_cost_r,"shadow_horizon_bars":cfg.quality.shadow_horizon_bars},
        "holdout_utc":datetime.fromtimestamp(holdout-offset,timezone.utc).isoformat(),"results":[],"failures":[],
        "live_edge_confirmed":False,"limitations":["Historical news, health, consensus and learned trust are unavailable; no values invented.",
            "Does not replay confluence/directional portfolio scoring or exit management; this isolates structural pattern selection and target checks.",
            "Desk setup blocks remain enforced live; these are paper counterfactuals, not permission to trade.",
            "One open paper trade and one entry per UTC day per symbol; no portfolio/account cap replay.",
            "Bid OHLC with broker spread and 0.05R configured extra cost; conservative stop priority for ambiguous bars.",
            "Missing spread is forward-filled from past observations only; gapped outcomes are not filled."]}
    if args.resume and (args.output/"results.json").exists():
        previous=json.loads((args.output/"results.json").read_text(encoding="utf-8"))
        if previous["manifest"]!=manifest or previous["frozen_policy"]!=report["frozen_policy"]:
            raise ValueError("Cannot resume a different data or policy snapshot")
        report["results"]=previous["results"]
    import mt5_mcp.strategy as strategy
    strategy.market_structure=fast_market_structure
    for symbol in symbols:
        if any(row["symbol"]==symbol for row in report["results"]):
            continue
        began=time.perf_counter()
        print(json.dumps({"replay_started":symbol}),flush=True)
        try:
            frames={tf:pd.read_parquet(args.output/f"{symbol}_{tf}.parquet") for tf in ("M5","H1","H4")}
            for tf,f in frames.items():
                if hashlib.sha256((args.output/f"{symbol}_{tf}.parquet").read_bytes()).hexdigest()!=manifest["frames"][f"{symbol}:{tf}"]["sha256"]:
                    raise ValueError(f"Changed cached {tf} history")
                if len(f)<300 or epochs(f)[0]>start:
                    raise ValueError(f"Incomplete requested {tf} coverage")
            row={"symbol":symbol,"comparison":compare_symbol(symbol,frames,manifest["point"][symbol],cfg,start,end,offset,manifest["sl_atr"],manifest["tp_r"])}
            for branch in row["comparison"].values():
                branch["holdout"]=stats([t for t in branch["trades"] if t["entry_time"]>=holdout-offset])
            report["results"].append(row)
            print(json.dumps({"symbol":symbol,"seconds":round(time.perf_counter()-began),
                "results":{k:{"full":v["full"],"holdout":v["holdout"]} for k,v in row["comparison"].items()}}),flush=True)
        except Exception as exc:
            report["failures"].append({"symbol":symbol,"error":str(exc)})
            print(f"Replay unavailable: {symbol}: {exc}",flush=True)
        (args.output/"results.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    report["aggregate"]={name:{"full":stats(sorted([t for row in report["results"] for t in row["comparison"][name]["trades"]],key=lambda t:t["exit_time"])),
        "holdout":stats(sorted([t for row in report["results"] for t in row["comparison"][name]["trades"] if t["entry_time"]>=holdout-offset],key=lambda t:t["exit_time"]))}
        for name in ("previous","updated")}
    (args.output/"results.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(report["aggregate"],indent=2),flush=True)
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
