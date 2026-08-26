"""ict_collate.py - collate ict_validation.py JSONs into a summary CSV with verdicts.

Verdict rules (edge-lab standard):
  EDGE        : n >= 200, bootstrap p < 0.05 vs baseline on >= 2 horizons with
                event drift > baseline drift, drift20 (or drift10) > cost ATR,
                and both halves of drift20 positive.
  MARGINAL    : n >= 200, p < 0.05 on >= 1 horizon, drift20 > 0, but fails cost
                or half-sample stability.
  NO EDGE     : n >= 200, no stable significant edge vs baseline.
  INSUFFICIENT: n < 200.

Writes backtest/results/ict_summary.csv and prints a compact verdict table.
"""
import json
import sys
from pathlib import Path

import pandas as pd

res_dir = Path(r"E:\Project\Mt5 Ea\backtest\results")
CONCEPT_ORDER = ["displacement", "fvg_cont", "fvg_reentry", "mss", "mss_strict", "bos", "vimb"]
CELL_ORDER = ["all", "killzone_union", "killzone_ny", "killzone_london"]


def verdict(n: int, drift: float, cost: float, pvals: list[float], halves: tuple[float, float], diffs_pos: list[bool]) -> str:
    if n < 200:
        return "INSUFFICIENT"
    if drift != drift or cost != cost:
        return "NO EDGE"
    sig = sum(1 for p, dpos in zip(pvals, diffs_pos) if p < 0.05 and dpos)
    h1, h2 = halves
    if sig >= 2 and drift > cost and h1 > 0 and h2 > 0:
        return "EDGE"
    if sig >= 1 and drift > 0:
        return "MARGINAL"
    return "NO EDGE"


rows = []
for f in sorted(res_dir.glob("ict_*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    info = d["info"]
    for cname in CONCEPT_ORDER:
        cell = d["cells"].get(cname)
        if not cell:
            continue
        for kname in CELL_ORDER:
            st = cell.get(kname)
            if not st or st["n"] == 0:
                continue
            h3, h5, h10, h20 = (st["horizons"][h] for h in (3, 5, 10, 20))
            pvals = [h3["bootstrap"]["p"], h5["bootstrap"]["p"], h10["bootstrap"]["p"], h20["bootstrap"]["p"]]
            diffs_pos = [
                h3["mean_atr"] > h3["baseline_mean_atr"],
                h5["mean_atr"] > h5["baseline_mean_atr"],
                h10["mean_atr"] > h10["baseline_mean_atr"],
                h20["mean_atr"] > h20["baseline_mean_atr"],
            ]
            drift20 = h20["mean_atr"] if h20["mean_atr"] == h20["mean_atr"] else h10["mean_atr"]
            v = verdict(st["n"], drift20, info["cost_atr_units"], pvals, (h20["first_half_mean"], h20["second_half_mean"]), diffs_pos)
            rows.append({
                "symbol": info["symbol"],
                "tf": info["tf"],
                "concept": cname,
                "cell": kname,
                "n": st["n"],
                "n_up": st["n_up"],
                "n_dn": st["n_dn"],
                "drift3": round(h3["mean_atr"], 4),
                "drift5": round(h5["mean_atr"], 4),
                "drift10": round(h10["mean_atr"], 4),
                "drift20": round(h20["mean_atr"], 4),
                "base3": round(h3["baseline_mean_atr"], 4),
                "base20": round(h20["baseline_mean_atr"], 4),
                "p3": round(pvals[0], 3),
                "p5": round(pvals[1], 3),
                "p10": round(pvals[2], 3),
                "p20": round(pvals[3], 3),
                "half1_20": round(h20["first_half_mean"], 4),
                "half2_20": round(h20["second_half_mean"], 4),
                "touch_wr": round(st["touch"]["win_rate"], 3),
                "touch_base_wr": round(st["touch"]["baseline_win_rate"], 3),
                "touch_p": round(st["touch"]["bootstrap"]["p"], 3),
                "cost_atr": round(info["cost_atr_units"], 4),
                "net20": round(drift20 - info["cost_atr_units"], 4),
                "verdict": v,
            })

df = pd.DataFrame(rows)
out = res_dir / "ict_summary.csv"
df.to_csv(out, index=False)

# compact verdict table
print("verdict by (symbol tf concept cell):")
pivot = df[df["cell"] == "all"][["symbol", "tf", "concept", "n", "drift20", "base20", "p20", "half1_20", "half2_20", "net20", "verdict"]]
print(pivot.to_string(index=False))
print()
kz = df[df["cell"].str.startswith("killzone_")][["symbol", "tf", "concept", "cell", "n", "drift20", "p20", "net20", "verdict"]]
if len(kz):
    print("killzone cells:")
    print(kz.to_string(index=False))
print()
print("EDGE/MARGINAL counts per concept (all cells):")
print(df[df["verdict"].isin(["EDGE", "MARGINAL"])].groupby("concept")["verdict"].value_counts().to_string())
print(f"\nwrote {out}")
