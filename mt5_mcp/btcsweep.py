"""BTCUSD variant sweep — loads frames once, runs many configs in-process.

Faster than the CLI for heavy symbols (shared frames, tunable lookback).
Run: python btcsweep.py
"""
import time

from backtest import (
    BacktestParams, Backtester, load_frames, get_client,
)


def run(p, frames, info):
    started = time.perf_counter()
    bt = Backtester(p, frames, info)
    res = bt.run()
    el = time.perf_counter() - started
    print(
        f"  {res.n_trades:3d} trades | win {res.win_rate:5.1f}% | "
        f"PF {res.profit_factor:5.2f} | ret {res.total_return_pct:+7.2f}% | "
        f"maxDD {res.max_drawdown_pct:5.2f}% | exp {res.expectancy:+7.2f} | {el:.0f}s"
    )
    return res


def main():
    frames = load_frames("BTCUSD", ["H4", "H1", "M15"], 3000)
    if not frames:
        print("no data")
        return
    info = get_client().symbol_info("BTCUSD") or {"symbol": "BTCUSD"}
    print(f"frames: H1={len(frames.get('H1', []))} H4={len(frames.get('H4', []))} "
          f"M15={len(frames.get('M15', []))}")

    base = dict(symbol="BTCUSD", risk_percent=0.5, sl_min=15.0,
                lookback=250, bars=5000)

    variants = [
        # name, trend, zones, confirm, zscore, tp_min, tp_max, rr
        ("H4/H1 z50 rr2 tp60-80",       "H4", ["H1"],        "H1",  50, 60, 80,  2.0),
        ("H4/H1+H4 z40 rr2 tp60-120",   "H4", ["H1", "H4"],  "H1",  40, 60, 120, 2.0),
        ("H4/H1+H4 z70 rr3 tp80-100",   "H4", ["H1", "H4"],  "H1",  70, 80, 100, 3.0),
        ("H4/H1+H4 z30 rr2 tp60-100",   "H4", ["H1", "H4"],  "H1",  30, 60, 100, 2.0),
        ("H4/M15+H1 z35 rr1.5 tp30-50", "H4", ["M15", "H1"], "M15", 35, 30, 50,  1.5),
        ("H1/M15+H1 z35 rr1.5 tp30-50", "H1", ["M15", "H1"], "M15", 35, 30, 50,  1.5),
    ]
    for name, trend, zones, confirm, z, tpmin, tpmax, rr in variants:
        p = BacktestParams(
            trend_timeframe=trend,
            zone_timeframes=zones,
            confirmation_timeframe=confirm,
            min_zone_score=float(z),
            tp_min=float(tpmin),
            tp_max=float(tpmax),
            min_rr=float(rr),
            **base,
        )
        print(f"[{name}]")
        run(p, frames, info)


if __name__ == "__main__":
    main()
