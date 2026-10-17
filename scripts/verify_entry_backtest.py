"""Independently recompute saved OHLC fills and accounting, without broker access."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pandas as pd


def verify_trade(trade, frame, point, offset, cost_r, horizon):
    stamp = (frame.time.astype("int64")//10**9).to_numpy()
    start, end = trade["entry_time"]+offset,trade["exit_time"]+offset
    window = frame.loc[(stamp>=start)&(stamp<end)].copy()
    assert len(window) and int(window.time.iloc[0].timestamp())==start, "Missing entry bar"
    assert len(window)<=horizon, "Trade exceeds configured horizon"
    assert trade["h1_last_closed"]<=trade["decision_time"] and trade["h4_last_closed"]<=trade["decision_time"], "Lookahead candle"
    assert trade["decision_time"]<trade["entry_time"]<trade["exit_time"], "Invalid event ordering"
    buy = trade["side"]=="BUY"
    entry = float(window.open.iloc[0])+(float(window.spread.iloc[0])*point if buy else 0)
    assert abs(entry-trade["entry"])<=max(1.,abs(entry))*1e-10, "Entry price mismatch"
    risk = abs(entry-trade["sl"])
    assert risk>0, "Missing price risk"
    expected = None
    for i,row in enumerate(window.itertuples()):
        spread = float(row.spread)*point
        high,low = float(row.high)+(spread if not buy else 0),float(row.low)+(spread if not buy else 0)
        stopped = low<=trade["sl"] if buy else high>=trade["sl"]
        targeted = high>=trade["tp"] if buy else low<=trade["tp"]
        if stopped or targeted or i+1==horizon:
            opening = float(row.open)+(spread if not buy else 0)
            price = (min(trade["sl"],opening) if buy else max(trade["sl"],opening)) if stopped else trade["tp"] if targeted else float(row.close)+(spread if not buy else 0)
            expected = (1 if buy else -1)*(price-entry)/risk-cost_r
            assert i==len(window)-1,"Earlier exit omitted"
            assert int(stopped and targeted)==trade["ambiguous"], "Ambiguity mismatch"
            break
    assert expected is not None, "No verifiable exit"
    assert abs(expected-trade["net_r"])<1e-9,"Net R mismatch"
    return expected


def verify(directory):
    report=json.loads((directory/"results.json").read_text(encoding="utf-8"))
    manifest=report["manifest"]
    policy=report.get("frozen_policy",{})
    cost_r=policy.get("shadow_cost_r",.05)
    horizon=policy.get("shadow_horizon_bars",72)
    errors, verified, checked_frames=[],0,0
    if report.get("failures"):
        errors.append("Replay contains failed symbols")
    if {row["symbol"] for row in report["results"]}!=set(manifest["symbols"]):
        errors.append("Replay does not cover the complete frozen symbol universe")
    totals={"previous":[],"updated":[]}
    for row in report["results"]:
        symbol=row["symbol"]
        for tf in ("M5","H1","H4"):
            path=directory/f"{symbol}_{tf}.parquet"
            if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest["frames"][f"{symbol}:{tf}"]["sha256"]:
                errors.append(f"{symbol} {tf}: cached history hash differs")
            data=pd.read_parquet(path)
            if len(data)!=manifest["frames"][f"{symbol}:{tf}"]["rows"] or data.time.duplicated().any() or not data.time.is_monotonic_increasing:
                errors.append(f"{symbol} {tf}: row count or timestamp integrity failed")
            if (data.high<data[["open","close","low"]].max(axis=1)).any() or (data.low>data[["open","close","high"]].min(axis=1)).any():
                errors.append(f"{symbol} {tf}: invalid OHLC")
            checked_frames+=1
        frame=pd.read_parquet(directory/f"{symbol}_M5.parquet")
        frame["spread"]=frame.spread.replace(0,float("nan")).ffill()
        for branch,value in row["comparison"].items():
            if value["full"]["trades"]!=len(value["trades"]):
                errors.append(f"{symbol} {branch}: trade count differs")
            last_exit=0
            days=set()
            for trade in value["trades"]:
                try:
                    assert trade["decision_time"]>=last_exit,"Overlapping per-symbol trade"
                    assert trade["exit_time"]+manifest["offset"]<=manifest["end"],"Exit uses an unfinished candle"
                    day=datetime.fromtimestamp(trade["decision_time"],timezone.utc).date()
                    assert day not in days,"Duplicate entry day"
                    days.add(day)
                    last_exit=trade["exit_time"]
                    result=verify_trade(trade,frame,manifest["point"][symbol],manifest["offset"],cost_r,horizon)
                    totals[branch].append(result)
                    verified+=1
                except (AssertionError,ValueError,TypeError) as exc:
                    errors.append(f"{symbol} {branch} {trade['entry_time']}: {exc}")
            if abs(sum(t["net_r"] for t in value["trades"])-value["full"]["net_r"])>1e-4:
                errors.append(f"{symbol} {branch}: reported net R differs")
    for branch,values in totals.items():
        if abs(sum(values)-report["aggregate"][branch]["full"]["net_r"])>1e-4:
            errors.append(f"{branch}: aggregate net R differs")
    output={"passed":not errors,"verified_trades":verified,"history_files_checked":checked_frames,
            "errors":errors,"scope":"Independent OHLC fill, R accounting, timing, nonoverlap/day and cache integrity verification; not evidence of profitability"}
    (directory/"verification.json").write_text(json.dumps(output,indent=2),encoding="utf-8")
    return output


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    result=verify(args.directory)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result["passed"] else 1)
