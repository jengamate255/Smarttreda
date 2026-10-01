"""Aggregate stats for the final report (reads the three result JSONs)."""
from __future__ import annotations

import json
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import OUT_DIR  # noqa: E402


def load(name: str) -> dict:
    p = OUT_DIR / name
    return json.loads(p.read_text(encoding="utf-8"))


def fmt_pf(rows: list[dict]) -> str:
    pfs = [r["pf"] for r in rows]
    return (f"n={len(pfs)} min={min(pfs):.2f} med={st.median(pfs):.2f} "
            f"mean={st.mean(pfs):.2f} max={max(pfs):.2f} "
            f">=1:{sum(1 for p in pfs if p >= 1)}/{len(pfs)}")


def main() -> int:
    d2 = time.strftime("%Y-%m-%d")
    h = load(f"eurchf_results_{d2}.json")["variants"]
    rob = load(f"eurchf_robustness_{d2}.json")["runs"]
    d1 = load(f"eurchf_d1_{d2}.json")["runs"]

    def sel(src: dict, pred, cost="realistic"):
        return [v for k, v in src.items() if k.endswith(f"|{cost}") and pred(k)]

    print("== H1 harness variants (realistic / canonical PF) ==")
    for k, v in sorted(h.items()):
        if not k.endswith("|realistic"):
            continue
        canon = h.get(k.replace("|realistic", "|canonical"), {})
        print(f"  {v['variant']['name']:<24} {v['variant']['sig']:<6} "
              f"tr={v['trades']:>3} win={v['win_rate']:>4.1f}% "
              f"PF={v['pf']:>5.2f} canonPF={canon.get('pf', 0):>5.2f} "
              f"exp={v['expectancy']:>7.2f} dd={v['max_dd_pct']:>5.2f}% "
              f"h={v['half1']['pf']:.2f}/{v['half2']['pf']:.2f}")

    print("\n== G1 grid (H1 z x sl x tp, realistic) ==")
    g1 = sel(rob, lambda k: k.startswith("g1_z"))
    print(" ", fmt_pf(g1))
    g1t = [c for k, c in ((k, v) for k, v in [(k.replace("|realistic", ""), v)
           for k, v in rob.items() if k.endswith("|realistic") and k.startswith("g1_z")])
           if k.endswith("_tp2.0")]
    print("  tp2.0 cells only:", fmt_pf(g1t))
    both = [k for k, v in [(k.replace("|realistic", ""), v) for k, v in rob.items()
            if k.endswith("|realistic") and k.startswith("g1_z")]
            if v["half1"]["pf"] >= 1 and v["half2"]["pf"] >= 1]
    print(f"  both halves >=1: {len(both)}")

    print("\n== G2 session grid (realistic) ==")
    print(" ", fmt_pf(sel(rob, lambda k: k.startswith("g2_"))))
    print("== G3 z25 grid (realistic) ==")
    print(" ", fmt_pf(sel(rob, lambda k: k.startswith("g3_"))))
    print("== D1 grid (realistic) ==")
    print(" ", fmt_pf(sel(d1, lambda k: k.startswith("d1_z"))))
    print("== D1 grid (canonical) ==")
    print(" ", fmt_pf(sel(d1, lambda k: k.startswith("d1_z"), "canonical")))

    base = rob["g1_base|realistic"]
    print(f"\n== base config detail: {base['trades']} trades, "
          f"PF {base['pf']}, net {base['net_pnl']}, exp {base['expectancy']}, "
          f"dd {base['max_dd_pct']}%, "
          f"thirds/halves {base['half1']}/{base['half2']}, "
          f"avg_r {base['avg_r']}, held {base['avg_bars_held']}, "
          f"TP/SL/TO {base['tp_hits']}/{base['sl_hits']}/{base['timeouts']}")

    # canonical winners (what survives the punitive model)
    canon_all = [(k, v["pf"], v["trades"]) for k, v in rob.items()
                 if k.endswith("|canonical") and v["trades"] > 0 and v["pf"] >= 1.0]
    print("\nrobustness runs with canonical PF>=1:", canon_all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
