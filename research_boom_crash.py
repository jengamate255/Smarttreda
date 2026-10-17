"""Robust backtest of the live Boom/Crash Spike+Drift watcher (trade_watch.py).

Live-aligned baseline (thr 2.5 ATR, 8 ticks, spike age 15, lookback 60,
SL buffer 2.0 ATR, chandelier exits) plus a 27-combo grid
(threshold x SL buffer x max spike age) over all 20 Deriv synthetic
indices, 35 days of M1 bars each.

PnL is spread-adjusted (round trip = one full median bar spread) and
judged with atlas-style robustness gates: chronological thirds, 70/30
holdout, and a 1000-draw bootstrap of P(PF > 1).

Usage:
    python -X utf8 research_boom_crash.py            # full run
    python -X utf8 research_boom_crash.py baseline   # baseline only
    python -X utf8 research_boom_crash.py --symbols BOOM1000,CRASH500
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

os.environ["MT5_MCP_ACCOUNT"] = "DERIV"  # must precede mt5_mcp imports

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest_boom_crash import Trade, _normalize, atr, run_one
from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.tools.boom_crash_strategy import mt5_symbol

CACHE_DIR = ROOT / "mt5_mcp" / "backtest" / "frame_cache" / "boomcrash"
OUT_DIR = ROOT / "mt5_mcp" / "memory" / "research_lab"
DAYS = 35

BASELINE = {
    "spike_threshold_atr": 2.5,
    "sl_atr_buffer": 2.0,
    "max_spike_age": 15,
}
GRID = {
    "spike_threshold_atr": [2.0, 2.5, 3.0],
    "sl_atr_buffer": [1.0, 2.0, 3.0],
    "max_spike_age": [10, 15, 30],
}


def combos() -> list[dict]:
    out = []
    for thr in GRID["spike_threshold_atr"]:
        for buf in GRID["sl_atr_buffer"]:
            for age in GRID["max_spike_age"]:
                out.append(
                    {
                        "spike_threshold_atr": thr,
                        "sl_atr_buffer": buf,
                        "max_spike_age": age,
                    }
                )
    return out


# --------------------------------------------------------------------------
# Data loading (main process, cached to parquet)

def load_frames(symbols: list[str], days: int = DAYS) -> dict[str, pd.DataFrame]:
    from mt5_mcp.client import get_client

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    client = get_client()
    client.ensure_connected()
    frames: dict[str, pd.DataFrame] = {}
    for api in symbols:
        sym = mt5_symbol(api)
        cache_path = CACHE_DIR / f"{api}.parquet"
        meta_path = CACHE_DIR / f"{api}_meta.json"
        if cache_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if "volume_min" not in meta:
                try:
                    info = client.symbol_info(sym)
                    meta.update(
                        {
                            "volume_min": float(info.get("volume_min", 0.2) or 0.2),
                            "volume_step": float(info.get("volume_step", 0.01) or 0.01),
                            "volume_max": float(info.get("volume_max", 100.0) or 100.0),
                        }
                    )
                    meta_path.write_text(json.dumps(meta, indent=2))
                except Exception:
                    meta.update({"volume_min": 0.2, "volume_step": 0.01, "volume_max": 100.0})
                    meta_path.write_text(json.dumps(meta, indent=2))
            frames[api] = pd.read_parquet(cache_path)
            continue
        try:
            raw = client.rates(sym, "M1", days * 1440 + 500)
        except Exception as exc:
            print(f"{api}: data unavailable ({exc})")
            continue
        info = client.symbol_info(sym)
        try:
            tick = client.tick(sym)
            tick_spread = float(tick.get("ask", 0)) - float(tick.get("bid", 0))
        except Exception:
            tick_spread = None
        point = float(info.get("point", 0) or 0)
        med_pts = float(raw["spread"].tail(5000).median()) if "spread" in raw else 0.0
        meta = {
            "mt5_symbol": sym,
            "digits": info.get("digits"),
            "point": point,
            "tick_value": float(info.get("trade_tick_value", 0) or 0),
            "tick_size": float(info.get("trade_tick_size", 0) or 0),
            "volume_min": float(info.get("volume_min", 0.2) or 0.2),
            "volume_step": float(info.get("volume_step", 0.01) or 0.01),
            "volume_max": float(info.get("volume_max", 100.0) or 100.0),
            "median_spread_points": med_pts,
            "median_spread_price": med_pts * point,
            "tick_spread_price": tick_spread,
            "bars": int(len(raw)),
            "start": str(raw["time"].iloc[0]),
            "end": str(raw["time"].iloc[-1]),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        df = _normalize(raw)
        df.to_parquet(cache_path)
        frames[api] = df
        print(f"{api}: {len(df)} bars ({meta['start']} .. {meta['end']}), "
              f"median spread {med_pts:.0f} pts = {med_pts * point:g}")
    return frames


# --------------------------------------------------------------------------
# Worker (one process per symbol; runs every combo for that symbol)

def _trade_row(t: Trade) -> dict:
    return {
        "symbol": t.symbol,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "pnl_usd": t.pnl_price,
        "risk_usd": t.risk_price,
        "r_multiple": t.r_multiple,
        "exit_reason": t.exit_reason,
        "spike_age": t.spike_age,
        "retracement": t.match_retracement,
    }


def run_symbol_jobs(payload: tuple[str, list[dict]]):
    symbol, cfgs = payload
    df = pd.read_parquet(CACHE_DIR / f"{symbol}.parquet")
    meta = json.loads((CACHE_DIR / f"{symbol}_meta.json").read_text())
    atr_series = atr(df, period=14)
    point = meta["point"] or 0.0
    med_spread = meta["median_spread_price"]
    tick_spread = meta.get("tick_spread_price")

    out = []
    for cfg in cfgs:
        trades, info = run_one(symbol, df, atr_series=atr_series, **cfg)
        tick_size = info.get("tick_size") or 0.0
        usd = info["tick_value"] / tick_size if tick_size else 1.0
        cost = med_spread * usd
        cost_tick = (tick_spread or med_spread) * usd
        rows = []
        gross_rows = []
        for t in trades:
            gross_rows.append(_trade_row(t))
            t.pnl_price -= cost
            rows.append(_trade_row(t))
        out.append(
            {
                "symbol": symbol,
                "cfg": cfg,
                "usd_per_price_unit": usd,
                "spread_cost_usd": cost,
                "spread_cost_tick_usd": cost_tick,
                "trades": rows,
                "gross_trades": gross_rows,
            }
        )
    return out


# --------------------------------------------------------------------------
# Robustness metrics (atlas-style)

def _pf(pnls: np.ndarray) -> float:
    wins = pnls[pnls > 0].sum()
    loss = abs(pnls[pnls < 0].sum())
    if loss == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / loss)


def _thirds(rows: list[dict]) -> list[float | None]:
    if len(rows) < 9:
        return [None, None, None]
    s = sorted(rows, key=lambda r: r["entry_time"])
    k = len(s) // 3
    parts = (s[:k], s[k : 2 * k], s[2 * k:])
    return [
        _pf(np.array([r["pnl_usd"] for r in p])) if p else None for p in parts
    ]


def _holdout(rows: list[dict]) -> tuple[int, float | None]:
    if len(rows) < 10:
        return 0, None
    s = sorted(rows, key=lambda r: r["entry_time"])
    cut = int(len(s) * 0.7)
    tail = s[cut:]
    pnls = np.array([r["pnl_usd"] for r in tail])
    return len(tail), _pf(pnls)


def _bootstrap(rows: list[dict], draws: int = 1000, seed: int = 7) -> float:
    if len(rows) < 10:
        return 0.0
    pnls = np.array([r["pnl_usd"] for r in rows])
    rng = np.random.default_rng(seed)
    n = len(pnls)
    exceed = 0
    for _ in range(draws):
        sample = pnls[rng.integers(0, n, n)]
        if _pf(sample) > 1.0:
            exceed += 1
    return exceed / draws


def verdict(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"verdict": "NO-TRADES", "n": 0}
    pnls = np.array([r["pnl_usd"] for r in rows])
    pf = _pf(pnls)
    thirds = _thirds(rows)
    hold_n, hold_pf = _holdout(rows)
    boot = _bootstrap(rows)
    thirds_ok = sum(1 for t in thirds if t is not None and t >= 1.0)
    if n >= 50 and pf >= 1.25 and thirds_ok == 3 and boot >= 0.80 and hold_n >= 15 and hold_pf and hold_pf >= 1.0:
        v = "PROMOTE"
    elif n >= 40 and pf >= 1.10 and boot >= 0.60 and thirds_ok >= 2:
        v = "PAPER"
    else:
        v = "FAIL"
    return {
        "verdict": v,
        "n": n,
        "pf": round(pf, 3),
        "thirds_pf": [round(t, 2) if t is not None else None for t in thirds],
        "holdout_n": hold_n,
        "holdout_pf": round(hold_pf, 3) if hold_pf is not None else None,
        "bootstrap_p_pf_gt_1": round(boot, 3),
        "win_rate": round(float((pnls > 0).mean()) * 100, 1),
        "avg_r": round(float(np.mean([r["r_multiple"] for r in rows])), 3),
        "net_usd": round(float(pnls.sum()), 2),
    }


def cfg_key(cfg: dict) -> tuple:
    return (cfg["spike_threshold_atr"], cfg["sl_atr_buffer"], cfg["max_spike_age"])


def simulate_account(
    rows: list[dict],
    metas: dict[str, dict],
    start_balance: float = 500.0,
    risk_pct: float = 0.8,
) -> dict:
    """Fixed-fractional equity sim mirroring trade_watch sizing.

    Live path: budget = equity * risk% (watcher applies 0.8 factor to
    risk.max_risk_percent=1.0 -> 0.8%), lots floored at volume_min.
    """
    eq = start_balance
    peak = start_balance
    max_dd = 0.0
    over_risk = 0
    worst = 0.0
    best = 0.0
    min_lot_risks = []
    for r in sorted(rows, key=lambda x: x["exit_time"] or x["entry_time"]):
        m = metas.get(r["symbol"], {})
        vstep = float(m.get("volume_step", 0.01) or 0.01)
        vmin = float(m.get("volume_min", 0.2) or 0.2)
        risk_per_lot = float(r["risk_usd"])
        if risk_per_lot <= 0:
            continue
        budget = eq * risk_pct / 100.0
        lots = int(budget / risk_per_lot / vstep) * vstep
        min_risk = risk_per_lot * vmin
        min_lot_risks.append(min_risk)
        if lots < vmin:
            lots = vmin
            if min_risk > 1.5 * budget:
                over_risk += 1
        pnl = float(r["pnl_usd"]) * lots
        eq += pnl
        worst = min(worst, pnl)
        best = max(best, pnl)
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    med_min_risk = float(np.median(min_lot_risks)) if min_lot_risks else 0.0
    return {
        "start": start_balance,
        "final": round(eq, 2),
        "return_pct": round((eq / start_balance - 1) * 100, 1),
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / peak * 100, 1) if peak else 0.0,
        "risk_pct_per_trade": risk_pct,
        "median_min_lot_risk_usd": round(med_min_risk, 2),
        "over_risk_trades": over_risk,
        "worst_trade_usd": round(worst, 2),
        "best_trade_usd": round(best, 2),
    }


def monte_carlo(
    rows: list[dict],
    metas: dict[str, dict],
    start_balance: float = 500.0,
    risk_pct: float = 0.8,
    draws: int = 1000,
    seed: int = 42,
) -> dict:
    """Monte Carlo sequence reshuffling: shuffle trade order, simulate equity, repeat.

    Answers: given my actual trade PnLs, how much could the outcome vary just
    from the order they happened in?
    """
    rng = np.random.default_rng(seed)
    finals = []
    max_dds = []
    for _ in range(draws):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        sim = simulate_account(shuffled, metas, start_balance, risk_pct)
        finals.append(sim["final"])
        max_dds.append(sim["max_dd_pct"])
    return {
        "draws": draws,
        "final_equity": {
            "median": round(float(np.median(finals)), 2),
            "p5": round(float(np.percentile(finals, 5)), 2),
            "p95": round(float(np.percentile(finals, 95)), 2),
            "min": round(float(min(finals)), 2),
            "max": round(float(max(finals)), 2),
        },
        "max_dd_pct": {
            "median": round(float(np.median(max_dds)), 1),
            "p5": round(float(np.percentile(max_dds, 5)), 1),
            "p95": round(float(np.percentile(max_dds, 95)), 1),
        },
        "prob_double": round(sum(1 for f in finals if f >= 2 * start_balance) / draws * 100, 1),
        "prob_halve": round(sum(1 for f in finals if f <= 0.5 * start_balance) / draws * 100, 1),
    }


# --------------------------------------------------------------------------
# Report

def write_report(pool_results: list[dict], baseline_only: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = OUT_DIR / f"boomcrash_{today}.json"
    md_path = OUT_DIR / f"boomcrash_{today}.md"

    metas = {
        p.stem.replace("_meta", ""): json.loads(p.read_text())
        for p in CACHE_DIR.glob("*_meta.json")
    }

    by_cfg: dict[tuple, list[dict]] = {}
    for item in pool_results:
        by_cfg.setdefault(cfg_key(item["cfg"]), []).extend(item["trades"])
    by_cfg_gross: dict[tuple, list[dict]] = {}
    for item in pool_results:
        by_cfg_gross.setdefault(cfg_key(item["cfg"]), []).extend(item["gross_trades"])

    cfg_rows = []
    for key, rows in by_cfg.items():
        gross = by_cfg_gross[key]
        v = verdict(rows)
        gross_pf = verdict(gross).get("pf")
        cfg_rows.append(
            {
                "spike_threshold_atr": key[0],
                "sl_atr_buffer": key[1],
                "max_spike_age": key[2],
                **v,
                "pf_gross_no_cost": round(gross_pf, 3) if gross_pf is not None else None,
            }
        )
    cfg_rows.sort(key=lambda r: r.get("avg_r", 0), reverse=True)

    base_rows = by_cfg[cfg_key(BASELINE)]
    base_verdict = verdict(base_rows)
    per_symbol = []
    for item in pool_results:
        if cfg_key(item["cfg"]) != cfg_key(BASELINE):
            continue
        v = verdict(item["trades"])
        per_symbol.append({"symbol": item["symbol"], **v})
    per_symbol.sort(key=lambda r: r.get("net_usd", 0), reverse=True)

    meta_samples = {}
    for item in pool_results:
        meta_samples[item["symbol"]] = {
            "spread_cost_usd": item["spread_cost_usd"],
            "tick_spread_usd": item["spread_cost_tick_usd"],
            "usd_per_price_unit": item["usd_per_price_unit"],
        }

    # $500-account equity sims (baseline pooled across all symbols)
    account_sims = {}
    for label, pct in (("conservative_0.5pct", 0.5), ("live_mirror_0.8pct", 0.8)):
        account_sims[label] = simulate_account(
            base_rows, metas, start_balance=500.0, risk_pct=pct
        )

    # Monte Carlo sequence reshuffling (1000 draws, live-mirror sizing)
    print("running Monte Carlo (1000 draws) ...")
    mc = monte_carlo(base_rows, metas, start_balance=500.0, risk_pct=0.8, draws=1000)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "days": DAYS,
        "baseline": {**BASELINE, **base_verdict},
        "per_symbol_baseline": per_symbol,
        "configs": cfg_rows,
        "grid": GRID,
        "cost_meta": meta_samples,
        "account_sim_500usd": account_sims,
        "monte_carlo_500usd": mc,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    lines = [
        "# Boom/Crash Spike+Drift — robust backtest",
        "",
        f"Generated {payload['generated']} | {DAYS} days M1 | spread-adjusted "
        f"(round trip = 1x median bar spread) | grid {len(cfg_rows)} configs x 20 symbols",
        "",
        "## Baseline (live watcher params: thr 2.5 ATR, SL buf 2.0, age 15)",
        "",
        f"**Verdict: {base_verdict.get('verdict')}** — "
        f"n={base_verdict.get('n')} PF={base_verdict.get('pf')} "
        f"WR={base_verdict.get('win_rate')}% avgR={base_verdict.get('avg_r')} "
        f"net=${base_verdict.get('net_usd')} "
        f"thirds={base_verdict.get('thirds_pf')} holdout={base_verdict.get('holdout_pf')} "
        f"(n={base_verdict.get('holdout_n')}) bootP={base_verdict.get('bootstrap_p_pf_gt_1')}",
        "",
        "| symbol | verdict | n | PF | WR% | avg R | net $ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in per_symbol:
        lines.append(
            f"| {r['symbol']} | {r.get('verdict')} | {r.get('n', 0)} | {r.get('pf')} "
            f"| {r.get('win_rate')} | {r.get('avg_r')} | {r.get('net_usd')} |"
        )
    lines += [
        "",
        "## $500 account (fixed-fractional, min-lot 0.2 floor, live sizing mirror)",
        "",
        "Sizing mirrors trade_watch: budget = equity x risk%, lots floored at",
        "volume_min (0.2) — min-lot risk exceeding 1.5x budget counted as over-risk.",
        "All signals taken (max 2 open concurrent is not simulated).",
        "",
        "| profile | risk/trade | final | return | max DD | over-risk trades | worst trade |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, s in account_sims.items():
        lines.append(
            f"| {label} | {s['risk_pct_per_trade']}% | ${s['final']} | {s['return_pct']}% "
            f"| ${s['max_dd_usd']} ({s['max_dd_pct']}%) | {s['over_risk_trades']} "
            f"| ${s['worst_trade_usd']} |"
        )
    fe = mc["final_equity"]
    dd = mc["max_dd_pct"]
    lines += [
        "",
        "## Monte Carlo — sequence risk ($500 account, 0.8% risk, 1000 shuffles)",
        "",
        "Same trades, random order. Shows how much outcome depends on sequence.",
        "",
        f"| metric | value |",
        f"|---|---|",
        f"| median final equity | **${fe['median']}** |",
        f"| 5th percentile (worst case) | ${fe['p5']} |",
        f"| 95th percentile (best case) | ${fe['p95']} |",
        f"| worst draw | ${fe['min']} |",
        f"| best draw | ${fe['max']} |",
        f"| median max drawdown | {dd['median']}% |",
        f"| 95th percentile max DD | {dd['p95']}% |",
        f"| probability of doubling ($1000+) | **{mc['prob_double']}%** |",
        f"| probability of halving ($250 or less) | **{mc['prob_halve']}%** |",
        "",
    ]
    lines += ["", "## Configs ranked by avg R", ""]
    if not baseline_only:
        lines += [
            "| thr | sl_buf | age | n | PF | PF(no cost) | WR% | avg R | net $ | bootP | verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in cfg_rows[:15]:
            lines.append(
                f"| {r['spike_threshold_atr']} | {r['sl_atr_buffer']} | {r['max_spike_age']} "
                f"| {r.get('n', 0)} | {r.get('pf')} | {r.get('pf_gross_no_cost')} "
                f"| {r.get('win_rate')} | {r.get('avg_r')} | {r.get('net_usd')} "
                f"| {r.get('bootstrap_p_pf_gt_1')} | {r.get('verdict')} |"
            )
    md_path.write_text("\n".join(lines) + "\n")
    return md_path


# --------------------------------------------------------------------------

def main() -> None:
    baseline_only = "baseline" in sys.argv
    symbols = list(BOOM_CRASH_SYMBOLS)
    for arg in sys.argv[1:]:
        if arg.startswith("--symbols"):
            if "=" in arg:
                symbols = arg.split("=", 1)[1].split(",")
            elif sys.argv.index(arg) + 1 < len(sys.argv):
                symbols = sys.argv[sys.argv.index(arg) + 1].split(",")

    print(f"loading {len(symbols)} symbols x {DAYS}d M1 from Deriv terminal ...")
    load_frames(symbols)
    print("data loaded.")

    cfgs = [BASELINE] if baseline_only else combos()
    payloads = [(s, cfgs) for s in symbols if (CACHE_DIR / f"{s}.parquet").exists()]

    pool_results: list[dict] = []
    with ProcessPoolExecutor(max_workers=min(len(payloads), os.cpu_count() or 4)) as pool:
        for res in pool.map(run_symbol_jobs, payloads):
            pool_results.extend(res)
            base = [r for r in res if cfg_key(r["cfg"]) == cfg_key(BASELINE)]
            if base:
                print(f"{base[0]['symbol']}: baseline {len(base[0]['trades'])} trades")

    md = write_report(pool_results, baseline_only)
    print(f"\nreport: {md}")


if __name__ == "__main__":
    main()
