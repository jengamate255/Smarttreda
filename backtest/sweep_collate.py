"""Collate sweep_validation JSONs into a summary CSV (evidence artifact)."""
import json
import sys
from pathlib import Path

import pandas as pd

res_dir = Path(r"E:\Project\Mt5 Ea\backtest\results")
rows = []
for f in sorted(res_dir.glob("sweep_*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    a, b, info = d["concept_a"], d["concept_b"], d["info"]
    row = {
        "symbol": info["symbol"],
        "tf": info["tf"],
        "bars": info["bars"],
        "start": info["start"][:10],
        "end": info["end"][:10],
        "events": a["events"],
        "event_rate_per_1k": round(a["events"] / info["bars"] * 1000, 2),
        "drift3_atr": round(a["stats"]["drift3"]["mean_atr"], 4),
        "baseline_drift3": round(a["stats"]["drift3"]["baseline_mean_atr"], 4),
        "diff3_p": round(a["stats"]["drift3"]["bootstrap"]["p"], 3),
        "drift5_atr": round(a["stats"]["drift5"]["mean_atr"], 4),
        "drift5_p": round(a["stats"]["drift5"]["bootstrap"]["p"], 3),
        "drift20_atr": round(a["stats"]["drift20"]["mean_atr"], 4),
        "drift20_p": round(a["stats"]["drift20"]["bootstrap"]["p"], 3),
        "touch_wr": round(a["stats"]["touch"]["win_rate"], 3),
        "baseline_touch_wr": round(a["stats"]["touch"]["baseline_win_rate"], 3),
        "touch_p": round(a["stats"]["touch"]["bootstrap"]["p"], 3),
        "thinned_touch_wr": round(a["stats_thinned"]["touch"]["win_rate"], 3),
        "thinned_drift3_p": round(a["stats_thinned"]["drift3"]["bootstrap"]["p"], 3),
        "median_atr_pts": round(info["median_atr_points"], 1),
        "median_spread_pts": info["median_spread_points"],
        "cost_atr": round(info["cost_atr_units"], 3),
    }
    if b.get("available"):
        sc = b["strong_close_z1"]
        row.update({
            "b_strong_n": sc["n"],
            "b_strong_h3": round(sc["mean_drift3"], 4),
            "b_strong_h3_p": round(sc.get("bs_3", {}).get("p", float("nan")), 3),
            "b_strong_h5": round(sc["mean_drift5"], 4),
            "b_strong_h5_p": round(sc.get("bs_5", {}).get("p", float("nan")), 3),
            "b_strong_h3_half2": round(sc.get("second_half_mean3", float("nan")), 4),
            "b_baseline_h5": round(b["baseline_signed_drift"]["5"], 4),
        })
    rows.append(row)

df = pd.DataFrame(rows)
out = res_dir / "sweep_summary.csv"
df.to_csv(out, index=False)
print(df.to_string(index=False))
print(f"\nwrote {out}")