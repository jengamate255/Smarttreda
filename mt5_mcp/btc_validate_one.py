"""Run exactly one BTCUSD backtest variant in an isolated Python process.

This helper intentionally does not sweep variants in-process.  The MT5 Python
module has been observed to terminate the interpreter when a second heavy
backtest is started in the same process.  Invoke this file once per variant.

Example::

    python -X utf8 btc_validate_one.py --min-zone-score 40 --min-rr 3 \
        --tp-min 80 --tp-max 100 --bars 5000 --lookback 400
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from backtest import BacktestParams, Backtester, get_client, load_frames, save_result


DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results" / "btc_validation"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one BTCUSD backtest variant")
    parser.add_argument("--trend-tf", default="H4")
    parser.add_argument("--zone-tfs", default="H1,H4")
    parser.add_argument("--confirm-tf", default="H1")
    parser.add_argument("--min-zone-score", type=float, required=True)
    parser.add_argument("--min-rr", type=float, required=True)
    parser.add_argument("--tp-min", type=float, required=True)
    parser.add_argument("--tp-max", type=float, required=True)
    parser.add_argument("--sl-min", type=float, default=20.0)
    parser.add_argument("--sl-max", type=float, default=30.0)
    parser.add_argument("--lookback", type=int, default=250)
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--spread-points", type=float, default=15.0)
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument(
        "--end-time",
        default="",
        help="optional inclusive UTC cutoff (ISO format), useful for a fixed historical window",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    zone_tfs = [tf.strip().upper() for tf in args.zone_tfs.split(",") if tf.strip()]
    trend_tf = args.trend_tf.upper()
    confirm_tf = args.confirm_tf.upper()

    params = BacktestParams(
        symbol="BTCUSD",
        trend_timeframe=trend_tf,
        zone_timeframes=zone_tfs,
        confirmation_timeframe=confirm_tf,
        min_zone_score=args.min_zone_score,
        min_rr=args.min_rr,
        tp_min=args.tp_min,
        tp_max=args.tp_max,
        sl_min=args.sl_min,
        sl_max=args.sl_max,
        lookback=args.lookback,
        bars=args.bars,
        spread_points=args.spread_points,
        risk_percent=args.risk,
    )

    client = get_client()
    client.ensure_connected()
    # Do not fall back to a synthetic/stub symbol description: pip and lot
    # sizing must use the live SIM account's BTCUSD contract specification.
    info = client.symbol_info("BTCUSD")
    required = ("digits", "point", "trade_tick_size", "trade_tick_value", "volume_min", "volume_step")
    missing = [key for key in required if info.get(key) in (None, "")]
    if missing:
        raise RuntimeError(f"BTCUSD symbol_info is missing required fields: {missing}")

    tfs = sorted({trend_tf, confirm_tf, *zone_tfs})
    started = time.perf_counter()
    print(
        json.dumps(
            {
                "variant": {
                    "trend_tf": trend_tf,
                    "zone_tfs": zone_tfs,
                    "confirm_tf": confirm_tf,
                    "min_zone_score": args.min_zone_score,
                    "min_rr": args.min_rr,
                    "tp_min": args.tp_min,
                    "tp_max": args.tp_max,
                    "sl_min": args.sl_min,
                    "lookback": args.lookback,
                    "bars": args.bars,
                },
                "symbol_info": {
                    key: info.get(key)
                    for key in (
                        "name",
                        "digits",
                        "point",
                        "trade_tick_size",
                        "trade_tick_value",
                        "volume_min",
                        "volume_step",
                        "volume_max",
                        "trade_contract_size",
                    )
                },
            },
            sort_keys=True,
        ),
        flush=True,
    )

    # If a cutoff is requested, fetch extra history first.  Fetching exactly
    # ``bars`` and then trimming would silently shorten the historical window
    # because the terminal returns the most recent bars.
    cutoff = pd.Timestamp(args.end_time) if args.end_time else None
    fetch_bars = args.bars + 500 if cutoff is not None else args.bars
    frames = load_frames("BTCUSD", tfs, fetch_bars)
    for tf in tfs:
        frame = frames.get(tf)
        if frame is None or frame.empty:
            raise RuntimeError(f"No {tf} data returned for BTCUSD")
        if cutoff is not None:
            frame = frame.loc[frame["time"] <= cutoff].tail(args.bars).reset_index(drop=True)
            frames[tf] = frame
            if frame.empty:
                raise RuntimeError(f"No {tf} data remains before cutoff {cutoff}")
        print(f"data {tf}: {len(frame)} bars {frame.time.iloc[0]} -> {frame.time.iloc[-1]}", flush=True)

    result = Backtester(params, frames, info).run()
    path = save_result(result, Path(args.out))
    print(
        json.dumps(
            {
                "n_trades": result.n_trades,
                "wins": result.wins,
                "losses": result.losses,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "net_pnl": result.net_pnl,
                "total_return_pct": result.total_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "expectancy": result.expectancy,
                "avg_rr": result.avg_rr,
                "start": result.start,
                "end": result.end,
                "result": str(path),
                "seconds": round(time.perf_counter() - started, 1),
                "errors": result.errors,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
