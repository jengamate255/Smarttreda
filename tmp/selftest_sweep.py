"""Synthetic-data self-test of sweep_validation compute path (no MT5)."""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Project\Mt5 Ea")
sys.path.insert(0, r"E:\Project\Mt5 Ea\backtest")

from sweep_validation import (  # noqa: E402
    wilder_atr, find_pivots, detect_sweeps, _random_entry_metrics,
    concept_a_stats, volume_flow_stats,
)

rng = np.random.default_rng(7)
n = 20000
# random-walk with occasional spikes to generate sweep-like bars
ret = rng.normal(0, 1, n)
close = 1000 + np.cumsum(ret)
high = close + np.abs(rng.normal(0, 0.8, n))
low = close - np.abs(rng.normal(0, 0.8, n))
open_ = close + rng.normal(0, 0.3, n)
vol = rng.poisson(200, n).astype(float)

df = pd.DataFrame({
    "time": pd.date_range("2026-01-01", periods=n, freq="5min"),
    "open": open_, "high": high, "low": low, "close": close,
    "tick_volume": vol, "spread": np.full(n, 2.0),
})

atr = wilder_atr(high, low, close, 14)
print("ATR nans:", int(np.isnan(atr).sum()), "median:", np.nanmedian(atr))

ph, pl = find_pivots(high, low, 3)
print("pivots high/low:", int(ph.sum()), int(pl.sum()))

events = detect_sweeps(df, atr, 3, 0.4, 200, (3, 5, 10, 20), 20)
print("events:", len(events))
if events:
    print("sample event:", {k: v for k, v in events[0].items() if k not in ("time",)})
    dirs = np.array([e["dir"] for e in events])
    offsets = np.array([e["idx"] for e in events])
    baseline = _random_entry_metrics(df, atr, dirs, (3, 5, 10, 20), 20, offsets)
    stats = concept_a_stats(events, baseline, (3, 5, 10, 20), 20)
    print("drift3:", stats["drift3"])
    print("touch:", stats["touch"])

b = volume_flow_stats(df, atr)
print("concept B available:", b.get("available"))
if b.get("available"):
    print("baseline:", b["baseline_signed_drift"])
    for q, row in b["quintiles"].items():
        print(f"Q{q}: n={row['n']} h3={row['mean_drift3']:.4f} h5={row['mean_drift5']:.4f}")
    print("strong:", b["strong_close_z1"])
print("SELF-TEST OK")