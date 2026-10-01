"""Analysis of round-3 robustness JSON: neighborhood summary, walk-forward
selection test (pick by half-1, judge by half-2), thirds split, streaks."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import OUT_DIR  # noqa: E402


def pf_of(pnls: list[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p <= 0)
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return gp / gl


def main() -> int:
    path = OUT_DIR / f"eurchf_robustness_{time.strftime('%Y-%m-%d')}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    runs = data["runs"]

    # ---- G1 3x3x3 neighborhood (realistic) -------------------------------
    cells = {}
    for k, v in runs.items():
        if not k.startswith("g1_z") or not k.endswith("|realistic"):
            continue
        cells[k.replace("|realistic", "")] = v
    pfs = [v["pf"] for v in cells.values()]
    print(f"G1 neighborhood (z x sl x tp at rsi30, ATR target): {len(cells)} cells")
    print(f"  PF: min {min(pfs):.2f}  median {np.median(pfs):.2f}  "
          f"mean {np.mean(pfs):.2f}  max {max(pfs):.2f}")
    print(f"  cells with PF>=1.0: {sum(1 for p in pfs if p >= 1.0)}/{len(pfs)}; "
          f"PF>=1.1: {sum(1 for p in pfs if p >= 1.1)}; "
          f"PF>=1.3: {sum(1 for p in pfs if p >= 1.3)}")
    both = [k for k, v in cells.items()
            if v["half1"]["pf"] >= 1.0 and v["half2"]["pf"] >= 1.0]
    print(f"  cells with BOTH halves PF>=1: {len(both)}/{len(cells)}")
    tp20 = [k for k in cells if k.endswith("_tp2.0")]
    print(f"  tp2.0 cells: {len(tp20)}, "
          f"all both-halves>=1: "
          f"{all(cells[k]['half1']['pf'] >= 1 and cells[k]['half2']['pf'] >= 1 for k in tp20)}")

    # ---- walk-forward selection: argmax half-1 PF -> judge half-2 --------
    best = max(cells.items(), key=lambda kv: kv[1]["half1"]["pf"])
    print(f"\nWalk-forward selection by half1 PF:")
    print(f"  selected: {best[0]}  half1 PF {best[1]['half1']['pf']:.2f} "
          f"({best[1]['half1']['trades']} trades)")
    print(f"  its half2 PF: {best[1]['half2']['pf']:.2f} "
          f"({best[1]['half2']['trades']} trades), full PF {best[1]['pf']:.2f}")
    # second: select by half1 among cells with >=30 trades overall
    big = [(k, v) for k, v in cells.items() if v["trades"] >= 40]
    best2 = max(big, key=lambda kv: kv[1]["half1"]["pf"])
    print(f"  selected (>=40 trades filter): {best2[0]}  half1 "
          f"{best2[1]['half1']['pf']:.2f} -> half2 {best2[1]['half2']['pf']:.2f}, "
          f"full {best2[1]['pf']:.2f}")

    # ---- thirds split for the key configs --------------------------------
    print("\nThirds split (chronological, realistic):")
    for key in ("g1_base|realistic", "g1_z2.0_sl1.0_tp2.0|realistic",
                "g1_z2.25_sl1.0_tp2.0|realistic", "g1_z2.0_sl1.5_tp2.0|realistic"):
        if key not in runs:
            continue
        tl = runs[key]["trade_list"]
        arr = np.array([t["pnl"] for t in tl])
        n = len(arr)
        cuts = [n // 3, 2 * n // 3]
        t1, t2, t3 = arr[:cuts[0]], arr[cuts[0]:cuts[1]], arr[cuts[1]:]
        print(f"  {key:<32} t1 {pf_of(t1):5.2f}({len(t1):>2})  "
              f"t2 {pf_of(t2):5.2f}({len(t2):>2})  t3 {pf_of(t3):5.2f}({len(t3):>2})"
              f"  total {pf_of(list(arr)):.2f}({n})")

    # ---- worst drawdown streak + expectancy stats for base --------------
    print("\nTrade-quality detail for g1_base (realistic):")
    tl = runs["g1_base|realistic"]["trade_list"]
    pnls = np.array([t["pnl"] for t in tl])
    rs = np.array([t["r_multiple"] for t in tl])
    streak, worst = 0, 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    print(f"  avg R {rs.mean():+.2f} | median R {np.median(rs):+.2f} | "
          f"worst loss streak {worst}")
    print(f"  exit mix: {sum(1 for t in tl if t['reason']=='TP')} TP / "
          f"{sum(1 for t in tl if t['reason']=='SL')} SL / "
          f"{sum(1 for t in tl if t['reason']=='timeout')} timeout")
    print(f"  avg win {pnls[pnls>0].mean():.2f} | avg loss {pnls[pnls<=0].mean():.2f}")

    out = {
        "g1_cells": len(cells),
        "g1_pf_min": min(pfs), "g1_pf_median": float(np.median(pfs)),
        "g1_pf_max": max(pfs),
        "g1_cells_pf_ge_1": sum(1 for p in pfs if p >= 1.0),
        "g1_cells_both_halves_ge_1": len(both),
        "wf_selection": {best[0]: {"half1_pf": best[1]["half1"]["pf"],
                                   "half2_pf": best[1]["half2"]["pf"]}},
        "bootstrap": data.get("bootstrap", {}),
    }
    op = OUT_DIR / f"eurchf_analysis_{time.strftime('%Y-%m-%d')}.json"
    op.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
