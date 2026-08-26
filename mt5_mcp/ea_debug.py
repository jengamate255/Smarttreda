"""Debug the MQL5 EA logic gates against live MT5 data.

Reproduces each gate of PriceAction_SD_Trader on historical data and
counts how often each one passes, so we can see where signals die.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from mt5_mcp.client import get_client
from mt5_mcp.indicators import atr
from mt5_mcp.price_action import detect_patterns
from mt5_mcp.strategy import DEMAND, SUPPLY, _best_confirmation, _select_zone, analyze_trend
from mt5_mcp.supply_demand import detect_zones


def count_gates(symbol: str = "EURUSD", trend_tf: str = "H4",
                zone_tfs=("H1", "H4"), confirm_tf: str = "H1",
                bars: int = 5000, min_zone_score: float = 70.0):
    client = get_client()
    client.ensure_connected()

    trend_frame = client.rates(symbol, trend_tf, max(bars, 300))
    zone_frames = {tf: client.rates(symbol, tf, bars) for tf in zone_tfs}
    confirm_frame = client.rates(symbol, confirm_tf, bars)

    base = confirm_frame
    base_times = base["time"].values.astype("datetime64[s]")

    warmup = 500
    n_gate = {"trend_bull": 0, "trend_bear": 0, "zone_found": 0,
              "confirm_found": 0, "both": 0}
    n = len(base_times)

    for i in range(warmup, n, 5):  # sample every 5 bars for speed
        t = base_times[i]

        # --- trend gate (cached per trend TF bar)
        trend_pos = int(np.searchsorted(
            trend_frame["time"].values.astype("datetime64[s]"), t, side="right"))
        if trend_pos < 250:
            continue
        trend = analyze_trend(trend_frame.iloc[:trend_pos])
        if trend.direction == "bullish":
            n_gate["trend_bull"] += 1
            _action, zone_type = "BUY", DEMAND
        elif trend.direction == "bearish":
            n_gate["trend_bear"] += 1
            _action, zone_type = "SELL", SUPPLY
        else:
            continue

        # --- zone gate
        price = float(base.iloc[i].close)
        best_zone = None
        for tf in zone_tfs:
            frame = zone_frames[tf]
            pos = int(np.searchsorted(frame["time"].values.astype("datetime64[s]"),
                                      t, side="right"))
            tail = frame.iloc[max(0, pos - 400):pos]
            if len(tail) < 60:
                continue
            zones = detect_zones(tail, min_strength=min_zone_score)
            for zone in zones:
                atr_val = float(atr(tail).iloc[-1]) or 1e-9
                chosen, _ = _select_zone([zone], zone_type, price, atr_val,
                                         min_zone_score)
                if chosen is not None:
                    best_zone = chosen
                    break
            if best_zone is not None:
                break
        if best_zone is None:
            continue
        n_gate["zone_found"] += 1

        # --- confirmation gate
        conf_tail = confirm_frame.iloc[:int(np.searchsorted(
            confirm_frame["time"].values.astype("datetime64[s]"), t, side="right"))]
        if len(conf_tail) < 60:
            continue
        patterns = detect_patterns(conf_tail, check_last=1)
        conf = _best_confirmation(patterns, trend.direction)
        if conf is None:
            continue
        n_gate["confirm_found"] += 1
        n_gate["both"] += 1

    print(f"{symbol} | trend TF {trend_tf} | zones {','.join(zone_tfs)} | "
          f"confirm {confirm_tf} | zone score >= {min_zone_score}")
    print(f"  sampled bars: {n - warmup} (every 5th)")
    for k, v in n_gate.items():
        print(f"  {k:16s}: {v}")
    if n_gate["confirm_found"]:
        print(f"  signal rate: ~1 in {(n - warmup) / n_gate['confirm_found']:.0f} bars")
    else:
        print("  NO SIGNALS at all")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--trend-tf", default="H4")
    parser.add_argument("--zone-tfs", default="H1,H4")
    parser.add_argument("--confirm-tf", default="H1")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--min-zone-score", type=float, default=70.0)
    args = parser.parse_args()
    count_gates(args.symbol, args.trend_tf,
                tuple(t.upper() for t in args.zone_tfs.split(",")),
                args.confirm_tf.upper(), args.bars, args.min_zone_score)
