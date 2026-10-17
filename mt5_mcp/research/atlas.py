"""Per-pair strategy atlas: sweep every strategy family against every symbol.

For each symbol in the atlas universe, runs each canonical strategy variant
(live-faithful params) through the lookahead-safe harness engine on deep
history (copy_rates_range from 2021 — bypasses the terminal MaxBars cap),
under two cost models:

    realistic  — actual per-bar broker spread; zero-spread bars (57% of old
                 history) backfilled with the symbol's median non-zero spread;
                 slippage 1 point. GATED on this model.
    canonical  — backtest.py-style punitive fixed 15-point spread x pip,
                 vol-adjusted, 1 pip slippage. Reported for transparency.

Each variant runs full-window + a 70/30 chronological holdout. Selection gates:

    PROMOTE  n >= 50, PF >= 1.25, thirds all >= 1.0, bootstrap P(PF>1) >= 0.80,
             holdout n >= 15 and holdout PF >= 1.0
    PAPER    n >= 40, PF >= 1.10, bootstrap >= 0.60, at least 2/3 thirds >= 1.0
    FAIL     everything else

trend_zone rows come from the existing BACKTEST_PF table (backtest.py engine,
2026-09-27) and are marked legacy (no robustness gates computable).

Outputs:
    mt5_mcp/memory/research_lab/atlas_<date>.json   machine-readable results
    mt5_mcp/memory/research_lab/atlas_<date>.md     report + routing proposals
    one Telegram summary (unless --no-notify); drift vs the previous atlas
    run is reported when an earlier atlas_*.json exists.

Run:
    $env:PYTHONPATH='E:\Project\Mt5 Ea'
    python -X utf8 mt5_mcp\research\atlas.py [--symbols EURJPY,BTCUSD] [--no-notify]

Weekly scheduled task: MT5R_Atlas.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5  # noqa: E402
from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size, pip_value_per_lot  # noqa: E402
from mt5_mcp.multi_strategy import BACKTEST_PF, STRATEGY_ROUTING  # noqa: E402
from mt5_mcp.notify import _esc, notify  # noqa: E402

# mt5_mcp/research.py (module) shadows the research/ directory, so the EURCHF
# harness cannot be imported as mt5_mcp.research.eurchf.harness — load by path.
_HARNESS_PATH = ROOT / "mt5_mcp" / "research" / "eurchf" / "harness.py"
_spec = importlib.util.spec_from_file_location("atlas_eurchf_harness", _HARNESS_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load harness from {_HARNESS_PATH}")
_harness = importlib.util.module_from_spec(_spec)
sys.modules["atlas_eurchf_harness"] = _harness
_spec.loader.exec_module(_harness)

WARMUP = _harness.WARMUP
CostModel = _harness.CostModel
Variant = _harness.Variant
prepare = _harness.prepare
run_variant = _harness.run_variant

OUT_DIR = ROOT / "mt5_mcp" / "memory" / "research_lab"

ATLAS_SYMBOLS: list[str] = [
    "EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "CHFJPY", "EURJPY", "GBPJPY", "NZDJPY", "GBPCHF", "NZDCAD",
    "AUDNZD", "EURAUD", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL", "JP225",
    "USDJPY", "BTCUSD", "ETHUSD", "SPX500", "NDX100",
]

DEEP_FROM = dt.datetime(2021, 1, 1)
BOOTSTRAP_N = 1000
HOLDOUT_FRACTION = 0.70

MT5_TF = {"H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4"}

ROUTING_FAMILIES: list[Variant] = [
    Variant("mean_reversion", "mr", {"validity_gate": True}),
    Variant("momentum_breakout", "mb", {"validity_gate": True}),
    Variant("ema_crossover", "emac", {"validity_gate": True}),
    Variant("bb_mr_fade", "bbmr",
            {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5, "target": "atr", "tp_atr": 2.0}),
    Variant("btc_trend_following", "btct", {"sl_atr": 2.5, "tp_atr": 4.0}),
]

CANDIDATE_FAMILIES: list[Variant] = [
    Variant("donchian", "donch", {"sl_atr": 1.5, "tp_atr": 3.0}),
    Variant("pullback", "pull", {"sl_atr": 1.5, "tp_atr": 3.0}),
    Variant("squeeze", "sqz", {"pct_th": 0.25, "sl_atr": 1.5, "tp_atr": 3.0}),
    Variant("daybreak", "daybrk", {"sl_atr": 1.5, "tp_atr": 2.5}),
]

H4_VARIANTS = {"btc_trend_following"}


def _deep_frame(symbol: str, tf: str) -> pd.DataFrame | None:
    """Deep history via copy_rates_range (bypasses the terminal MaxBars cap)."""
    mt5_tf = getattr(mt5, MT5_TF[tf])
    rates = None
    try:
        rates = mt5.copy_rates_range(symbol, mt5_tf, DEEP_FROM, dt.datetime.now())
    except Exception:
        rates = None
    if rates is None or len(rates) == 0:
        try:
            if not mt5.initialize():
                return None
            rates = mt5.copy_rates_range(symbol, mt5_tf, DEEP_FROM, dt.datetime.now())
        except Exception:
            return None
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)[["time", "open", "high", "low", "close", "spread"]]
    if np.issubdtype(df["time"].dtype, np.integer):
        unit = "s"
        if pd.to_datetime(df["time"].iloc[0], unit="s").year < 2005:
            unit = "ms"
        df["time"] = pd.to_datetime(df["time"], unit=unit)
    nonzero = df.loc[df["spread"] > 0, "spread"]
    fill = float(nonzero.median()) if len(nonzero) else 15.0
    df["spread"] = df["spread"].mask(df["spread"] <= 0, fill)
    return df


def _pf(pnls: np.ndarray) -> float:
    gp = float(pnls[pnls > 0].sum())
    gl = float(-pnls[pnls <= 0].sum())
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return round(gp / gl, 3)


def _thirds(pnls: np.ndarray) -> list[float]:
    if len(pnls) < 9:
        return [0.0, 0.0, 0.0]
    return [_pf(chunk) for chunk in np.array_split(pnls, 3)]


def _bootstrap_pf(pnls: np.ndarray) -> float | None:
    if len(pnls) < 5:
        return None
    rng = np.random.default_rng(7)
    idx = rng.integers(0, len(pnls), size=(BOOTSTRAP_N, len(pnls)))
    s = pnls[idx]
    wins = np.where(s > 0, s, 0.0).sum(axis=1)
    losses = np.where(s <= 0, -s, 0.0).sum(axis=1)
    pf = np.where(losses > 0, wins / np.maximum(losses, 1e-9), np.inf)
    return round(float((pf > 1.0).mean()), 3)


def _verdict(m: dict[str, Any]) -> str:
    thirds_ok = sum(1 for t in m["thirds"] if t >= 1.0)
    boot = m.get("bootstrap_pf_gt1")
    if (m["trades"] >= 50 and m["pf"] >= 1.25 and thirds_ok == 3
            and boot is not None and boot >= 0.80
            and m["holdout"]["trades"] >= 15 and m["holdout"]["pf"] >= 1.0):
        return "PROMOTE"
    if (m["trades"] >= 40 and m["pf"] >= 1.10
            and boot is not None and boot >= 0.60 and thirds_ok >= 2):
        return "PAPER"
    return "FAIL"


def _measure(stats: dict[str, Any]) -> dict[str, Any]:
    pnls = np.array([t["pnl"] for t in stats.get("trade_list", [])], dtype=float)
    return {
        "trades": stats["trades"],
        "pf": stats["pf"],
        "win_rate": stats["win_rate"],
        "net_pnl": stats["net_pnl"],
        "max_dd_pct": stats["max_dd_pct"],
        "avg_r": stats["avg_r"],
        "thirds": _thirds(pnls),
        "bootstrap_pf_gt1": _bootstrap_pf(pnls),
        "window": stats.get("window"),
        "tp_sl_timeout": [stats["tp_hits"], stats["sl_hits"], stats["timeouts"]],
    }


def _previous_atlas() -> Path | None:
    try:
        prior = sorted(p for p in OUT_DIR.glob("atlas_*.json")
                       if p.name != f"atlas_{time.strftime('%Y-%m-%d')}.json")
        return prior[-1] if prior else None
    except OSError:
        return None


def sweep(symbols: list[str]) -> dict[str, Any]:
    client = get_client()
    client.ensure_connected()
    variants = ROUTING_FAMILIES + CANDIDATE_FAMILIES
    results: dict[str, Any] = {}

    for si, symbol in enumerate(symbols, 1):
        t0 = time.perf_counter()
        entry: dict[str, Any] = {"strategies": {}, "notes": []}
        try:
            info = client.symbol_info(symbol)
            if not info:
                entry["notes"].append("symbol_info unavailable")
                results[symbol] = entry
                continue
            pip = pip_size(info)
            pip_value = pip_value_per_lot(info)
            point = float(info.get("point", 1e-5))

            df_h1 = _deep_frame(symbol, "H1")
            df_h4 = _deep_frame(symbol, "H4")
            if df_h1 is None and df_h4 is None:
                entry["notes"].append("no H1/H4 data")
                results[symbol] = entry
                continue
            P1 = prepare(df_h1, point) if df_h1 is not None else None
            P4 = prepare(df_h4, point) if df_h4 is not None else None
            entry["bars"] = {"H1": 0 if df_h1 is None else len(df_h1),
                             "H4": 0 if df_h4 is None else len(df_h4)}

            cost_real = CostModel("realistic", use_bar_spread=True,
                                  slippage_price=1.0 * point, vol_spread=False)
            cost_canon = CostModel("canonical", use_bar_spread=False,
                                   spread_points=15.0,
                                   slippage_price=1.0 * pip, vol_spread=True)

            for v in variants:
                P = P4 if (v.name in H4_VARIANTS and P4 is not None) else P1
                if P is None:
                    entry["notes"].append(f"{v.name}: no frame")
                    continue
                split = WARMUP + int(HOLDOUT_FRACTION * max(0, len(P.t) - 1 - WARMUP))
                try:
                    full = run_variant(P, v, cost_real, info, pip, pip_value)
                    hold = run_variant(P, v, cost_real, info, pip, pip_value,
                                       start=split, end=None)
                    canon = run_variant(P, v, cost_canon, info, pip, pip_value)
                    m = _measure(full)
                    m["holdout"] = _measure(hold)
                    m["holdout"]["split_time"] = (
                        str(P.t[split]) if split < len(P.t) else None
                    )
                    m["canonical"] = {
                        "trades": canon["trades"], "pf": canon["pf"],
                        "net_pnl": canon["net_pnl"],
                    }
                    m["verdict"] = _verdict(m)
                    m["routing_family"] = v.name in {x.name for x in ROUTING_FAMILIES}
                    entry["strategies"][v.name] = m
                except Exception:
                    entry["notes"].append(
                        f"{v.name}: {traceback.format_exc().splitlines()[-1]}"
                    )
                    continue
        except Exception:
            entry["notes"].append("ERROR\n" + traceback.format_exc())
        results[symbol] = entry
        best = max(
            ((n, m["pf"], m["verdict"])
             for n, m in entry["strategies"].items()
             if m["trades"] > 0 and m["routing_family"]),
            key=lambda x: (x[2] == "PROMOTE", x[1]),
            default=None,
        )
        print(f"[{si}/{len(symbols)}] {symbol:<8} "
              f"best={best[0] if best else '-':<20} pf={best[1] if best else 0:>6} "
              f"{best[2] if best else '':<8} [{time.perf_counter() - t0:.0f}s]",
              flush=True)
    return results


def proposals(results: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for symbol, entry in results.items():
        current = STRATEGY_ROUTING.get(symbol)
        current_pf = BACKTEST_PF.get((symbol, current)) if current else None
        eligible = [
            (name, m) for name, m in entry["strategies"].items()
            if m.get("routing_family") and m["verdict"] == "PROMOTE"
        ]
        if not eligible:
            out.append({"symbol": symbol, "action": "KEEP/none",
                        "current": current, "current_pf": current_pf,
                        "reason": "no PROMOTE strategy"})
            continue
        best_name, best_m = max(eligible, key=lambda x: x[1]["pf"])
        baseline = current_pf if current_pf is not None else 0.0
        if best_name == current:
            out.append({"symbol": symbol, "action": "KEEP", "current": current,
                        "current_pf": current_pf,
                        "reason": "routed strategy re-promoted"})
        elif best_m["pf"] >= baseline + 0.1:
            out.append({"symbol": symbol, "action": "CHANGE",
                        "proposed": best_name, "proposed_pf": best_m["pf"],
                        "current": current, "current_pf": current_pf,
                        "reason": f"gated PF {best_m['pf']} vs current {current_pf}"})
        else:
            out.append({"symbol": symbol, "action": "KEEP", "current": current,
                        "current_pf": current_pf,
                        "reason": f"best promote {best_name} pf {best_m['pf']} "
                                  f"does not beat {baseline} + 0.1"})
    return out


def drift_check(results: dict[str, Any]) -> list[str]:
    prev_path = _previous_atlas()
    if prev_path is None:
        return ["baseline established (no prior atlas run)"]
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read prior atlas {prev_path.name}: {exc}"]
    alerts = []
    for symbol, entry in results.items():
        old_entry = prev.get("symbols", prev).get(symbol, {})
        old_strats = old_entry.get("strategies", {})
        for name, m in entry["strategies"].items():
            om = old_strats.get(name)
            if not om:
                continue
            if om["pf"] >= 1.25 and m["pf"] < 1.10:
                alerts.append(f"DRIFT {symbol} {name}: PF {om['pf']} -> {m['pf']}")
            elif om["verdict"] == "PROMOTE" and m["verdict"] == "FAIL":
                alerts.append(f"DOWNGRADE {symbol} {name}: PROMOTE -> FAIL")
    if not alerts:
        alerts.append(f"no drift vs {prev_path.name}")
    return alerts


def write_reports(results: dict[str, Any], props: list[dict[str, Any]],
                  drifts: list[str]) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cost_gated": "realistic (bar spread w/ zero-fill, 1pt slip, $7/lot)",
        "cost_reported_also": "canonical (15pt fixed spread, vol-adjusted, 1 pip slip)",
        "gates": "PROMOTE: n>=50 PF>=1.25 thirds>=1.0 boot>=0.80 holdout n>=15 pf>=1.0 | "
                 "PAPER: n>=40 PF>=1.10 boot>=0.60 thirds 2/3 | else FAIL",
        "holdout_fraction": HOLDOUT_FRACTION,
        "history_from": str(DEEP_FROM.date()),
        "symbols": results,
        "proposals": props,
        "drift": drifts,
    }
    json_path = OUT_DIR / f"atlas_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    lines = [
        f"# Strategy atlas — {stamp}",
        "",
        "Gated on **realistic** cost (historical spread with zero-fill, 1pt slip); "
        "canonical PF shown for reference. "
        f"History from {DEEP_FROM.date()}, "
        f"chronological {HOLDOUT_FRACTION:.0%}/{1-HOLDOUT_FRACTION:.0%} train/holdout split, "
        f"{BOOTSTRAP_N}-draw bootstrap, trade-thirds stability.",
        "",
        "| symbol | strategy | n | PF | canon PF | thirds | boot P(PF>1) "
        "| holdout n/PF | verdict |",
        "|---|---|---:|---:|---:|---|---:|---|---|",
    ]
    for symbol, entry in results.items():
        for name, m in sorted(entry["strategies"].items(),
                              key=lambda kv: -kv[1]["pf"]):
            boot = m.get("bootstrap_pf_gt1")
            canon = m.get("canonical", {})
            lines.append(
                f"| {symbol} | {name} | {m['trades']} | {m['pf']} | "
                f"{canon.get('pf', '-')} | "
                f"{'/'.join(f'{t:.2f}' for t in m['thirds'])} | "
                f"{'-' if boot is None else boot} | "
                f"{m['holdout']['trades']}/{m['holdout']['pf']} | **{m['verdict']}** |"
            )
        for note in entry.get("notes", []):
            lines.append(f"| {symbol} | — | | | | | | | {note[:70]} |")
    lines += ["", "## Routing proposals", ""]
    for p in props:
        lines.append(f"- **{p['symbol']}**: {p['action']} — {p['reason']}")
    lines += ["", "## Drift vs previous atlas", ""]
    lines += [f"- {d}" for d in drifts]
    lines += [
        "",
        "## Legacy trend_zone PFs (BACKTEST_PF, backtest.py canonical, 2026-09-27)",
        "",
    ]
    for (sym, strat), pf in sorted(BACKTEST_PF.items()):
        if strat == "trend_zone":
            lines.append(f"- {sym}: {pf}")
    md_path = OUT_DIR / f"atlas_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def notify_summary(results: dict[str, Any], props: list[dict[str, Any]],
                   drifts: list[str], md_path: Path) -> bool:
    counts = {"PROMOTE": 0, "PAPER": 0, "FAIL": 0}
    for entry in results.values():
        for m in entry["strategies"].values():
            counts[m["verdict"]] = counts.get(m["verdict"], 0) + 1
    changes = [p for p in props if p["action"] == "CHANGE"]
    plain = [
        f"ATLAS {time.strftime('%Y-%m-%d')}: {counts['PROMOTE']} promote / "
        f"{counts['PAPER']} paper / {counts['FAIL']} fail across {len(results)} symbols",
        *[f"CHANGE {p['symbol']} -> {p['proposed']} (PF {p['proposed_pf']})"
          for p in changes],
        *drifts[:5],
    ]
    html = [
        f"<b>STRATEGY ATLAS</b> {time.strftime('%Y-%m-%d')}",
        f"promote <code>{counts['PROMOTE']}</code> | paper <code>{counts['PAPER']}</code> | "
        f"fail <code>{counts['FAIL']}</code> | symbols <code>{len(results)}</code>",
    ]
    if changes:
        html.append("\n<b>Routing changes:</b>")
        html += [f"· <b>{_esc(p['symbol'])}</b> → {_esc(p['proposed'])} "
                 f"(PF <code>{p['proposed_pf']}</code>, was {_esc(str(p.get('current')))} "
                 f"PF {p.get('current_pf')})" for p in changes]
    if drifts:
        html.append("\n<b>Drift:</b>")
        html += [f"· {_esc(d)}" for d in drifts[:5]]
    html.append(f"\n{_esc(md_path.name)}")
    return notify("research_atlas", "\n".join(plain), force=True,
                  html="\n".join(html))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="per-pair strategy atlas sweep")
    ap.add_argument("--symbols", default="",
                    help="comma list (default: full atlas universe)")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] \
        or list(ATLAS_SYMBOLS)
    t0 = time.perf_counter()
    print(f"atlas: {len(symbols)} symbols, "
          f"{len(ROUTING_FAMILIES)} routing + {len(CANDIDATE_FAMILIES)} candidate "
          f"families, history from {DEEP_FROM.date()}", flush=True)
    results = sweep(symbols)
    props = proposals(results)
    drifts = drift_check(results)
    json_path, md_path = write_reports(results, props, drifts)
    changes = [p for p in props if p["action"] == "CHANGE"]
    print(f"\nproposals: {len(changes)} change(s):")
    for p in changes:
        print(f"  {p['symbol']}: {p.get('current')} (PF {p.get('current_pf')}) -> "
              f"{p['proposed']} (PF {p['proposed_pf']})")
    print("drift:")
    for d in drifts:
        print(f"  {d}")
    if not args.no_notify:
        sent = notify_summary(results, props, drifts, md_path)
        print(f"notify sent={sent}")
    print(f"wrote {json_path.name} + {md_path.name} "
          f"[{time.perf_counter() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
