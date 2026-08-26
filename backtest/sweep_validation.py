"""sweep_validation.py — research-only statistical validation of two trading concepts.

Concept A: Liquidity-sweep detection (stop-hunt reversal vs breakout continuation).
Concept B: Tick-volume flow scoring (volume z-score quintiles -> forward drift).

Standalone validation script. It does NOT modify or use the production strategy
engine; it only fetches OHLCV via the MT5 client and computes statistics.
Run one symbol/timeframe per process (MetaTrader5 module instability rule).

Usage examples:
  python -X utf8 backtest/sweep_validation.py --terminal DERIV --symbol "Boom 500 Index" --tf M5 --bars 80000 --out backtest/results/sweep_Boom500_M5.json
  python -X utf8 backtest/sweep_validation.py --terminal PROP --symbol EURUSD --tf M15 --bars 60000 --out backtest/results/sweep_EURUSD_M15.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mt5_mcp.config import MT5Config  # noqa: E402
from mt5_mcp.mt5_client import MT5Client  # noqa: E402

TERMINALS = {
    "PROP": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    "DERIV": r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe",
}

RNG = np.random.default_rng(20260817)


# ----------------------------------------------------------------------------
# data
# ----------------------------------------------------------------------------
def fetch_rates(terminal: str, symbol: str, tf: str, bars: int) -> tuple[pd.DataFrame, dict]:
    client = MT5Client(MT5Config(terminal_path=TERMINALS[terminal]))
    if not client.connect():
        raise RuntimeError(f"MT5 connect failed: {client.last_error()}")
    try:
        df = client.rates(symbol, tf, bars)
        info = client.symbol_info(symbol)
    finally:
        client.shutdown()
    return df, info


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ATR. Returns array of length len(close); first n values are NaN."""
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = np.full(len(close), np.nan)
    if len(close) <= n:
        return atr
    atr[n] = np.mean(tr[1 : n + 1])
    for i in range(n + 1, len(close)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def find_pivots(high: np.ndarray, low: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Pivot highs/lows with left+right lookback k. Boolean arrays, 1 = pivot."""
    n = len(high)
    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        window_h = high[i - k : i + k + 1]
        window_l = low[i - k : i + k + 1]
        if high[i] >= np.max(window_h) and np.sum(window_h == high[i]) == 1:
            ph[i] = True
        if low[i] <= np.min(window_l) and np.sum(window_l == low[i]) == 1:
            pl[i] = True
    return ph, pl


# ----------------------------------------------------------------------------
# Concept A — liquidity sweeps
# ----------------------------------------------------------------------------
def detect_sweeps(
    df: pd.DataFrame,
    atr: np.ndarray,
    k: int,
    thr: float,
    max_age: int,
    horizons: tuple[int, ...],
    touch_h: int,
    min_gap: int = 0,
) -> list[dict]:
    """Detect sweep events with causal pivots only.

    Event at bar e uses the most recent confirmed pivot (index < e) of the
    same side, provided it is no older than max_age bars. A pivot is consumed
    once swept; a new pivot must form before the next event on that side.
    O(n) forward scan.
    """
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    times = df["time"].to_numpy()
    ph, pl = find_pivots(high, low, k)

    events: list[dict] = []
    cand_h = -1  # most recent confirmed pivot-high index seen so far
    cand_l = -1
    used_h = -1  # consumed swing-high index (must form a NEW pivot to re-arm)
    used_l = -1
    last_event_idx = -10**9

    # scan e from k+1 (first confirmable pivot) to n-1-touch_h-2
    for e in range(k + 1, n - touch_h - 2):
        # a pivot at index i becomes confirmed only once bar i+k has closed
        i_conf = e - k
        if i_conf >= 0:
            if ph[i_conf]:
                cand_h = i_conf
            if pl[i_conf]:
                cand_l = i_conf
        if e - last_event_idx < min_gap:
            continue
        if np.isnan(atr[e]) or atr[e] <= 0:
            continue

        ev = None
        # --- high side ---
        if cand_h > used_h and e - cand_h <= max_age:
            sh = high[cand_h]
            if high[e] >= sh + thr * atr[e]:
                if close[e] < sh:  # stop-hunt (wick through, close back inside)
                    ev = {"idx": e, "side": "high", "kind": "reversal", "dir": -1, "swing_idx": cand_h}
                    used_h = cand_h
                elif close[e] > sh:  # breakout (close beyond)
                    ev = {"idx": e, "side": "high", "kind": "breakout", "dir": +1, "swing_idx": cand_h}
                    used_h = cand_h
        # --- low side ---
        if ev is None and cand_l > used_l and e - cand_l <= max_age:
            sl = low[cand_l]
            if low[e] <= sl - thr * atr[e]:
                if close[e] > sl:  # stop-hunt
                    ev = {"idx": e, "side": "low", "kind": "reversal", "dir": +1, "swing_idx": cand_l}
                    used_l = cand_l
                elif close[e] < sl:  # breakout
                    ev = {"idx": e, "side": "low", "kind": "breakout", "dir": -1, "swing_idx": cand_l}
                    used_l = cand_l

        if ev is None:
            continue
        ev["time"] = str(pd.Timestamp(times[e]))
        ev["entry"] = float(open_[e + 1])
        ev["atr"] = float(atr[e])
        for h in horizons:
            ev[f"drift{h}"] = float((close[e + 1 + h] - open_[e + 1]) * ev["dir"] / atr[e])
        # touch win/loss within touch_h bars (1x ATR)
        target = ev["entry"] + ev["dir"] * atr[e]
        stop = ev["entry"] - ev["dir"] * atr[e]
        outcome = None  # 'win' | 'loss' | None
        for j in range(e + 1, min(e + 1 + touch_h, n)):
            if ev["dir"] > 0:
                hit_target = high[j] >= target
                hit_stop = low[j] <= stop
            else:
                hit_target = low[j] <= target
                hit_stop = high[j] >= stop
            if hit_target and hit_stop:
                outcome = "win" if (close[j] - ev["entry"]) * ev["dir"] > 0 else "loss"
            elif hit_target:
                outcome = "win"
            elif hit_stop:
                outcome = "loss"
            if outcome is not None:
                break
        ev["touch"] = outcome
        ev["touch_bars"] = j - e if outcome is not None else None
        events.append(ev)
        last_event_idx = e
    return events


def _random_entry_metrics(df: pd.DataFrame, atr: np.ndarray, dirs: np.ndarray, horizons, touch_h, event_idxs):
    """For each direction draw a random bar outside that event's neighbourhood
    and compute the same metrics as for a sweep event."""
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    out = []
    for i, d in enumerate(dirs):
        ei = int(event_idxs[i])
        while True:
            r = int(RNG.integers(touch_h + 2, n - touch_h - 2))
            if abs(r - ei) > 25:
                break
        rec = {"idx": r, "dir": int(d), "entry": float(open_[r + 1]), "atr": float(atr[r])}
        for h in horizons:
            rec[f"drift{h}"] = float((close[r + 1 + h] - open_[r + 1]) * d / atr[r])
        target = rec["entry"] + d * atr[r]
        stop = rec["entry"] - d * atr[r]
        outcome = None
        for j in range(r + 1, min(r + 1 + touch_h, n)):
            if d > 0:
                hit_target = high[j] >= target
                hit_stop = low[j] <= stop
            else:
                hit_target = low[j] <= target
                hit_stop = high[j] >= stop
            if hit_target and hit_stop:
                outcome = "win" if (close[j] - rec["entry"]) * d > 0 else "loss"
            elif hit_target:
                outcome = "win"
            elif hit_stop:
                outcome = "loss"
            if outcome is not None:
                break
        rec["touch"] = outcome
        out.append(rec)
    return out


def bootstrap_mean_diff(a: np.ndarray, b: np.ndarray, n_resample: int = 5000) -> dict:
    """Two-sample bootstrap CI and p-value for mean(a) - mean(b) (unequal n ok)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return {"diff": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "p": float("nan")}
    ia = RNG.integers(0, na, size=(n_resample, na))
    ib = RNG.integers(0, nb, size=(n_resample, nb))
    diffs = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    p = float(2 * min(np.mean(diffs <= 0), np.mean(diffs >= 0)))
    return {"diff": float(np.mean(a) - np.mean(b)), "ci_lo": float(lo), "ci_hi": float(hi), "p": p}


def concept_a_stats(events: list[dict], baseline: list[dict], horizons, touch_h) -> dict:
    out: dict = {}
    ev_dirs = np.array([e["dir"] for e in events])
    n_ev = len(events)
    for h in horizons:
        ev = np.array([e[f"drift{h}"] for e in events])
        bs = np.array([b[f"drift{h}"] for b in baseline])
        t_stat = np.mean(ev) / (np.std(ev, ddof=1) / np.sqrt(n_ev)) if n_ev > 1 else 0.0
        out[f"drift{h}"] = {
            "n": n_ev,
            "mean_atr": float(np.mean(ev)),
            "sd_atr": float(np.std(ev, ddof=1)),
            "t_vs_zero": float(t_stat),
            "baseline_mean_atr": float(np.mean(bs)),
            "bootstrap": bootstrap_mean_diff(ev, bs),
        }
    # touch win rates (exclude no-touch)
    ev_out = np.array([1.0 if e["touch"] == "win" else 0.0 for e in events if e["touch"] is not None])
    bs_out = np.array([1.0 if b["touch"] == "win" else 0.0 for b in baseline if b["touch"] is not None])
    wr = float(np.mean(ev_out)) if len(ev_out) else float("nan")
    wr_bs = float(np.mean(bs_out)) if len(bs_out) else float("nan")
    z = (wr - 0.5) / (np.sqrt(0.25 / len(ev_out))) if len(ev_out) else 0.0
    out["touch"] = {
        "horizon": touch_h,
        "n_decided": int(len(ev_out)),
        "n_no_touch": int(n_ev - len(ev_out)),
        "win_rate": wr,
        "baseline_win_rate": wr_bs,
        "z_vs_50pct": float(z),
        "bootstrap": bootstrap_mean_diff(ev_out, bs_out),
        "mean_dir": float(np.mean(ev_dirs)),
    }
    return out


# ----------------------------------------------------------------------------
# Concept B — volume flow
# ----------------------------------------------------------------------------
def volume_flow_stats(df: pd.DataFrame, atr: np.ndarray, roll: int = 50, horizons=(1, 3, 5)) -> dict:
    n = len(df)
    vol = df["tick_volume"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    close = df["close"].to_numpy(float)

    if np.nanmedian(vol[roll:]) <= 0:
        return {"available": False, "reason": "tick_volume is zero/constant"}

    vol_z = np.full(n, np.nan)
    for i in range(roll, n):
        w = vol[i - roll : i]
        mu, sd = np.mean(w), np.std(w)
        vol_z[i] = (vol[i] - mu) / sd if sd > 0 else 0.0

    rng_ = high - low
    range_pct = np.where(rng_ > 0, (close - low) / np.where(rng_ > 0, rng_, 1.0), 0.5)
    direction = np.where(close > open_, 1.0, -1.0)

    valid = ~np.isnan(vol_z) & ~np.isnan(atr) & (atr > 0)
    # forward drift signed by bar direction, in ATR units
    signed = {}
    for h in horizons:
        ret = np.full(n, np.nan)
        for i in range(roll, n - h):
            ret[i] = (close[i + h] - close[i]) * direction[i] / atr[i]
        signed[h] = ret

    # baseline: all bars
    base = {h: float(np.nanmean(signed[h][valid])) for h in horizons}

    # quintiles by volume z
    zvals = vol_z[valid]
    qs = np.quantile(zvals, [0.2, 0.4, 0.6, 0.8])
    quintiles = {}
    for q in range(5):
        lo = -np.inf if q == 0 else qs[q - 1]
        hi = np.inf if q == 4 else qs[q]
        mask = valid & (vol_z >= lo) & (vol_z < hi)
        row = {"z_range": f"[{lo:.2f},{hi:.2f})", "n": int(np.sum(mask))}
        for h in horizons:
            vals = signed[h][mask]
            row[f"mean_drift{h}"] = float(np.nanmean(vals))
            row[f"se_drift{h}"] = float(np.nanstd(vals, ddof=1) / np.sqrt(np.sum(~np.isnan(vals)))) if np.sum(~np.isnan(vals)) > 1 else float("nan")
            row[f"t_drift{h}"] = float(np.nanmean(vals) / row[f"se_drift{h}"]) if row[f"se_drift{h}"] and row[f"se_drift{h}"] == row[f"se_drift{h}"] else 0.0
        up_mask = mask & (direction == 1)
        dn_mask = mask & (direction == -1)
        row["n_up"] = int(np.sum(up_mask))
        row["n_dn"] = int(np.sum(dn_mask))
        for h in (3, 5):
            row[f"up_mean_drift{h}"] = float(np.nanmean(signed[h][up_mask]))
            row[f"dn_mean_drift{h}"] = float(np.nanmean(signed[h][dn_mask]))
        quintiles[q] = row

    # strong bar: close in top 10% of range AND volume z > 1
    strong = valid & (range_pct >= 0.9) & (vol_z > 1.0)
    strong_row = {"n": int(np.sum(strong)), "n_up": int(np.sum(strong & (direction == 1)))}
    for h in (3, 5):
        vals = signed[h][strong]
        base_vals = signed[h][valid]
        strong_row[f"mean_drift{h}"] = float(np.nanmean(vals))
        strong_row[f"baseline_drift{h}"] = base[h]
        # bootstrap CI of (event - baseline) difference
        if np.sum(~np.isnan(vals)) >= 30:
            ev = vals[~np.isnan(vals)]
            bs = base_vals[~np.isnan(base_vals)]
            strong_row[f"bs_{h}"] = bootstrap_mean_diff(ev, bs, 3000)
        # half-sample stability (first/second half of the series)
        half = n // 2
        m1 = (np.arange(n) < half) & strong
        m2 = (np.arange(n) >= half) & strong
        strong_row[f"first_half_mean{h}"] = float(np.nanmean(signed[h][m1]))
        strong_row[f"second_half_mean{h}"] = float(np.nanmean(signed[h][m2]))
        strong_row[f"first_half_n{h}"] = int(np.sum(m1))
        strong_row[f"second_half_n{h}"] = int(np.sum(m2))
        # continuation rate: fraction of strong bars where next h-bar move is in bar's direction
        strong_row[f"continuation_rate{h}"] = float(np.mean((signed[h][strong] > 0) if np.sum(~np.isnan(vals)) else np.nan))
    # z>1 all closes
    z1 = valid & (vol_z > 1.0)
    z1_row = {"n": int(np.sum(z1))}
    for h in (3, 5):
        z1_row[f"mean_drift{h}"] = float(np.nanmean(signed[h][z1]))
        z1_row[f"baseline_drift{h}"] = base[h]
    # vol spike exhaustion test: z>2
    z2 = valid & (vol_z > 2.0)
    z2_row = {"n": int(np.sum(z2))}
    for h in (3, 5):
        z2_row[f"mean_drift{h}"] = float(np.nanmean(signed[h][z2]))
        z2_row[f"baseline_drift{h}"] = base[h]

    return {
        "available": True,
        "roll": roll,
        "n_valid": int(np.sum(valid)),
        "baseline_signed_drift": base,
        "quintiles": quintiles,
        "strong_close_z1": strong_row,
        "z_gt_1": z1_row,
        "z_gt_2": z2_row,
        "vol_median": float(np.nanmedian(vol[roll:])),
        "vol_z_q": [float(x) for x in qs],
    }


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", choices=["PROP", "DERIV"], required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--bars", type=int, default=80000)
    ap.add_argument("--swing-k", type=int, default=3)
    ap.add_argument("--sweep-thr", type=float, default=0.4, help="ATR multiple for wick penetration")
    ap.add_argument("--max-age", type=int, default=200)
    ap.add_argument("--atr-n", type=int, default=14)
    ap.add_argument("--horizons", default="3,5,10,20")
    ap.add_argument("--touch-h", type=int, default=20)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    horizons = tuple(int(x) for x in args.horizons.split(","))
    t0 = time.time()
    df, sym_info = fetch_rates(args.terminal, args.symbol, args.tf, args.bars)
    fetch_s = time.time() - t0
    print(f"fetched {len(df)} bars in {fetch_s:.0f}s", flush=True)
    point = float(sym_info.get("point", 0.0) or 0.0)
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    atr = wilder_atr(high, low, close, args.atr_n)

    # ---- Concept A ----
    print("concept A scan...", flush=True)
    events = detect_sweeps(df, atr, args.swing_k, args.sweep_thr, args.max_age, horizons, args.touch_h)
    print(f"  events={len(events)}, baseline...", flush=True)
    dirs = np.array([e["dir"] for e in events])
    offsets = np.array([e["idx"] for e in events])
    baseline = _random_entry_metrics(df, atr, dirs, horizons, args.touch_h, offsets)
    a_stats = concept_a_stats(events, baseline, horizons, args.touch_h)

    # thinned (non-overlapping, gap >= 25) variant
    thin: list[dict] = []
    last_idx = -10**9
    for e in events:
        if e["idx"] - last_idx >= 25:
            thin.append(e)
            last_idx = e["idx"]
    thin_dirs = np.array([e["dir"] for e in thin])
    thin_offsets = np.array([e["idx"] for e in thin])
    thin_baseline = _random_entry_metrics(df, atr, thin_dirs, horizons, args.touch_h, thin_offsets)
    a_thin = concept_a_stats(thin, thin_baseline, horizons, args.touch_h)

    # ---- Concept B ----
    print("concept B...", flush=True)
    b_stats = volume_flow_stats(df, atr)

    # ---- costs / instrument info ----
    med_spread_points = float(np.nanmedian(df["spread"].to_numpy(float)))
    med_atr_price = float(np.nanmedian(atr[~np.isnan(atr)]))
    med_atr_points = med_atr_price / point if point else float("nan")
    info = {
        "symbol": args.symbol,
        "tf": args.tf,
        "bars": n,
        "start": str(df["time"].iloc[0]),
        "end": str(df["time"].iloc[-1]),
        "point": point,
        "median_spread_points": med_spread_points,
        "median_atr_points": med_atr_points,
        "cost_atr_units": med_spread_points / med_atr_points if med_atr_points else float("nan"),
        "median_tick_volume": float(np.nanmedian(df["tick_volume"].to_numpy(float))),
    }

    result = {
        "concept_a": {"events": len(events), "thinned_events": len(thin), "stats": a_stats, "stats_thinned": a_thin},
        "concept_b": b_stats,
        "info": info,
        "params": vars(args),
    }

    # ---- console summary ----
    print("=" * 100)
    print(f"CONCEPT A sweeps | {args.symbol} {args.tf} | {n} bars "
          f"({info['start'][:10]} -> {info['end'][:10]}) | events={len(events)} ({len(thin)} thinned)")
    print(f"  event rate: {len(events)/n*1000:.2f} per 1000 bars | dir mix: longs={np.mean(dirs):.2f}")
    for h in horizons:
        s = a_stats[f"drift{h}"]
        print(f"  drift {h:>2} bars: {s['mean_atr']:+.4f} ATR (t={s['t_vs_zero']:+.2f}) "
              f"vs baseline {s['baseline_mean_atr']:+.4f} | diff {s['bootstrap']['diff']:+.4f} "
              f"CI[{s['bootstrap']['ci_lo']:+.4f},{s['bootstrap']['ci_hi']:+.4f}] p={s['bootstrap']['p']:.3f}")
    t = a_stats["touch"]
    print(f"  touch 1xATR (H={t['horizon']}): WR={t['win_rate']:.3f} (n={t['n_decided']}, no-touch={t['n_no_touch']}) "
          f"vs baseline {t['baseline_win_rate']:.3f} | z={t['z_vs_50pct']:+.2f} "
          f"| bootstrap diff p={t['bootstrap']['p']:.3f}")
    t2 = a_thin["touch"]
    print(f"  thinned touch: WR={t2['win_rate']:.3f} (n={t2['n_decided']}) vs baseline {t2['baseline_win_rate']:.3f} "
          f"| drift3 diff={a_thin['drift3']['bootstrap']['diff']:+.4f} p={a_thin['drift3']['bootstrap']['p']:.3f}")
    print(f"  cost estimate: median spread {info['median_spread_points']:.1f} pts = {info['cost_atr_units']:.3f} ATR "
          f"(median ATR {info['median_atr_points']:.2f} pts)")
    print("-" * 100)
    if b_stats.get("available"):
        print(f"CONCEPT B volume flow | valid bars {b_stats['n_valid']} | vol median {b_stats['vol_median']:.0f}")
        print(f"  baseline signed drift: " + " ".join(f"h{h}={v:+.4f}" for h, v in b_stats["baseline_signed_drift"].items()))
        for q, row in b_stats["quintiles"].items():
            print(f"  Q{q} z{row['z_range']:>12}: n={row['n']:>7} (up {row['n_up']:>6}) "
                  f"h3={row['mean_drift3']:+.4f} h5={row['mean_drift5']:+.4f} "
                  f"up_h5={row['up_mean_drift5']:+.4f} dn_h5={row['dn_mean_drift5']:+.4f}")
        s = b_stats["strong_close_z1"]
        print(f"  strong close (top10% range & z>1): n={s['n']} h3={s['mean_drift3']:+.4f} h5={s['mean_drift5']:+.4f} "
              f"vs baseline h3={s['baseline_drift3']:+.4f} h5={s['baseline_drift5']:+.4f} cont5={s.get('continuation_rate5', float('nan')):.3f}")
        for h in (3, 5):
            b = s.get(f"bs_{h}")
            if b:
                print(f"    h{h} vs baseline: diff {b['diff']:+.4f} CI[{b['ci_lo']:+.4f},{b['ci_hi']:+.4f}] p={b['p']:.3f} "
                      f"| halves: [{s.get(f'first_half_mean{h}', float('nan')):+.4f} n={s.get(f'first_half_n{h}', 0)}] "
                      f"[{s.get(f'second_half_mean{h}', float('nan')):+.4f} n={s.get(f'second_half_n{h}', 0)}]")
        for k in ("z_gt_1", "z_gt_2"):
            r = b_stats[k]
            print(f"  {k}: n={r['n']} h3={r['mean_drift3']:+.4f} h5={r['mean_drift5']:+.4f} "
                  f"(baseline h5={r['baseline_drift5']:+.4f})")
    else:
        print("CONCEPT B: unavailable - " + b_stats.get("reason", ""))
    print(f"  fetch {fetch_s:.0f}s, total {time.time()-t0:.0f}s")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()