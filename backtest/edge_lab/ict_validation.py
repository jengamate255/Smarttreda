"""ict_validation.py - research-only event-study validation of "ICT Concepts [LuxAlgo]".

Implements the exact signal definitions from backtest/edge_lab/ict_concepts_spec.md
(extracted from the Pine v5 source):

  displacement : body > SMA(body,5) AND both wicks < 0.36*body (L_bodyUP / L_bodyDN)
  FVG          : displacement[1] AND low > high[2]  (bull) / high < low[2] (bear)
  FVG re-entry : later bar straddles the gap zone (classic rebalance), bounce measured
                 from the re-entry bar
  MSS          : close beyond latest confirmed swing high given a confirmed swing low
                 formed after it (pivothigh(3,1) / pivotlow(3,1)); mirror for bearish
  BOS          : close beyond latest swing high while structure already bullish
                 (MSS.dir == +1); mirror for bearish
  vImb         : vImb_Bl / vImb_Br engulfing-gap continuation (exact formula)
  killzone     : NY 07:00-09:00 America/New_York, LondonOpen 07:00-10:00
                 Europe/London (FX/prop only)

Methodology (edge-lab standard, mirrors backtest/sweep_validation.py):
  - causal events only: signal decided at bar close, entry at NEXT bar open
  - drift in ATR units at horizons 3/5/10/20
  - 1-ATR touch win/loss within 20 bars (SL-first tie-break, close-decided)
  - baseline: same-direction RANDOM-bar entries (per-event direction), same symbol/tf
  - bootstrap p (two-sample, 5000 resamples) vs baseline
  - half-sample stability (first vs second half of the series, by bar index)
  - cost survival: per-instrument cost in ATR units (FX 1.5 pips, XAU 12 pts,
    synthetics 2 pts) vs drift

Run ONE symbol/timeframe per process (MetaTrader5 module instability rule).
Usage:
  python -X utf8 backtest/edge_lab/ict_validation.py --terminal PROP --symbol EURUSD --tf M15 --bars 50000 --out backtest/results/ict_EURUSD_M15.json
  python -X utf8 backtest/edge_lab/ict_validation.py --terminal DERIV --symbol "Boom 500 Index" --tf M5 --bars 80000 --out backtest/results/ict_Boom500_M5.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from mt5_mcp.config import MT5Config  # noqa: E402
from mt5_mcp.mt5_client import MT5Client  # noqa: E402

TERMINALS = {
    "PROP": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    "DERIV": r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe",
}

BASE_SEED = 20260817
HORIZONS = (3, 5, 10, 20)
TOUCH_H = 20


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


def rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    """SMA over the last w values INCLUDING the current bar (Pine sma semantics)."""
    out = np.full(len(x), np.nan)
    if len(x) < w:
        return out
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out[w - 1 :] = (cs[w:] - cs[:-w]) / w
    return out


def find_pivots_lr(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Pine pivothigh(left,right)/pivotlow(left,right): candidate at p, window
    [p-right, p+left]; confirmed at bar p+left (when the window closes)."""
    n = len(high)
    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)
    for p in range(right, n - left):
        w0, w1 = p - right, p + left
        if high[p] >= np.max(high[w0 : w1 + 1]) and np.sum(high[w0 : w1 + 1] == high[p]) == 1:
            ph[p] = True
        if low[p] <= np.min(low[w0 : w1 + 1]) and np.sum(low[w0 : w1 + 1] == low[p]) == 1:
            pl[p] = True
    return ph, pl


def session_masks(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Session masks over bars (times are UTC). NY = 07:00-09:00 America/New_York,
    LondonOpen = 07:00-10:00 Europe/London."""
    dt = df["time"]
    utc = dt.dt.tz_localize("UTC")
    ny = utc.dt.tz_convert(ZoneInfo("America/New_York"))
    lon = utc.dt.tz_convert(ZoneInfo("Europe/London"))
    ny_m = ((ny.dt.hour >= 7) & (ny.dt.hour < 9)).to_numpy()
    lon_m = ((lon.dt.hour >= 7) & (lon.dt.hour < 10)).to_numpy()
    return {"ny": ny_m, "london": lon_m, "union": ny_m | lon_m}


# ----------------------------------------------------------------------------
# event post-processing (shared by all detectors)
# ----------------------------------------------------------------------------
def postprocess(pairs, df, atr, horizons, touch_h):
    """pairs: list of (idx, dir, meta). Produces event records with entry at
    next bar open, drift in ATR units, and 1-ATR touch outcome within touch_h."""
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    times = df["time"].to_numpy()
    events: list[dict] = []
    for idx, dir_, meta in pairs:
        if idx < 0 or idx >= n - touch_h - 2:
            continue
        if np.isnan(atr[idx]) or atr[idx] <= 0:
            continue
        entry = float(open_[idx + 1])
        rec = {"idx": int(idx), "dir": int(dir_), "time": str(pd.Timestamp(times[idx])), "entry": entry}
        if meta:
            rec["meta"] = meta
        for h in horizons:
            if idx + 1 + h < n:
                rec[f"drift{h}"] = float((close[idx + 1 + h] - entry) * dir_ / atr[idx])
        target = entry + dir_ * atr[idx]
        stop = entry - dir_ * atr[idx]
        outcome = None
        j_last = idx
        for j in range(idx + 1, min(idx + 1 + touch_h, n)):
            j_last = j
            if dir_ > 0:
                hit_t = high[j] >= target
                hit_s = low[j] <= stop
            else:
                hit_t = low[j] <= target
                hit_s = high[j] >= stop
            if hit_t and hit_s:
                outcome = "win" if (close[j] - entry) * dir_ > 0 else "loss"
            elif hit_t:
                outcome = "win"
            elif hit_s:
                outcome = "loss"
            if outcome is not None:
                break
        rec["touch"] = outcome
        rec["touch_bars"] = j_last - idx if outcome is not None else None
        events.append(rec)
    return events


def random_baseline(df, atr, dirs, event_idxs, horizons, touch_h, rng, mask=None):
    """Same-direction random-bar baseline. One random bar per event, kept at
    least 25 bars away; optionally restricted to a session mask."""
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    lo, hi = touch_h + 2, n - touch_h - 2
    out = []
    for d, ei in zip(dirs, event_idxs):
        r = -1
        tries = 0
        while tries < 2000:
            r = int(rng.integers(lo, hi))
            if abs(r - ei) > 25 and (mask is None or mask[r]) and not np.isnan(atr[r]) and atr[r] > 0:
                break
            tries += 1
        if tries >= 2000:
            continue
        entry = float(open_[r + 1])
        rec = {"idx": r, "dir": int(d), "entry": entry}
        for h in horizons:
            rec[f"drift{h}"] = float((close[r + 1 + h] - entry) * d / atr[r])
        target = entry + d * atr[r]
        stop = entry - d * atr[r]
        outcome = None
        j_last = r
        for j in range(r + 1, min(r + 1 + touch_h, n)):
            j_last = j
            if d > 0:
                hit_t = high[j] >= target
                hit_s = low[j] <= stop
            else:
                hit_t = low[j] <= target
                hit_s = high[j] >= stop
            if hit_t and hit_s:
                outcome = "win" if (close[j] - entry) * d > 0 else "loss"
            elif hit_t:
                outcome = "win"
            elif hit_s:
                outcome = "loss"
            if outcome is not None:
                break
        rec["touch"] = outcome
        out.append(rec)
    return out


# ----------------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------------
def bootstrap_mean_diff(a: np.ndarray, b: np.ndarray, rng, n_resample: int = 2000) -> dict:
    """Two-sample bootstrap CI and p-value for mean(a) - mean(b) (unequal n ok).
    Resamples are drawn with size min(n, 2000) to keep the computation bounded
    for large event counts; the test remains valid and conservative."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return {"diff": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "p": float("nan")}
    ma, mb = min(na, 2000), min(nb, 2000)
    ia = rng.integers(0, na, size=(n_resample, ma))
    ib = rng.integers(0, nb, size=(n_resample, mb))
    diffs = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    p = float(2 * min(np.mean(diffs <= 0), np.mean(diffs >= 0)))
    return {"diff": float(np.mean(a) - np.mean(b)), "ci_lo": float(lo), "ci_hi": float(hi), "p": p}


def cell_stats(events: list[dict], baseline: list[dict], horizons, touch_h, split_idx: int) -> dict:
    ev = np.array([e["idx"] for e in events])
    bs = np.array([b["idx"] for b in baseline])
    first = ev < split_idx
    second = ~first
    b_first = bs < split_idx
    out: dict = {
        "n": len(events),
        "n_up": int(np.sum([e["dir"] > 0 for e in events])),
        "n_dn": int(np.sum([e["dir"] < 0 for e in events])),
        "horizons": {},
    }
    for h in horizons:
        ev_v = np.array([e.get(f"drift{h}", np.nan) for e in events], dtype=float)
        bs_v = np.array([b.get(f"drift{h}", np.nan) for b in baseline], dtype=float)
        ev_v = ev_v[~np.isnan(ev_v)]
        bs_v = bs_v[~np.isnan(bs_v)]
        n_ev = len(ev_v)
        row = {
            "mean_atr": float(np.mean(ev_v)) if n_ev else float("nan"),
            "sd_atr": float(np.std(ev_v, ddof=1)) if n_ev > 1 else float("nan"),
            "t_vs_zero": float(np.mean(ev_v) / (np.std(ev_v, ddof=1) / np.sqrt(n_ev))) if n_ev > 1 else 0.0,
            "baseline_mean_atr": float(np.mean(bs_v)) if len(bs_v) else float("nan"),
            "bootstrap": bootstrap_mean_diff(ev_v, bs_v, RNG),
        }
        ev_first = np.array([e.get(f"drift{h}", np.nan) for e, f in zip(events, first) if f], dtype=float)
        ev_sec = np.array([e.get(f"drift{h}", np.nan) for e, f in zip(events, second) if f], dtype=float)
        bs_first = np.array([b.get(f"drift{h}", np.nan) for b, f in zip(baseline, b_first) if f], dtype=float)
        bs_sec = np.array([b.get(f"drift{h}", np.nan) for b, f in zip(baseline, ~b_first) if f], dtype=float)
        row["first_half_mean"] = float(np.nanmean(ev_first))
        row["second_half_mean"] = float(np.nanmean(ev_sec))
        row["first_half_n"] = int(np.sum(~np.isnan(ev_first)))
        row["second_half_n"] = int(np.sum(~np.isnan(ev_sec)))
        row["baseline_first_half"] = float(np.nanmean(bs_first))
        row["baseline_second_half"] = float(np.nanmean(bs_sec))
        out["horizons"][h] = row
    ev_out = np.array([1.0 if e["touch"] == "win" else 0.0 for e in events if e["touch"] is not None])
    bs_out = np.array([1.0 if b["touch"] == "win" else 0.0 for b in baseline if b["touch"] is not None])
    n_dec = len(ev_out)
    wr = float(np.mean(ev_out)) if n_dec else float("nan")
    wr_bs = float(np.mean(bs_out)) if len(bs_out) else float("nan")
    z = (wr - 0.5) / (np.sqrt(0.25 / n_dec)) if n_dec else 0.0
    touch = {
        "horizon": touch_h,
        "n_decided": n_dec,
        "n_no_touch": len(events) - n_dec,
        "win_rate": wr,
        "baseline_win_rate": wr_bs,
        "z_vs_50pct": float(z),
        "bootstrap": bootstrap_mean_diff(ev_out, bs_out, RNG),
    }
    ev_first_w = [e for e, f in zip(events, first) if f and e["touch"] is not None]
    ev_sec_w = [e for e, f in zip(events, second) if f and e["touch"] is not None]
    touch["first_half_wr"] = float(np.mean([e["touch"] == "win" for e in ev_first_w])) if ev_first_w else float("nan")
    touch["second_half_wr"] = float(np.mean([e["touch"] == "win" for e in ev_sec_w])) if ev_sec_w else float("nan")
    touch["first_half_n"] = len(ev_first_w)
    touch["second_half_n"] = len(ev_sec_w)
    out["touch"] = touch
    return out


def run_cell(name: str, events, df, atr, horizons, touch_h, split_idx, rng, masks: dict | None, min_n: int = 200) -> dict:
    dirs = np.array([e["dir"] for e in events])
    idxs = np.array([e["idx"] for e in events])
    baseline = random_baseline(df, atr, dirs, idxs, horizons, touch_h, rng)
    cells = {"all": cell_stats(events, baseline, horizons, touch_h, split_idx)}
    for kname, m in (masks or {}).items():
        sub = [e for e in events if m[e["idx"]]]
        if len(sub) < min(50, len(events)):
            continue
        sub_dirs = np.array([e["dir"] for e in sub])
        sub_idxs = np.array([e["idx"] for e in sub])
        sub_bs = random_baseline(df, atr, sub_dirs, sub_idxs, horizons, touch_h, rng, mask=m)
        cells[f"killzone_{kname}"] = cell_stats(sub, sub_bs, horizons, touch_h, split_idx)
    return cells


# ----------------------------------------------------------------------------
# detectors (exact spec definitions)
# ----------------------------------------------------------------------------
def detect_displacement(df, atr, horizons, touch_h):
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    body = np.abs(close - open_)
    mean_body = rolling_mean(body, 5)
    up_wick = high - np.maximum(close, open_)
    dn_wick = np.minimum(close, open_) - low
    L_up = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close > open_)
    L_dn = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close < open_)
    pairs = [(i, 1, None) for i in np.nonzero(L_up)[0]] + [(i, -1, None) for i in np.nonzero(L_dn)[0]]
    return postprocess(pairs, df, atr, horizons, touch_h)


def detect_fvg_cont(df, atr, horizons, touch_h):
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    body = np.abs(close - open_)
    mean_body = rolling_mean(body, 5)
    up_wick = high - np.maximum(close, open_)
    dn_wick = np.minimum(close, open_) - low
    L_up = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close > open_)
    L_dn = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close < open_)
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    up[2:] = L_up[1:-1] & (low[2:] > high[:-2])  # L_bodyUP[1] AND low > high[2]
    dn[2:] = L_dn[1:-1] & (high[2:] < low[:-2])  # L_bodyDN[1] AND high < low[2]
    pairs = [(i, 1, None) for i in np.nonzero(up)[0]] + [(i, -1, None) for i in np.nonzero(dn)[0]]
    return postprocess(pairs, df, atr, horizons, touch_h)


def detect_fvg_reentry(df, atr, horizons, touch_h, max_age=500):
    """FVG formed at bar i; later bar j straddles the zone -> rebalance event at j.
    Bull zone [high[i-2], low[i]]; re-entry: low[j] <= top AND high[j] >= top.
    Bear zone [high[i], low[i-2]]; re-entry: high[j] >= bottom AND low[j] <= bottom.
    Only the FIRST bar of each visit is an event (visits merged)."""
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    body = np.abs(close - open_)
    mean_body = rolling_mean(body, 5)
    up_wick = high - np.maximum(close, open_)
    dn_wick = np.minimum(close, open_) - low
    L_up = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close > open_)
    L_dn = (body > mean_body) & (up_wick < body * 0.36) & (dn_wick < body * 0.36) & (close < open_)
    forms_up = np.zeros(n, dtype=bool)
    forms_dn = np.zeros(n, dtype=bool)
    forms_up[2:] = L_up[1:-1] & (low[2:] > high[:-2])
    forms_dn[2:] = L_dn[1:-1] & (high[2:] < low[:-2])
    pairs = []
    for i in np.nonzero(forms_up)[0]:
        top, bot = low[i], high[i - 2]
        j = i + 1
        end = min(i + max_age + 1, n - touch_h - 2)
        while j < end:
            chunk = np.arange(j, end)
            hit = np.nonzero((low[chunk] <= top) & (high[chunk] >= top))[0]
            if len(hit) == 0:
                break
            jj = j + int(hit[0])
            meta = {"form_idx": int(i), "zone_top": float(top), "zone_bot": float(bot),
                    "depth_atr": float((top - low[jj]) / atr[jj]) if not np.isnan(atr[jj]) and atr[jj] > 0 else float("nan")}
            pairs.append((jj, 1, meta))
            k = jj + 1
            while k < end and low[k] <= top:
                k += 1
            j = k
    for i in np.nonzero(forms_dn)[0]:
        top, bot = high[i], low[i - 2]
        j = i + 1
        end = min(i + max_age + 1, n - touch_h - 2)
        while j < end:
            chunk = np.arange(j, end)
            hit = np.nonzero((high[chunk] >= bot) & (low[chunk] <= bot))[0]
            if len(hit) == 0:
                break
            jj = j + int(hit[0])
            meta = {"form_idx": int(i), "zone_top": float(top), "zone_bot": float(bot),
                    "depth_atr": float((high[jj] - bot) / atr[jj]) if not np.isnan(atr[jj]) and atr[jj] > 0 else float("nan")}
            pairs.append((jj, -1, meta))
            k = jj + 1
            while k < end and high[k] >= bot:
                k += 1
            j = k
    return postprocess(pairs, df, atr, horizons, touch_h)


def detect_mss_bos(df, atr, horizons, touch_h, left=3, right=1):
    """MSS (simplified spec): latest confirmed swing low AFTER latest confirmed
    swing high (lower-low context) + close > swing high -> bullish MSS (dir=+1);
    mirror -> bearish (dir=-1). BOS: close > latest swing high while structure
    already bullish (MSS.dir==+1), only when NO lower-low formed after that high;
    mirror for bearish. Swing highs/lows are consumed by events; a new pivot is
    required for the next event. strict flag = lower low below the swing low
    that preceded the swing high (classic lower-low)."""
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    ph, pl = find_pivots_lr(high, low, left, right)
    pairs_mss: list[tuple[int, int, dict]] = []
    pairs_bos: list[tuple[int, int, dict]] = []
    sh_i, sh_v = -1, float("nan")
    sl_i, sl_v = -1, float("nan")
    prev_sl_i, prev_sl_v = -1, float("nan")  # swing low before the latest swing high
    prev_sh_i, prev_sh_v = -1, float("nan")  # swing high before the latest swing low
    struct = 0
    consumed_sh = -1  # latest swing high used by any MSS/BOS event
    consumed_sl = -1
    for e in range(left, n):
        p = e - left  # pivot candidate at p confirmed at close of bar e
        if ph[p]:
            prev_sl_i, prev_sl_v = sl_i, sl_v
            sh_i, sh_v = p, high[p]
        if pl[p]:
            prev_sh_i, prev_sh_v = sh_i, sh_v
            sl_i, sl_v = p, low[p]
        if e >= n - touch_h - 2:
            continue
        if np.isnan(atr[e]) or atr[e] <= 0:
            continue
        # --- bullish MSS: swing low after swing high, close > swing high ---
        if sh_i >= 0 and sl_i > sh_i and sh_i > consumed_sh and close[e] > sh_v:
            strict = prev_sl_i >= 0 and sl_v < prev_sl_v
            pairs_mss.append((e, 1, {"strict": bool(strict), "sh_i": int(sh_i), "sl_i": int(sl_i)}))
            consumed_sh = sh_i
            struct = 1
        # --- bearish MSS ---
        elif sl_i >= 0 and sh_i > sl_i and sl_i > consumed_sl and close[e] < sl_v:
            strict = prev_sh_i >= 0 and sh_v > prev_sh_v
            pairs_mss.append((e, -1, {"strict": bool(strict), "sh_i": int(sh_i), "sl_i": int(sl_i)}))
            consumed_sl = sl_i
            struct = -1
        # --- bullish BOS (structure already up, no lower-low after this high) ---
        elif struct == 1 and sh_i > consumed_sh and close[e] > sh_v and not (sl_i > sh_i):
            pairs_bos.append((e, 1, {"sh_i": int(sh_i)}))
            consumed_sh = sh_i
        # --- bearish BOS ---
        elif struct == -1 and sl_i > consumed_sl and close[e] < sl_v and not (sh_i > sl_i):
            pairs_bos.append((e, -1, {"sl_i": int(sl_i)}))
            consumed_sl = sl_i
    return postprocess(pairs_mss, df, atr, horizons, touch_h), postprocess(pairs_bos, df, atr, horizons, touch_h)


def detect_vimb(df, atr, horizons, touch_h):
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    o1 = np.roll(open_, 1)
    c1 = np.roll(close, 1)
    h1 = np.roll(high, 1)
    l1 = np.roll(low, 1)
    o1[0] = c1[0] = h1[0] = l1[0] = np.nan
    # vImb_Bl: open>close[1] AND high[1]>low AND close>close[1] AND open>open[1] AND high[1]<min(close,open)
    bl = (open_ > c1) & (h1 > low) & (close > c1) & (open_ > o1) & (h1 < np.minimum(close, open_))
    # vImb_Br: mirror
    br = (open_ < c1) & (l1 < high) & (close < c1) & (open_ < o1) & (l1 > np.maximum(close, open_))
    pairs = [(i, 1, None) for i in np.nonzero(bl)[0]] + [(i, -1, None) for i in np.nonzero(br)[0]]
    return postprocess(pairs, df, atr, horizons, touch_h)


# ----------------------------------------------------------------------------
# costs
# ----------------------------------------------------------------------------
def cost_points_for(symbol: str) -> float:
    """User cost assumptions: FX 1.5 pips (5-digit -> 15 points), XAU 12 pts,
    synthetics 2 pts."""
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s:
        return 12.0
    if s.startswith(("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDJPY", "USDCHF", "US30", "NAS")):
        return 15.0 if s != "XAUUSD" else 12.0
    return 2.0  # synthetics (Boom/Crash, point = 0.001)


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", choices=["PROP", "DERIV"], required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--bars", type=int, default=50000)
    ap.add_argument("--atr-n", type=int, default=14)
    ap.add_argument("--horizons", default="3,5,10,20")
    ap.add_argument("--touch-h", type=int, default=TOUCH_H)
    ap.add_argument("--reentry-max-age", type=int, default=500)
    ap.add_argument("--min-n", type=int, default=200)
    ap.add_argument("--no-killzones", action="store_true", help="disable session masks (synthetics)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    horizons = tuple(int(x) for x in args.horizons.split(","))
    touch_h = args.touch_h
    global RNG
    RNG = np.random.default_rng(BASE_SEED + zlib.crc32(f"{args.symbol}|{args.tf}".encode()) % (10**6))

    t0 = time.time()
    df, sym_info = fetch_rates(args.terminal, args.symbol, args.tf, args.bars)
    fetch_s = time.time() - t0
    print(f"fetched {len(df)} bars in {fetch_s:.0f}s", flush=True)
    n = len(df)
    split_idx = n // 2
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    atr = wilder_atr(high, low, close, args.atr_n)

    use_kz = (args.terminal == "PROP") and not args.no_killzones
    masks = session_masks(df) if use_kz else None
    if use_kz:
        print(f"killzone masks: ny={masks['ny'].sum()}, london={masks['london'].sum()}, union={masks['union'].sum()} bars", flush=True)

    cells: dict[str, dict] = {}
    t1 = time.time()
    print("scan displacement...", flush=True)
    cells["displacement"] = run_cell("displacement", detect_displacement(df, atr, horizons, touch_h),
                                     df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)
    t1 = time.time()
    print("scan fvg_cont...", flush=True)
    cells["fvg_cont"] = run_cell("fvg_cont", detect_fvg_cont(df, atr, horizons, touch_h),
                                 df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)
    t1 = time.time()
    print("scan fvg_reentry...", flush=True)
    cells["fvg_reentry"] = run_cell("fvg_reentry", detect_fvg_reentry(df, atr, horizons, touch_h, args.reentry_max_age),
                                    df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)
    t1 = time.time()
    print("scan mss...", flush=True)
    mss_ev, bos_ev = detect_mss_bos(df, atr, horizons, touch_h)
    cells["mss"] = run_cell("mss", mss_ev, df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)
    t1 = time.time()
    print("scan bos...", flush=True)
    cells["bos"] = run_cell("bos", bos_ev, df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)
    t1 = time.time()
    print("scan vimb...", flush=True)
    cells["vimb"] = run_cell("vimb", detect_vimb(df, atr, horizons, touch_h),
                             df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)
    print(f"  {time.time()-t1:.0f}s", flush=True)

    # strict lower-low MSS sub-cell (robustness variant)
    strict_ev = [e for e in mss_ev if e.get("meta", {}).get("strict")]
    cells["mss_strict"] = run_cell("mss_strict", strict_ev, df, atr, horizons, touch_h, split_idx, RNG, masks, args.min_n)

    # instrument info / costs
    point = float(sym_info.get("point", 0.0) or 0.0)
    med_spread_points = float(np.nanmedian(df["spread"].to_numpy(float)))
    med_atr_price = float(np.nanmedian(atr[~np.isnan(atr)]))
    med_atr_points = med_atr_price / point if point else float("nan")
    cost_points = cost_points_for(args.symbol)
    cost_atr = cost_points / med_atr_points if med_atr_points else float("nan")
    info = {
        "symbol": args.symbol,
        "tf": args.tf,
        "bars": n,
        "start": str(df["time"].iloc[0]),
        "end": str(df["time"].iloc[-1]),
        "point": point,
        "median_spread_points": med_spread_points,
        "median_atr_points": med_atr_points,
        "cost_assumption_points": cost_points,
        "cost_atr_units": cost_atr,
        "killzones": use_kz,
    }

    result = {"cells": cells, "info": info, "params": vars(args)}

    # ---- console summary ----
    print("=" * 110)
    print(f"ICT VALIDATION | {args.symbol} {args.tf} | {n} bars ({info['start'][:10]} -> {info['end'][:10]}) "
          f"| cost {cost_atr:.3f} ATR ({cost_points:g} pts)")
    for cname, cell in cells.items():
        for kname, st in cell.items():
            tag = "" if kname == "all" else f"[{kname}]"
            h3, h20 = st["horizons"][3], st["horizons"][20]
            h10 = st["horizons"][10]
            print(f"{cname + tag:<32} n={st['n']:>6} (up {st['n_up']:>5}) "
                  f"d3 {h3['mean_atr']:+.4f} bs {h3['baseline_mean_atr']:+.4f} p={h3['bootstrap']['p']:.3f} | "
                  f"d10 {h10['mean_atr']:+.4f} bs {h10['baseline_mean_atr']:+.4f} p={h10['bootstrap']['p']:.3f} | "
                  f"d20 {h20['mean_atr']:+.4f} bs {h20['baseline_mean_atr']:+.4f} p={h20['bootstrap']['p']:.3f} "
                  f"halves [{h20['first_half_mean']:+.4f}/{h20['second_half_mean']:+.4f}] | "
                  f"touch WR {st['touch']['win_rate']:.3f} (n={st['touch']['n_decided']}) bs {st['touch']['baseline_win_rate']:.3f} "
                  f"p={st['touch']['bootstrap']['p']:.3f}")
    print(f"  total {time.time()-t0:.0f}s (fetch {fetch_s:.0f}s)")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"wrote {args.out}")


RNG = np.random.default_rng(BASE_SEED)

if __name__ == "__main__":
    main()
