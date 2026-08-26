"""Profile sweep_validation compute steps on synthetic data."""
import sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Project\Mt5 Ea")
sys.path.insert(0, r"E:\Project\Mt5 Ea\backtest")

from sweep_validation import (  # noqa: E402
    wilder_atr, find_pivots, detect_sweeps, _random_entry_metrics,
    concept_a_stats, volume_flow_stats,
)

log = open(r"E:\Project\Mt5 Ea\tmp\profile.log", "w")
def p(msg):
    log.write(f"{time.time()%1000:.3f} {msg}\n"); log.flush()

rng = np.random.default_rng(7)
n = 20000
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

t = time.time(); atr = wilder_atr(high, low, close, 14); p(f"ATR {time.time()-t:.2f}s")
t = time.time(); ph, pl = find_pivots(high, low, 3); p(f"pivots {time.time()-t:.2f}s, {ph.sum()} {pl.sum()}")
t = time.time(); events = detect_sweeps(df, atr, 3, 0.4, 200, (3,5,10,20), 20); p(f"sweeps {time.time()-t:.2f}s, n={len(events)}")
if events:
    t = time.time()
    dirs = np.array([e["dir"] for e in events])
    offsets = np.array([e["idx"] for e in events])
    baseline = _random_entry_metrics(df, atr, dirs, (3,5,10,20), 20, offsets)
    p(f"baseline {time.time()-t:.2f}s")
    t = time.time(); stats = concept_a_stats(events, baseline, (3,5,10,20), 20); p(f"stats {time.time()-t:.2f}s")
t = time.time(); b = volume_flow_stats(df, atr); p(f"volflow {time.time()-t:.2f}s avail={b.get('available')}")
p("PROFILE DONE")
log.close()