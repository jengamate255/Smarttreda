"""xau_paper_monitor.py — paper-trade monitor for the XAUUSD M15 strong-close signal.

Validates forward whether the signal found marginally significant in
backtest/sweep_validation.py (Concept B: bar closes in top 10% of its range
AND tick-volume z-score > 1 -> next-3/5-bar continuation in bar direction)
holds out-of-sample. Paper only: no live orders are ever placed.

Rules (identical to the validation study):
  * entry = next bar's open, direction = sign(close - open) of the signal bar
  * drift measured over h=3 and h=5 bars, in ATR-14 units
  * touch outcome: +1 ATR target vs -1 ATR stop within 20 bars
  * baseline = same-direction signed drift of ALL valid bars (same window)

State is persisted in backtest/results/xau_paper_state.json so the monitor
catches up on events missed between runs. Run as often as you like, e.g.
once per day for 2-4 weeks:
    python backtest/xau_paper_monitor.py

Data source: prop dashboard API http://localhost:8051/api/rates (no MT5
attach needed, no conflict with live processes).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(__file__).resolve().parent / "results" / "xau_paper_state.json"
LOG_FILE = Path(__file__).resolve().parent / "results" / "xau_paper_log.txt"

API_BASE = "http://localhost:8051/api/rates"
SYMBOL = "XAUUSD"
TF = "M15"
BARS = 1000

ROLL = 50      # volume z-score window
ATR_N = 14     # Wilder ATR period
RANGE_PCT = 0.9  # close must be in top 10% of bar range
VOL_Z = 1.0    # volume z-score threshold
TOUCH_H = 20   # bars to evaluate +1/-1 ATR target/stop
HORIZONS = (3, 5)


def fetch_rates(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode())


def wilder_atr(high, low, close, n=ATR_N):
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = np.full(len(close), np.nan)
    if len(close) > n:
        atr[n] = np.mean(tr[1 : n + 1])
        for i in range(n + 1, len(close)):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def volume_z(vol, roll=ROLL):
    n = len(vol)
    z = np.full(n, np.nan)
    for i in range(roll, n):
        w = vol[i - roll : i]
        mu, sd = np.mean(w), np.std(w)
        z[i] = (vol[i] - mu) / sd if sd > 0 else 0.0
    return z


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"events": [], "created": dt.datetime.now(dt.timezone.utc).isoformat()}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def append_log(line: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force-fetch", action="store_true", help="ignore API cache wait, always refetch")
    args = ap.parse_args()

    url = f"{API_BASE}?symbol={SYMBOL}&timeframe={TF}&count={BARS}"
    data = fetch_rates(url)
    times = np.array(data["times"], dtype=np.int64)
    open_ = np.array(data["open"], dtype=float)
    high = np.array(data["high"], dtype=float)
    low = np.array(data["low"], dtype=float)
    close = np.array(data["close"], dtype=float)
    vol = np.array(data["volume"], dtype=float)
    n = len(close)

    atr = wilder_atr(high, low, close)
    zvol = volume_z(vol)
    rng = high - low
    range_pct = np.where(rng > 0, (close - low) / np.where(rng > 0, rng, 1.0), 0.5)
    direction = np.where(close > open_, 1.0, -1.0)

    valid = ~np.isnan(zvol) & ~np.isnan(atr) & (atr > 0)
    strong = valid & (range_pct >= RANGE_PCT) & (zvol > VOL_Z)

    state = load_state()
    events = state["events"]

    # baseline: signed drift of all valid bars (h=3,5)
    baseline = {}
    for h in HORIZONS:
        vals = []
        for i in range(ROLL, n - h):
            vals.append((close[i + h] - close[i]) * direction[i] / atr[i])
        baseline[h] = float(np.mean(vals)) if vals else float("nan")

    # ---- detect new events (needs next bar to exist for entry) ----
    known = {e["time"] for e in events}
    added = 0
    for i in range(ROLL, n - 1):
        if not strong[i]:
            continue
        t = int(times[i])
        if str(t) in known:
            continue
        entry = float(open_[i + 1])
        events.append(
            {
                "time": str(t),
                "time_iso": str(dt.datetime.fromtimestamp(t, dt.timezone.utc)),
                "dir": int(direction[i]),
                "entry": entry,
                "atr": float(atr[i]),
                "range_pct": float(range_pct[i]),
                "vol_z": float(zvol[i]),
                "status": "open",
            }
        )
        known.add(str(t))
        added += 1

    # ---- update outcomes ----
    for e in events:
        t = int(e["time"])
        i = int(np.searchsorted(times, t))
        if i >= n or times[i] != t:
            continue  # bar fell off the fetched window; keep last known outcome
        d = e["dir"]
        if e.get("status") == "open":
            if i + 1 + max(HORIZONS) >= n:
                e["current_drift"] = float((close[-1] - e["entry"]) * d / e["atr"])
                continue  # not enough bars yet
            for h in HORIZONS:
                e[f"drift{h}"] = float((close[i + 1 + h] - e["entry"]) * d / e["atr"])
            target = e["entry"] + d * e["atr"]
            stop = e["entry"] - d * e["atr"]
            outcome, bars_used = None, None
            for j in range(i + 1, min(i + 1 + TOUCH_H, n)):
                if d > 0:
                    hit_t, hit_s = high[j] >= target, low[j] <= stop
                else:
                    hit_t, hit_s = low[j] <= target, high[j] >= stop
                if hit_t and hit_s:
                    outcome = "win" if (close[j] - e["entry"]) * d > 0 else "loss"
                elif hit_t:
                    outcome = "win"
                elif hit_s:
                    outcome = "loss"
                if outcome is not None:
                    bars_used = j - i
                    break
            e["status"] = "decided" if outcome is not None else "open"
            e["touch"] = outcome
            e["touch_bars"] = bars_used
            e["touch_win"] = 1.0 if outcome == "win" else (0.0 if outcome == "loss" else None)
            e["current_drift"] = float((close[-1] - e["entry"]) * d / e["atr"])

    save_state(state)

    # ---- report ----
    decided = [e for e in events if e.get("status") == "decided"]
    open_ev = [e for e in events if e.get("status") == "open"]
    n_decided = len(decided)
    wins = sum(1 for e in decided if e.get("touch") == "win")
    losses = sum(1 for e in decided if e.get("touch") == "loss")
    no_touch = n_decided - wins - losses

    lines = []
    lines.append("=" * 78)
    lines.append(f"XAUUSD M15 strong-close paper monitor | {n} bars | {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC")
    lines.append(f"  events total={len(events)} (new this run={added}) | decided={n_decided} | open={len(open_ev)}")
    lines.append(f"  touch WR (1 ATR tgt / 1 ATR stop, {TOUCH_H} bars): "
                 f"{wins/n_decided*100:.1f}% win, {losses/n_decided*100:.1f}% loss, {no_touch} no-touch" if n_decided else
                 "  touch WR: n/a (no decided events yet)")
    for h in HORIZONS:
        ev = [e.get(f"drift{h}") for e in decided if e.get(f"drift{h}") is not None]
        op = [e.get(f"drift{h}") for e in open_ev if e.get(f"drift{h}") is not None]
        if ev:
            m = float(np.mean(ev))
            lines.append(f"  drift{h}b: {m:+.4f} ATR (n={len(ev)}) vs all-bar baseline {baseline[h]:+.4f} | diff {m - baseline[h]:+.4f}")
        elif op:
            m = float(np.mean(op))
            lines.append(f"  drift{h}b: {m:+.4f} ATR (n={len(op)}, open) vs baseline {baseline[h]:+.4f}")
    if open_ev:
        newest = open_ev[-1]
        lines.append(f"  newest open signal: {newest['time_iso']} {'BUY' if newest['dir'] > 0 else 'SELL'} @ {newest['entry']:.2f} "
                     f"current drift {newest.get('current_drift', 0):+.4f} ATR")
    # freshly formed signal on the latest bar?
    last = n - 1
    if strong[last]:
        lines.append(f"  !! LATEST BAR is a strong-close signal ({'BUY' if direction[last] > 0 else 'SELL'} bias) — watch next bars")
    else:
        last5 = [i for i in range(max(ROLL, n - 6), n) if strong[i]]
        if last5:
            lines.append(f"  recent signal bars: {[str(dt.datetime.fromtimestamp(int(times[i]), dt.timezone.utc))[:16] for i in last5]}")
    lines.append("=" * 78)

    for line in lines:
        print(line)
    append_log("\n".join(lines))


if __name__ == "__main__":
    main()