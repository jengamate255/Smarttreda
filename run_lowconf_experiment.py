#!/usr/bin/env python3
"""Low-confidence small-lot experiment.

Compares:
  A. baseline          conf>=75, score>=70, risk 0.50%
  B. mid-conf small    conf 55-75, score>=55, risk 0.20%
  C. low-conf micro    conf 40-75, score>=40, risk 0.10%
  D. very-low micro    conf 30-75, score>=35, risk 0.05%

Also reports combined portfolio: baseline + low band (disjoint conf bands).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "mt5_mcp"))

from mt5_mcp.backtest import (  # noqa: E402
    Backtester,
    BacktestParams,
    _run_variant_job,
    _variant_name,
    load_frames,
    save_result,
    _variant_tfs,
)
from mt5_mcp.client import get_client  # noqa: E402

OUT_DIR = ROOT / "mt5_mcp" / "backtest" / "results" / "lowconf_exp"

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCAD",
    "NZDCAD", "AUDNZD", "EURJPY", "XAUUSD",
]

BARS = 5000
ZONE_SCORE = 40.0  # relaxed zone floor (matches live red-team recommendation)
MIN_RR = 3.0

VARIANTS: list[dict] = [
    {
        "tag": "A_baseline",
        "desc": "conf>=75 score>=70 risk=0.50% (production)",
        "min_conf": 75.0, "max_conf": 100.0, "min_score": 70.0, "risk": 0.50,
    },
    {
        "tag": "B_mid_small",
        "desc": "conf 55-75 score>=55 risk=0.20%",
        "min_conf": 55.0, "max_conf": 74.99, "min_score": 55.0, "risk": 0.20,
    },
    {
        "tag": "C_low_micro",
        "desc": "conf 40-75 score>=40 risk=0.10%",
        "min_conf": 40.0, "max_conf": 74.99, "min_score": 40.0, "risk": 0.10,
    },
    {
        "tag": "D_vlow_micro",
        "desc": "conf 30-75 score>=35 risk=0.05%",
        "min_conf": 30.0, "max_conf": 74.99, "min_score": 35.0, "risk": 0.05,
    },
]


def make_params(symbol: str, v: dict) -> BacktestParams:
    return BacktestParams(
        symbol=symbol,
        trend_timeframe="H4",
        zone_timeframes=["H1", "H4"],
        confirmation_timeframe="H1",
        min_zone_score=ZONE_SCORE,
        min_trade_score=v["min_score"],
        min_confidence_percent=v["min_conf"],
        max_confidence_percent=v["max_conf"],
        risk_percent=v["risk"],
        min_rr=MIN_RR,
        bars=BARS,
    )


def run_one(symbol: str, v: dict) -> dict:
    p = make_params(symbol, v)
    frames = load_frames(symbol, _variant_tfs(p), p.bars, use_cache=True, allow_mt5=False)
    bt = Backtester(p, frames, {})
    r = bt.run()
    out = OUT_DIR / v["tag"]
    save_result(r, out)
    trades = r.trades or []
    return {
        "tag": v["tag"],
        "symbol": symbol,
        "n_trades": r.n_trades,
        "wins": r.wins,
        "win_rate": r.win_rate,
        "profit_factor": r.profit_factor,
        "net_pnl": r.net_pnl,
        "total_return_pct": r.total_return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "expectancy": r.expectancy,
        "avg_rr": r.avg_rr,
        "avg_confidence": round(
            sum(t.get("confidence", 0) for t in trades) / len(trades), 1
        ) if trades else 0.0,
        "errors": r.errors,
    }


def prefetch() -> dict[str, dict]:
    tfs = ["H1", "H4"]
    client = get_client()
    client.ensure_connected()
    info_cache: dict[str, dict] = {}
    for i, s in enumerate(SYMBOLS, 1):
        t0 = time.perf_counter()
        print(f"  [{i}/{len(SYMBOLS)}] {s}...", flush=True)
        load_frames(s, tfs, BARS, use_cache=True, allow_mt5=True, ttl=300.0)
        try:
            info_cache[s] = client.symbol_info(s) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"      WARN symbol_info failed: {exc}", flush=True)
            info_cache[s] = {}
        print(f"      done in {time.perf_counter() - t0:.1f}s", flush=True)
    return info_cache


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {}
    n = sum(r["n_trades"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    net = sum(r["net_pnl"] for r in rows)
    gp = sum(r["net_pnl"] for r in rows if r["net_pnl"] > 0)
    gl = abs(sum(r["net_pnl"] for r in rows if r["net_pnl"] < 0))
    avg_dd = sum(r["max_drawdown_pct"] for r in rows) / len(rows)
    avg_wr = sum(r["win_rate"] for r in rows) / len(rows)
    avg_conf = (
        sum(r["avg_confidence"] * r["n_trades"] for r in rows if r["n_trades"]) / n
        if n else 0.0
    )
    return {
        "trades": n,
        "wins": wins,
        "net_pnl": round(net, 2),
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "avg_symbol_win_rate": round(avg_wr, 1),
        "profit_factor": round(gp / gl, 3) if gl else (999.0 if gp else 0.0),
        "avg_max_dd_pct": round(avg_dd, 2),
        "avg_confidence": round(avg_conf, 1),
        "symbols_with_trades": sum(1 for r in rows if r["n_trades"] > 0),
    }


def main() -> int:
    global SYMBOLS
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Prefetching {len(symbols)} symbols ({BARS} bars, H1+H4)...")
    SYMBOLS = symbols
    info_cache = prefetch()

    jobs = []
    for v in VARIANTS:
        for s in symbols:
            p = make_params(s, v)
            jobs.append({
                "params": asdict(p),
                "info": info_cache.get(s, {}),
                "out_dir": str(OUT_DIR / v["tag"]),
                "use_cache": True,
                "ttl": 999999.0,
                "_tag": v["tag"],
                "_desc": v["desc"],
            })

    print(f"Running {len(jobs)} variant jobs ({args.jobs} workers)...")
    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futs = {
            pool.submit(
                _run_variant_job,
                {k: j[k] for k in ("params", "info", "out_dir", "use_cache", "ttl")},
            ): j
            for j in jobs
        }
        for i, fut in enumerate(as_completed(futs), 1):
            j = futs[fut]
            try:
                done = fut.result()
                r = done["result"]
                trades = r.trades or []
                row = {
                    "tag": j["_tag"],
                    "symbol": j["params"]["symbol"],
                    "n_trades": r.n_trades,
                    "wins": r.wins,
                    "win_rate": r.win_rate,
                    "profit_factor": r.profit_factor,
                    "net_pnl": r.net_pnl,
                    "total_return_pct": r.total_return_pct,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "expectancy": r.expectancy,
                    "avg_rr": r.avg_rr,
                    "avg_confidence": round(
                        sum(t.get("confidence", 0) for t in trades) / len(trades), 1
                    ) if trades else 0.0,
                    "errors": r.errors,
                }
                results.append(row)
                err = f" ERR:{r.errors}" if r.errors else ""
                print(
                    f"  [{i}/{len(jobs)}] {row['tag']:14} {row['symbol']:7} "
                    f"tr={row['n_trades']:3d} wr={row['win_rate']:5.1f}% "
                    f"pf={row['profit_factor']:5.2f} pnl={row['net_pnl']:+9.2f} "
                    f"dd={row['max_drawdown_pct']:5.2f}%{err}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(jobs)}] FAIL {j['_tag']} {j['params']['symbol']}: {exc}", flush=True)
                results.append({
                    "tag": j["_tag"], "symbol": j["params"]["symbol"],
                    "n_trades": 0, "wins": 0, "win_rate": 0, "profit_factor": 0,
                    "net_pnl": 0, "total_return_pct": 0, "max_drawdown_pct": 0,
                    "expectancy": 0, "avg_rr": 0, "avg_confidence": 0,
                    "errors": [str(exc)],
                })

    by_tag: dict[str, list[dict]] = {}
    for r in results:
        by_tag.setdefault(r["tag"], []).append(r)

    report = {"bars": BARS, "zone_score": ZONE_SCORE, "min_rr": MIN_RR, "variants": {}}
    print("\n" + "=" * 78)
    print("AGGREGATE BY VARIANT")
    print("=" * 78)
    header = f"{'variant':14} {'desc':42} {'tr':>4} {'wr%':>5} {'PF':>6} {'PnL':>10} {'avgDD%':>7} {'conf':>5}"
    print(header)
    print("-" * len(header))
    for v in VARIANTS:
        agg = aggregate(by_tag.get(v["tag"], []))
        report["variants"][v["tag"]] = {"desc": v["desc"], "params": v, "agg": agg}
        print(
            f"{v['tag']:14} {v['desc']:42} {agg.get('trades', 0):4d} "
            f"{agg.get('win_rate', 0):5.1f} {agg.get('profit_factor', 0):6.2f} "
            f"{agg.get('net_pnl', 0):+10.2f} {agg.get('avg_max_dd_pct', 0):7.2f} "
            f"{agg.get('avg_confidence', 0):5.1f}"
        )

    # Combined: baseline full-size + disjoint low-conf bands at their small sizes
    print("\n" + "=" * 78)
    print("COMBINED (baseline full-size + low band small-lot)")
    print("=" * 78)
    base = aggregate(by_tag.get("A_baseline", []))
    for tag in ("B_mid_small", "C_low_micro", "D_vlow_micro"):
        band = aggregate(by_tag.get(tag, []))
        comb_tr = base.get("trades", 0) + band.get("trades", 0)
        comb_pnl = base.get("net_pnl", 0) + band.get("net_pnl", 0)
        comb_wins = base.get("wins", 0) + band.get("wins", 0)
        # avg DD: rough — not portfolio-reconstructed; report both separately
        report["variants"][f"A+{tag}"] = {
            "desc": f"baseline + {tag}",
            "agg": {
                "trades": comb_tr,
                "net_pnl": round(comb_pnl, 2),
                "win_rate": round(comb_wins / comb_tr * 100, 1) if comb_tr else 0,
                "base_pnl": base.get("net_pnl", 0),
                "band_pnl": band.get("net_pnl", 0),
                "base_dd": base.get("avg_max_dd_pct", 0),
                "band_dd": band.get("avg_max_dd_pct", 0),
            },
        }
        print(
            f"  A+{tag:12}  trades {comb_tr:4d}  PnL {comb_pnl:+10.2f} "
            f"(base {base.get('net_pnl', 0):+8.2f} + band {band.get('net_pnl', 0):+8.2f})  "
            f"WR {report['variants'][f'A+{tag}']['agg']['win_rate']}%  "
            f"DD base {base.get('avg_max_dd_pct', 0):.2f}% / band {band.get('avg_max_dd_pct', 0):.2f}%"
        )

    # Per-symbol table for baseline vs C
    print("\n" + "=" * 78)
    print("PER-SYMBOL: A_baseline vs C_low_micro vs D_vlow_micro")
    print("=" * 78)
    print(f"{'symbol':8} {'A_tr':>4} {'A_pnl':>9} {'A_wr':>5} | {'C_tr':>4} {'C_pnl':>9} {'C_wr':>5} | {'D_tr':>4} {'D_pnl':>9} {'D_wr':>5}")
    print("-" * 78)
    for s in symbols:
        a = next((r for r in by_tag.get("A_baseline", []) if r["symbol"] == s), None)
        c = next((r for r in by_tag.get("C_low_micro", []) if r["symbol"] == s), None)
        d = next((r for r in by_tag.get("D_vlow_micro", []) if r["symbol"] == s), None)
        def cell(r):
            if not r: return "   -        -     "
            return f"{r['n_trades']:4d} {r['net_pnl']:+9.2f} {r['win_rate']:5.1f}"
        print(f"{s:8} {cell(a)} | {cell(c)} | {cell(d)}")

    out_json = OUT_DIR / "report.json"
    report["per_symbol"] = results
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
