"""Parameter optimization pass for the auto-trader trend_zone stack.

Answers the 2026-10-05 finding that all 8 auto_trader symbols lose money
under the live trend_zone configuration (backtest: 83 trades, combined
-$859, PF<1 on 7/8 symbols) by sweeping the live SYMBOL_CONFIG parameters
through the live-faithful backtest.py engine, and keeping only parameter
sets that survive the same robustness gates the research atlas uses.

Method (per symbol):
  stage A — one-factor-at-a-time sensitivity around the live baseline:
            zone score, conf/score pair, min RR, SL/TP band scale, trend TF,
            exit management, regime filter, consecutive-loss guard.
  stage B — coordinate ascent from the baseline: repeatedly try the top-3
            levels of each axis (from stage A) holding the others at the
            current best until no axis improves (max 3 passes). Pass-1
            candidates are all stage A runs, so they cost nothing new.
  gates   — full window + trade-thirds + 70/30 chronological holdout +
            bootstrap P(PF>1); PROMOTE / PAPER / FAIL verdicts identical to
            research/atlas.py.
  costs   — realistic: live snapshot spread + 1pt slippage + $7/lot
            commission (the GATING model); canonical: 15pt fixed spread,
            vol-adjusted (stress model, matches backtest.py defaults and
            the 2026-10-05 run). Winners are re-scored under canonical.

Outputs:
    mt5_mcp/memory/research_lab/optimize_<date>.json   machine-readable
    mt5_mcp/memory/research_lab/optimize_<date>.md     report + proposals

Run:
    python -X utf8 mt5_mcp\\research\\optimize.py [--symbols XAUUSD,...]
                   [--bars 10000] [--jobs 6] [--stage-a-only] [--smoke]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mt5_mcp.backtest import BacktestParams, Backtester, load_frames  # noqa: E402

OUT_DIR = ROOT / "mt5_mcp" / "memory" / "research_lab"

LIVE_CONF = 65.0          # auto_trader MIN_CONFIDENCE (2026-10-05)
LIVE_SCORE = 70.0         # auto_trader MIN_TRADE_SCORE
LIVE_RISK = 0.25          # auto_trader RISK_PERCENT
BARS = 10000
BOOTSTRAP_N = 1000
HOLDOUT_FRACTION = 0.70
MIN_RANK_TRADES = 25
PF_RANK_CAP = 3.0
CANON_SPREAD = 15.0


# --------------------------------------------------------------------------- #
# robustness metrics (identical math to research/atlas.py)
# --------------------------------------------------------------------------- #
def _pf_of(pnls: np.ndarray) -> float:
    gp = float(pnls[pnls > 0].sum())
    gl = float(-pnls[pnls <= 0].sum())
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return round(gp / gl, 3)


def _thirds(pnls: np.ndarray) -> list[float]:
    if len(pnls) < 9:
        return [0.0, 0.0, 0.0]
    return [_pf_of(chunk) for chunk in np.array_split(pnls, 3)]


def _bootstrap(pnls: np.ndarray) -> float | None:
    if len(pnls) < 5:
        return None
    rng = np.random.default_rng(7)
    idx = rng.integers(0, len(pnls), size=(BOOTSTRAP_N, len(pnls)))
    s = pnls[idx]
    wins = np.where(s > 0, s, 0.0).sum(axis=1)
    losses = np.where(s <= 0, -s, 0.0).sum(axis=1)
    pf = np.where(losses > 0, wins / np.maximum(losses, 1e-9), np.inf)
    return round(float((pf > 1.0).mean()), 3)


def _holdout(pnls: np.ndarray, times: np.ndarray, start: str, end: str) -> dict:
    if len(pnls) < 2 or not start or not end:
        return {"n": 0, "pf": None}
    t0 = np.datetime64(start, "s")
    t1 = np.datetime64(end, "s")
    split = t0 + (t1 - t0) * HOLDOUT_FRACTION
    tail = pnls[times >= split]
    if len(tail) == 0:
        return {"n": 0, "pf": None}
    return {"n": int(len(tail)), "pf": _pf_of(tail)}


def _verdict(n: int, pf: float, thirds: list[float], boot: float | None,
             holdout: dict) -> str:
    thirds_ok = sum(1 for t in thirds if t >= 1.0)
    if (n >= 50 and pf >= 1.25 and thirds_ok == 3
            and boot is not None and boot >= 0.80
            and holdout.get("n", 0) >= 15 and (holdout.get("pf") or 0) >= 1.0):
        return "PROMOTE"
    if (n >= 40 and pf >= 1.10
            and boot is not None and boot >= 0.60 and thirds_ok >= 2):
        return "PAPER"
    return "FAIL"


def _digest(res: Any, cost: str) -> dict:
    trades = res.trades or []
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    times = (np.array([np.datetime64(t["entry_time"], "s") for t in trades])
             if trades else np.array([], dtype="datetime64[s]"))
    thirds = _thirds(pnls)
    holdout = _holdout(pnls, times, res.start, res.end)
    boot = _bootstrap(pnls)
    return {
        "cost": cost,
        "n": res.n_trades,
        "pf": res.profit_factor,
        "wr": res.win_rate,
        "net": res.net_pnl,
        "dd": res.max_drawdown_pct,
        "avg_rr": res.avg_rr,
        "thirds": thirds,
        "holdout": holdout,
        "boot": boot,
        "verdict": _verdict(res.n_trades, res.profit_factor, thirds, boot, holdout),
        "tp_sl_to": [
            sum(1 for t in trades if t["reason"] == "TP"),
            sum(1 for t in trades if t["reason"] == "SL"),
            sum(1 for t in trades if t["reason"] == "timeout"),
        ],
        "window": [res.start, res.end],
        "errors": res.errors,
    }


# --------------------------------------------------------------------------- #
# worker (module level so it pickles on Windows spawn)
# --------------------------------------------------------------------------- #
def _tfs_of(p: BacktestParams) -> list[str]:
    tfs = {p.trend_timeframe.upper(), p.confirmation_timeframe.upper()}
    tfs.update(tf.upper() for tf in p.zone_timeframes)
    return sorted(tfs)


def _run_job(job: dict) -> dict:
    p = BacktestParams(**job["params"])
    frames = load_frames(p.symbol, _tfs_of(p), p.bars,
                         use_cache=True, allow_mt5=False, ttl=job["ttl"])
    bt = Backtester(p, frames, job["info"])
    res = bt.run()
    return {
        "key": job["key"],
        "symbol": job["symbol"],
        "label": job["label"],
        "axes": job["axes_moved"],
        "cost": job["cost"],
        "digest": _digest(res, job["cost"]),
    }


def config_key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


def make_params(base: dict, overrides: dict) -> dict:
    cfg = dict(base)
    cfg.update(overrides)
    return cfg


def rank_key(digest: dict) -> tuple:
    if digest["n"] < MIN_RANK_TRADES:
        return (-1.0, -1e9, 0.0)
    return (min(digest["pf"], PF_RANK_CAP), digest["net"], -digest["dd"])


# --------------------------------------------------------------------------- #
# axes (every axis INCLUDES its baseline level; run dedupe is by config key)
# --------------------------------------------------------------------------- #
def stage_a_axes(base: dict, smoke: bool = False) -> dict[str, list[tuple[str, dict]]]:
    sl_min, sl_max = float(base["sl_min"]), float(base["sl_max"])
    tp_min, tp_max = float(base["tp_min"]), float(base["tp_max"])
    axes: dict[str, list[tuple[str, dict]]] = {
        "zone": [(f"z{int(z)}", {"min_zone_score": z})
                 for z in (50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0)],
        "confscore": [
            (f"c{int(c)}s{int(s)}",
             {"min_confidence_percent": c, "min_trade_score": s})
            for c, s in ((55.0, 60.0), (60.0, 65.0), (65.0, 70.0),
                         (70.0, 75.0), (75.0, 80.0))
        ],
        "rr": [(f"rr{r:g}", {"min_rr": r}) for r in (1.8, 2.0, 2.2, 2.5, 3.0)],
        "slscale": [(f"sl{x:g}", {"sl_min": round(sl_min * x, 1),
                                  "sl_max": round(sl_max * x, 1)})
                    for x in (0.7, 1.0, 1.4)],
        "tpscale": [(f"tp{x:g}", {"tp_min": round(tp_min * x, 1),
                                  "tp_max": round(tp_max * x, 1)})
                    for x in (0.7, 1.0, 1.3)],
        "trendtf": [("H4", {"trend_timeframe": "H4"}),
                    ("H1", {"trend_timeframe": "H1"})],
        "exits": [
            ("live", {}),
            ("notrail", {"use_trailing_stop": False}),
            ("nopartial", {"use_partial_tp": False}),
            ("plain", {"use_trailing_stop": False, "use_partial_tp": False}),
            ("hold120", {"adaptive_timeout_bars": 120}),
            ("hold200", {"use_adaptive_timeout": False}),
        ],
        "regime": [("", {}), ("trend", {"regime_filter": "trend"})],
        "guard": [("g0", {}), ("g3", {"max_consecutive_losses": 3})],
    }
    if smoke:
        keep = {"zone", "rr", "exits"}
        return {a: v for a, v in axes.items() if a in keep}
    return axes


def default_levels(base: dict, axes: dict) -> dict[str, str]:
    """Label of the level equal to the live baseline, per axis."""
    out: dict[str, str] = {}
    base_key = config_key(base)
    for axis, levels in axes.items():
        out[axis] = levels[0][0]
        for label, ov in levels:
            if config_key(make_params(base, ov)) == base_key:
                out[axis] = label
                break
    return out


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _row(label: str, dig: dict) -> str:
    thirds = "/".join(f"{t:.2f}" for t in dig["thirds"])
    ho = dig["holdout"]
    to, sl_, tout = dig["tp_sl_to"]
    return (f"| {label} | {dig['cost']} | {dig['n']} | {dig['wr']:.1f} | "
            f"{dig['pf']:.2f} | {dig['net']:+.0f} | {dig['dd']:.1f} | "
            f"{thirds} | {ho['n']}/{ho['pf'] if ho['pf'] is not None else '-'} | "
            f"{'-' if dig['boot'] is None else dig['boot']} | "
            f"{to}/{sl_}/{tout} | **{dig['verdict']}** |")


def _param_diff(cfg: dict, base: dict) -> str:
    keys = ["min_zone_score", "min_confidence_percent", "min_trade_score",
            "min_rr", "sl_min", "sl_max", "tp_min", "tp_max",
            "trend_timeframe", "use_trailing_stop", "use_partial_tp",
            "use_adaptive_timeout", "adaptive_timeout_bars",
            "regime_filter", "max_consecutive_losses"]
    diffs = [f"{k}={cfg[k]}" for k in keys
             if k in cfg and cfg.get(k) != base.get(k)]
    return ", ".join(diffs) or "baseline"


def write_report(payload: dict, md_path: Path, json_path: Path) -> None:
    lines: list[str] = [
        f"# Auto-trader parameter optimization — {payload['generated']}",
        "",
        "Engine: `backtest.py` Backtester (live-faithful trend_zone replay), "
        f"{payload['bars']} H1 base bars, risk {LIVE_RISK:.2f}%/trade, "
        "$7/lot commission, slippage 1pt.",
        "Gated on **realistic** cost (live snapshot spread); canonical "
        "(15pt fixed, vol-adjusted) shown for baselines + winners. Gates "
        "identical to the research atlas: PROMOTE n>=50 PF>=1.25 thirds>=1.0 "
        "boot>=0.80 holdout n>=15 pf>=1.0 | PAPER n>=40 PF>=1.10 boot>=0.60 "
        "thirds 2/3.",
        "",
        "Context: 2026-10-05 backtest of the live configs = 83 trades, "
        "combined -$859.26, 28.9% WR (PF<1 on 7/8 symbols).",
        "",
    ]
    if payload.get("stage_a_only"):
        lines += ["(stage A only — coordinate ascent not run)", ""]
    for symbol, sym in payload["symbols"].items():
        lines.append(f"## {symbol}")
        lines.append("")
        lines.append("| config | cost | n | WR% | PF | net$ | DD% | thirds | "
                     "holdout n/PF | boot | TP/SL/TO | verdict |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---|---|---:|---|---|")
        lines.append(_row("baseline (live)", sym["baseline"]["realistic"]))
        if sym["baseline"].get("canonical"):
            lines.append(_row("baseline (live)", sym["baseline"]["canonical"]))
        win = sym.get("winner")
        if win:
            lines.append(_row("winner", win["digest_realistic"]))
            if win.get("digest_canonical"):
                lines.append(_row("winner", win["digest_canonical"]))
            lines.append("")
            lines.append(f"- winner diff: "
                         f"`{_param_diff(win['config'], sym['base_config'])}`")
            if win.get("path"):
                lines.append(f"- ascent: {' -> '.join(win['path'])}")
        elif not payload.get("stage_a_only"):
            lines.append("")
            lines.append("- no combined improvement over baseline")
        sens = sym.get("sensitivity") or []
        if sens:
            lines.append("")
            lines.append("Top single-axis moves (stage A, vs baseline PF "
                         f"{sym['baseline']['realistic']['pf']:.2f}):")
            for axis, label, dig in sens[:4]:
                if dig["n"] >= 10:
                    lines.append(
                        f"- {axis}={label}: n {dig['n']}, PF {dig['pf']:.2f}, "
                        f"net {dig['net']:+.0f}, verdict {dig['verdict']}")
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=1, default=str),
                         encoding="utf-8")


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="auto-trader parameter optimization")
    ap.add_argument("--symbols", default="",
                    help="comma list (default: SYMBOL_CONFIG keys)")
    ap.add_argument("--bars", type=int, default=BARS)
    ap.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 4))
    ap.add_argument("--stage-a-only", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    from mt5_mcp.auto_trader import SYMBOL_CONFIG
    from mt5_mcp.client import get_client

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               or list(SYMBOL_CONFIG))
    if args.smoke:
        symbols = symbols[:1]

    t0 = time.perf_counter()
    client = get_client()
    client.ensure_connected()

    baselines: dict[str, dict] = {}
    infos: dict[str, dict] = {}
    spreads: dict[str, float] = {}
    for symbol in symbols:
        cfg = SYMBOL_CONFIG[symbol]
        baselines[symbol] = {
            "symbol": symbol,
            "trend_timeframe": "H4",
            "zone_timeframes": list(cfg["zone_timeframes"]),
            "confirmation_timeframe": "H1",
            "min_zone_score": float(cfg["min_zone_score"]),
            "min_trade_score": LIVE_SCORE,
            "min_confidence_percent": LIVE_CONF,
            "lookback": 400,
            "risk_percent": LIVE_RISK,
            "tp_min": float(cfg["tp_min"]),
            "tp_max": float(cfg["tp_max"]),
            "sl_min": float(cfg["sl_min"]),
            "sl_max": float(cfg["sl_max"]),
            "min_rr": float(cfg["min_rr"]),
            "bars": args.bars,
        }
        info = client.symbol_info(symbol) or {}
        infos[symbol] = info
        spread = float(info.get("spread", 0) or 0)
        spreads[symbol] = spread if spread > 0 else CANON_SPREAD
        frames = load_frames(symbol, ["H1", "H4"], args.bars,
                             use_cache=True, allow_mt5=True)
        print(f"[{symbol}] spread {spreads[symbol]:g}pt, frames: "
              f"{ {tf: len(f) for tf, f in frames.items()} } "
              f"[{time.perf_counter() - t0:.0f}s]", flush=True)

    def cost_for(symbol: str, model: str) -> dict:
        if model == "canonical":
            return {"spread_points": CANON_SPREAD, "use_vol_spread": True}
        return {"spread_points": spreads[symbol], "use_vol_spread": False}

    cache: dict[str, dict[str, dict]] = {s: {} for s in symbols}
    pool = ProcessPoolExecutor(max_workers=args.jobs)

    def submit(jobs: list[dict]) -> int:
        if not jobs:
            return 0
        n = 0
        for r in pool.map(_run_job, jobs):
            cache[r["symbol"]][r["key"]] = {
                "label": r["label"], "axes": r["axes"],
                "cost": r["cost"], "digest": r["digest"],
            }
            n += 1
        return n

    def make_job(symbol: str, cfg: dict, model: str, label: str,
                 axes_moved: str) -> dict:
        return {
            "params": cfg, "key": config_key(cfg), "symbol": symbol,
            "label": label, "axes_moved": axes_moved, "cost": model,
            "info": infos[symbol], "ttl": 900.0,
        }

    def baseline_cfg(symbol: str, model: str) -> dict:
        return make_params(baselines[symbol], cost_for(symbol, model))

    # ---------------- stage A ----------------
    print(f"\n=== stage A: sensitivity sweeps ({len(symbols)} symbols) ===",
          flush=True)
    stage_a_jobs: list[dict] = []
    for symbol in symbols:
        base = baselines[symbol]
        axes = stage_a_axes(base, smoke=args.smoke)
        cfg = baseline_cfg(symbol, "realistic")
        if config_key(cfg) not in cache[symbol]:
            stage_a_jobs.append(make_job(symbol, cfg, "realistic", "baseline", ""))
        for axis, levels in axes.items():
            for label, ov in levels:
                cfg = make_params(base, {**ov, **cost_for(symbol, "realistic")})
                if config_key(cfg) not in cache[symbol]:
                    stage_a_jobs.append(
                        make_job(symbol, cfg, "realistic", label, axis))
    tA = time.perf_counter()
    nA = submit(stage_a_jobs)
    print(f"stage A: {nA} new runs in {time.perf_counter() - tA:.0f}s", flush=True)

    sensitivity: dict[str, list] = {}
    for symbol in symbols:
        base_rec = cache[symbol][config_key(baseline_cfg(symbol, "realistic"))]
        axes = stage_a_axes(baselines[symbol], smoke=args.smoke)
        rows = []
        for axis, levels in axes.items():
            for label, ov in levels:
                cfg = make_params(baselines[symbol],
                                  {**ov, **cost_for(symbol, "realistic")})
                rec = cache[symbol].get(config_key(cfg))
                if rec:
                    rows.append((axis, label, rec["digest"]))
        rows.sort(key=lambda r: rank_key(r[2]), reverse=True)
        sensitivity[symbol] = rows
        top = rows[0] if rows else None
        bd = base_rec["digest"]
        msg = (f"[{symbol}] baseline n={bd['n']} PF={bd['pf']:.2f} "
               f"net={bd['net']:+.0f} {bd['verdict']}")
        if top:
            msg += (f" | best axis {top[0]}={top[1]}: PF={top[2]['pf']:.2f} "
                    f"n={top[2]['n']} {top[2]['verdict']}")
        print(msg, flush=True)

    # ---------------- stage B: coordinate ascent ----------------
    winners: dict[str, dict] = {}
    if not args.stage_a_only:
        print("\n=== stage B: coordinate ascent ===", flush=True)
        for symbol in symbols:
            base = baselines[symbol]
            axes = stage_a_axes(base, smoke=args.smoke)
            defs = default_levels(base, axes)
            ranked: dict[str, list[str]] = {}
            for axis, levels in axes.items():
                scored = []
                for label, ov in levels:
                    cfg = make_params(base, {**ov, **cost_for(symbol, "realistic")})
                    rec = cache[symbol].get(config_key(cfg))
                    if rec:
                        scored.append((rank_key(rec["digest"]), label, ov))
                scored.sort(reverse=True, key=lambda x: x[0])
                labels = [s[1] for s in scored[:3]]
                if defs[axis] not in labels:
                    labels.append(defs[axis])
                ranked[axis] = labels

            current: dict[str, tuple[str, dict]] = {
                a: (defs[a], dict(next(o for l, o in axes[a] if l == defs[a])))
                for a in axes
            }

            def combined(over_axis: str | None = None,
                         over_ov: dict | None = None) -> dict:
                ov_all: dict = {}
                for a, (_l, o) in current.items():
                    if a == over_axis:
                        continue
                    ov_all.update(o)
                if over_axis is not None and over_ov is not None:
                    ov_all.update(over_ov)
                return make_params(base, {**ov_all,
                                          **cost_for(symbol, "realistic")})

            cur_cfg = combined()
            cur_rec = cache[symbol].get(config_key(cur_cfg))
            best_rank = (rank_key(cur_rec["digest"]) if cur_rec
                         else (-1.0, -1e9, 0.0))
            path: list[str] = []
            for _pass in range(3):
                improved = False
                for axis in ranked:
                    cand_jobs = []
                    for label in ranked[axis]:
                        if label == current[axis][0]:
                            continue
                        ov = next(o for l, o in axes[axis] if l == label)
                        cfg = combined(axis, ov)
                        if config_key(cfg) not in cache[symbol]:
                            cand_jobs.append(make_job(symbol, cfg, "realistic",
                                                      label, axis))
                    if cand_jobs:
                        submit(cand_jobs)
                    best_label, best_ov = current[axis]
                    for label in ranked[axis]:
                        if label == current[axis][0]:
                            continue
                        ov = next(o for l, o in axes[axis] if l == label)
                        cfg = combined(axis, ov)
                        rec = cache[symbol].get(config_key(cfg))
                        if rec and rank_key(rec["digest"]) > best_rank:
                            best_rank = rank_key(rec["digest"])
                            best_label, best_ov = label, ov
                    if best_label != current[axis][0]:
                        current[axis] = (best_label, best_ov)
                        improved = True
                        path.append(f"{axis}->{best_label}")
                if not improved:
                    break
            win_cfg = combined()
            win_rec = cache[symbol].get(config_key(win_cfg))
            if win_rec and rank_key(win_rec["digest"]) > rank_key(
                    cache[symbol][config_key(baseline_cfg(symbol, "realistic"))]["digest"]):
                canon_cfg = dict(win_cfg)
                canon_cfg.update(cost_for(symbol, "canonical"))
                if config_key(canon_cfg) not in cache[symbol]:
                    submit([make_job(symbol, canon_cfg, "canonical",
                                     "winner", "winner")])
                winners[symbol] = {
                    "config": win_cfg,
                    "digest_realistic": win_rec["digest"],
                    "digest_canonical": (cache[symbol].get(config_key(canon_cfg))
                                         or {}).get("digest"),
                    "path": path,
                }
                wd = win_rec["digest"]
                print(f"[{symbol}] winner: {' '.join(path)} -> n={wd['n']} "
                      f"PF={wd['pf']:.2f} net={wd['net']:+.0f} {wd['verdict']}",
                      flush=True)
            else:
                print(f"[{symbol}] no combined improvement over baseline",
                      flush=True)

        # ---------------- canonical stress on baselines ----------------
        print("\n=== canonical stress on baselines ===", flush=True)
        canon_jobs = []
        for symbol in symbols:
            cfg = baseline_cfg(symbol, "canonical")
            if config_key(cfg) not in cache[symbol]:
                canon_jobs.append(make_job(symbol, cfg, "canonical",
                                           "baseline", ""))
        nC = submit(canon_jobs)
        print(f"canonical baselines: {nC} runs", flush=True)

    pool.shutdown()

    # ---------------- payload + reports ----------------
    stamp = time.strftime("%Y-%m-%d")
    payload: dict[str, Any] = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bars": args.bars,
        "stage_a_only": args.stage_a_only,
        "spreads_snapshot": spreads,
        "symbols": {},
    }
    for symbol in symbols:
        sym: dict[str, Any] = {
            "base_config": baselines[symbol],
            "baseline": {
                "realistic": cache[symbol][
                    config_key(baseline_cfg(symbol, "realistic"))]["digest"],
            },
            "sensitivity": [[a, l, d] for a, l, d in sensitivity[symbol]],
        }
        canon_key = config_key(baseline_cfg(symbol, "canonical"))
        if canon_key in cache[symbol]:
            sym["baseline"]["canonical"] = cache[symbol][canon_key]["digest"]
        if symbol in winners:
            sym["winner"] = winners[symbol]
        payload["symbols"][symbol] = sym

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"optimize_{stamp}.json"
    md_path = OUT_DIR / f"optimize_{stamp}.md"
    write_report(payload, md_path, json_path)
    print(f"\nwrote {md_path}", flush=True)
    print(f"wrote {json_path}", flush=True)
    print(f"total [{time.perf_counter() - t0:.0f}s]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
