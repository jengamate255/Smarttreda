"""Walk-forward check: run a config on the first and second half of a long
history to make sure the edge is not concentrated in one period.

Second half keeps pre-cut history for zone/trend context (like live trading)
but only counts trades entered after the cut.

Run from the package directory:
    python walkforward.py EURUSD --zones H1 --zscore 70 --sl-min 15
    python walkforward.py JP225 --zones H1,H4 --zscore 40 --sl-min 20
"""
import argparse
import sys

from backtest import (
    BacktestParams, Backtester, load_frames, DEFAULT_OUT, get_client,
)

WARMUP_HISTORY = 1500  # base-TF bars of pre-cut history kept for context


def run_period(name, frames, info, params):
    bt = Backtester(params, frames, info)
    res = bt.run()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="EURUSD")
    ap.add_argument("--trend-tf", default="H4")
    ap.add_argument("--zones", default="H1")
    ap.add_argument("--confirm", default="H1")
    ap.add_argument("--min-zone-score", type=float, default=70.0)
    ap.add_argument("--sl-min", type=float, default=15.0)
    ap.add_argument("--tp-min", type=float, default=80.0)
    ap.add_argument("--tp-max", type=float, default=100.0)
    ap.add_argument("--min-rr", type=float, default=3.0)
    ap.add_argument("--bars", type=int, default=12000)
    args = ap.parse_args()

    symbol = args.symbol
    p = BacktestParams(
        symbol=symbol,
        trend_timeframe=args.trend_tf.upper(),
        zone_timeframes=[z.upper() for z in args.zones.split(",") if z.strip()],
        confirmation_timeframe=args.confirm.upper(),
        min_zone_score=args.min_zone_score,
        risk_percent=0.5,
        sl_min=args.sl_min,
        tp_min=args.tp_min,
        tp_max=args.tp_max,
        min_rr=args.min_rr,
        lookback=400,
        bars=20000,
    )
    base_tf = p.confirmation_timeframe.upper()
    tfs = sorted({p.trend_timeframe.upper(), *p.zone_timeframes, base_tf})
    frames = load_frames(symbol, tfs, args.bars)
    if not frames:
        print("no data")
        return
    info = get_client().symbol_info(symbol) or {"symbol": symbol}
    base = frames[base_tf]
    times = base["time"].tolist()
    mid_i = len(times) // 2
    mid = times[mid_i]
    print(f"history: {times[0]} -> {times[-1]} | mid: {mid}")

    def stats(res):
        if not res.trades:
            return "0 trades"
        wins = sum(1 for t in res.trades if t["pnl"] > 0)
        gp = sum(t["pnl"] for t in res.trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in res.trades if t["pnl"] <= 0))
        pf = gp / gl if gl else 0.0
        ret = sum(t["pnl"] for t in res.trades)
        return (f"{len(res.trades)} trades | win {wins/len(res.trades)*100:.1f}% "
                f"| PF {pf:.2f} | net {ret:+.0f} | "
                f"entries {res.trades[0]['entry_time'][:10]}..{res.trades[-1]['entry_time'][:10]}")

    # First half: data up to mid only
    first = {tf: df[df["time"] <= mid].reset_index(drop=True)
             for tf, df in frames.items()}
    res1 = run_period("first", first, info, p)
    print(f"[first ] {stats(res1)}")

    # Second half: keep WARMUP_HISTORY pre-cut bars for context, count trades after mid
    keep_start = times[max(0, mid_i - WARMUP_HISTORY)]
    second = {tf: df[df["time"] >= keep_start].reset_index(drop=True)
              for tf, df in frames.items()}
    res2 = run_period("second", second, info, p)
    before = [t for t in res2.trades if t["entry_time"] <= str(mid)]
    after = [t for t in res2.trades if t["entry_time"] > str(mid)]
    print(f"[second] raw: {stats(res2)}")
    if after:
        wins = sum(1 for t in after if t["pnl"] > 0)
        gp = sum(t["pnl"] for t in after if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in after if t["pnl"] <= 0))
        pf = gp / gl if gl else 0.0
        ret = sum(t["pnl"] for t in after)
        print(f"         after-cut only: {len(after)} trades | win {wins/len(after)*100:.1f}% "
              f"| PF {pf:.2f} | net {ret:+.0f} | "
              f"entries {after[0]['entry_time'][:10]}..{after[-1]['entry_time'][:10]}")
    else:
        print("         after-cut only: 0 trades")
    if before:
        print(f"         pre-cut (context only): {len(before)} trades "
              f"| net {sum(t['pnl'] for t in before):+.0f}")


if __name__ == "__main__":
    main()
