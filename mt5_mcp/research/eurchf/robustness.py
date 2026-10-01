"""Round 3: robustness for the near-miss EURCHF candidates.

  G1  bbmr_atrTP parameter neighborhood (z x sl x tp x rsi)
  G2  bbmr_us session-filter neighborhood (hour-window stability)
  G3  bbmr_z25_sl15 parameter neighborhood
  G4  H4 timeframe spot-check (bbmr / emac / donch / mr on H4 bars)
  +   bootstrap P(PF>1) for the best H1 candidate
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
    WARMUP, CostModel, Prepared, Variant, load_frames, prepare, run_variant,
    OUT_DIR, SYMBOL, START_BALANCE, RISK_PCT,
)
from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size, pip_value_per_lot  # noqa: E402


def line(name: str, cost: str, r: dict) -> str:
    return (
        f"{name:<28} {cost:<10} {r['trades']:>4} {r['win_rate']:>5.1f} "
        f"{r['pf']:>7.2f} {r['net_pnl']:>9.2f} {r['expectancy']:>7.2f} "
        f"{r['max_dd_pct']:>6.2f} "
        f"{r['half1']['pf']:>6.2f}({r['half1']['trades']:>2}) "
        f"{r['half2']['pf']:>6.2f}({r['half2']['trades']:>2})"
    )


HDR = (f"{'variant':<28} {'cost':<10} {'tr':>4} {'win%':>5} {'PF':>7} "
       f"{'net$':>9} {'exp$':>7} {'DD%':>6}   {'half1 PF (n)':>13} {'half2 PF (n)':>13}")


def main() -> int:
    t0 = time.perf_counter()
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(SYMBOL)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)

    frames = load_frames(SYMBOL, ["H1", "H4"], 6000)
    h1 = frames["H1"].reset_index(drop=True)
    h4 = frames["H4"].reset_index(drop=True)
    P1 = prepare(h1, float(info.get("point", 1e-5)))
    P4 = prepare(h4, float(info.get("point", 1e-5)))
    print(f"H1 {len(h1)} bars | H4 {len(h4)} bars")

    costs = [
        CostModel("canonical", use_bar_spread=False, spread_points=15.0,
                  slippage_price=1.0 * pip, vol_spread=True),
        CostModel("realistic", use_bar_spread=True,
                  slippage_price=1.0 * float(info.get("point", 1e-5)),
                  vol_spread=False),
    ]

    out: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": {}}

    def do(P: Prepared, v: Variant, tag: str = "") -> None:
        for cost in costs:
            r = run_variant(P, v, cost, info, pip, pip_value)
            out["runs"][f"{tag}{v.name}|{cost.name}"] = {
                "variant": asdict(v), "cost": cost.name, **r}
            print(line(f"{tag}{v.name}", cost.name, r), flush=True)

    print("\n=== G1: bbmr_atrTP neighborhood (base z2/sl1.5/tp2.0/rsi30) ===")
    print(HDR)
    for z in (2.0, 2.25, 2.5):
        for sl in (1.0, 1.5, 2.0):
            for tp in (1.5, 2.0, 2.5):
                if (z, sl, tp) == (2.0, 1.5, 2.0):
                    continue
                do(P1, Variant(f"g1_z{z}_sl{sl}_tp{tp}", "bbmr",
                               {"z": z, "rsi_th": 30.0, "sl_atr": sl,
                                "target": "atr", "tp_atr": tp}))
    for rsi in (27.0, 33.0):
        do(P1, Variant(f"g1_rsi{rsi:g}", "bbmr",
                       {"z": 2.0, "rsi_th": rsi, "sl_atr": 1.5,
                        "target": "atr", "tp_atr": 2.0}))
    for sl in (0.75, 1.25):   # stop-width refinement around the sl1.0 row
        do(P1, Variant(f"g1_z2.0_sl{sl}_tp2.0", "bbmr",
                       {"z": 2.0, "rsi_th": 30.0, "sl_atr": sl,
                        "target": "atr", "tp_atr": 2.0}))
    do(P1, Variant("g1_base", "bbmr",
                   {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                    "target": "atr", "tp_atr": 2.0}))

    print("\n=== G2: bbmr_us session neighborhood (mid target) ===")
    print(HDR)
    for hf, ht in ((16, 23), (17, 23), (17, 22), (18, 23)):
        for sl in (1.0, 1.5, 2.0):
            do(P1, Variant(f"g2_h{hf}-{ht}_sl{sl}", "bbmr",
                           {"z": 2.0, "rsi_th": 30.0, "sl_atr": sl,
                            "hour_from": hf, "hour_to": ht}))

    print("\n=== G3: bbmr_z25_sl15 neighborhood (mid target) ===")
    print(HDR)
    for z in (2.25, 2.5, 2.75):
        for sl in (1.25, 1.5, 1.75):
            do(P1, Variant(f"g3_z{z}_sl{sl}", "bbmr",
                           {"z": z, "rsi_th": 30.0, "sl_atr": sl}))

    print("\n=== G4: H4 timeframe spot-check ===")
    print(HDR)
    h4_vars = [
        Variant("bbmr_atrTP", "bbmr",
                {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                 "target": "atr", "tp_atr": 2.0}),
        Variant("bbmr_z25_sl15", "bbmr",
                {"z": 2.5, "rsi_th": 30.0, "sl_atr": 1.5}),
        Variant("mr_signal_only", "mr", {"validity_gate": False}),
        Variant("emac_sl15tp3", "emac",
                {"validity_gate": False, "sl_atr": 1.5, "tp_atr": 3.0}),
        Variant("donch_base", "donch",
                {"sl_atr": 1.5, "tp_atr": 3.0}),
    ]
    for v in h4_vars:
        do(P4, v, tag="H4:")

    # --- bootstrap on the best H1 candidates ------------------------------
    print("\n=== bootstrap P(PF > 1), 5000 resamples (realistic cost) ===")
    candidates = [
        "g1_base|realistic",
        "g1_z2.0_sl1.0_tp2.0|realistic",
        "g1_z2.25_sl1.0_tp2.0|realistic",
        "bbmr_us_h17-23|realistic",   # from harness round-2 JSON if present
        "bbmr_z25_sl15|realistic",
    ]
    # merge in round-2 harness results (keys live under "variants")
    r2_path = OUT_DIR / f"eurchf_results_{time.strftime('%Y-%m-%d')}.json"
    if r2_path.exists():
        r2 = json.loads(r2_path.read_text(encoding="utf-8"))
        for k, v in r2.get("variants", {}).items():
            out["runs"].setdefault(f"r2:{k}", v)

    boot = {}
    for key in candidates:
        runs_key = key if key in out["runs"] else f"r2:{key}"
        if runs_key not in out["runs"]:
            continue
        pnls = [t["pnl"] for t in out["runs"][runs_key]["trade_list"]]
        if not pnls:
            continue
        rng = np.random.default_rng(42)
        arr = np.asarray(pnls)
        idx = rng.integers(0, len(arr), size=(5000, len(arr)))
        samp = arr[idx]
        gp = samp.clip(min=0).sum(axis=1)
        gl = (-samp.clip(max=0)).sum(axis=1)
        pf = np.where(gl > 0, gp / np.maximum(gl, 1e-9), np.inf)
        p_gt1 = float(np.mean(pf > 1.0))
        p_gt13 = float(np.mean(pf > 1.3))
        boot[runs_key] = {"n": len(arr), "p_pf_gt_1": round(p_gt1, 3),
                          "p_pf_gt_1_3": round(p_gt13, 3)}
        print(f"  {runs_key}: n={len(arr)} P(PF>1)={p_gt1:.3f} "
              f"P(PF>1.3)={p_gt13:.3f}")

    out["bootstrap"] = boot
    path = OUT_DIR / f"eurchf_robustness_{time.strftime('%Y-%m-%d')}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}  [{time.perf_counter() - t0:.1f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
