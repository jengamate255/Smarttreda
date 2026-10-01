"""Round 4: the bbmr family on D1 (4.5 years) — longer-horizon check.

Runs the same z x sl x tp grid as the H1 G1 neighborhood so results are
directly comparable, plus mr/emac spot checks. Writes results JSON.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    CostModel, Variant, load_frames, prepare, run_variant, OUT_DIR, SYMBOL,
)
from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size, pip_value_per_lot  # noqa: E402


def main() -> int:
    t0 = time.perf_counter()
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(SYMBOL)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)

    frames = load_frames(SYMBOL, ["D1"], 1500)
    d1 = frames["D1"].reset_index(drop=True)
    P = prepare(d1, float(info.get("point", 1e-5)))
    print(f"D1 {len(d1)} bars {d1['time'].iloc[0]} .. {d1['time'].iloc[-1]}")

    costs = [
        CostModel("canonical", use_bar_spread=False, spread_points=15.0,
                  slippage_price=1.0 * pip, vol_spread=True),
        CostModel("realistic", use_bar_spread=True,
                  slippage_price=1.0 * float(info.get("point", 1e-5)),
                  vol_spread=False),
    ]

    variants: list[Variant] = []
    for z in (2.0, 2.25, 2.5):
        for sl in (1.0, 1.5, 2.0):
            for tp in (1.5, 2.0, 2.5):
                variants.append(Variant(
                    f"d1_z{z}_sl{sl}_tp{tp}", "bbmr",
                    {"z": z, "rsi_th": 30.0, "sl_atr": sl,
                     "target": "atr", "tp_atr": tp}))
    variants += [
        Variant("d1_bbmr_mid", "bbmr",
                {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5}),
        Variant("d1_mr_signal", "mr", {"validity_gate": False}),
        Variant("d1_emac", "emac",
                {"validity_gate": False, "sl_atr": 1.5, "tp_atr": 3.0}),
    ]

    hdr = (f"{'variant':<28} {'cost':<10} {'tr':>4} {'win%':>5} {'PF':>7} "
           f"{'net$':>9} {'exp$':>7} {'DD%':>6} {'h1 PF (n)':>11} {'h2 PF (n)':>11}")
    print(hdr)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": "D1", "runs": {}}
    pfs_real = []
    for v in variants:
        for cost in costs:
            r = run_variant(P, v, cost, info, pip, pip_value)
            out["runs"][f"{v.name}|{cost.name}"] = {
                "variant": asdict(v), "cost": cost.name, **r}
            if cost.name == "realistic":
                pfs_real.append(r["pf"])
            print(f"{v.name:<28} {cost.name:<10} {r['trades']:>4} "
                  f"{r['win_rate']:>5.1f} {r['pf']:>7.2f} {r['net_pnl']:>9.2f} "
                  f"{r['expectancy']:>7.2f} {r['max_dd_pct']:>6.2f} "
                  f"{r['half1']['pf']:>6.2f}({r['half1']['trades']:>2}) "
                  f"{r['half2']['pf']:>6.2f}({r['half2']['trades']:>2})", flush=True)

    cells = [r["pf"] for k, r in out["runs"].items()
             if k.startswith("d1_z") and k.endswith("|realistic")]
    print(f"\nD1 grid (27 cells, realistic): min {min(cells):.2f} "
          f"median {np.median(cells):.2f} mean {np.mean(cells):.2f} "
          f"max {max(cells):.2f} | PF>=1: {sum(1 for p in cells if p >= 1)}/27")

    path = OUT_DIR / f"eurchf_d1_{time.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path} [{time.perf_counter() - t0:.1f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
