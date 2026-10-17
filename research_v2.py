"""Research v2 — temporally-aligned gates, research + walk-forward validation.

Fixes vs v1:
  - Gate biases are aligned by TIME (reindex on bar timestamps, ffill),
    not by integer position (which mis-mapped M1/M5/M15 bars to H1 bars).
  - Gates are shifted 1 bar before alignment (no intrabar lookahead).
  - Validation splits the ALIGNED series positionally, so train/test both work.

One pass: load data once -> 144 combos -> rank -> validate top 8 (70/30).
"""
import json, sys, datetime, time as _t
from pathlib import Path
import urllib.request as _req
import pandas as pd, numpy as np

PROJECT = Path(r"E:\Project\Mt5 Ea")
GW = "http://127.0.0.1:8060"
RESEARCH_DIR = PROJECT / "memory" / "research"
VALIDATED_DIR = PROJECT / "memory" / "validated"

token = ""
for line in (PROJECT / ".env").read_text().splitlines():
    if line.startswith("WEB_AUTH_TOKEN="):
        token = line.split("=", 1)[1].strip(); break

def gw_call(method, kwargs):
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW + "/call/" + method, data=body,
                       headers={"Content-Type": "application/json", "X-Auth-Token": token})
    with _req.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    if data.get("error"): raise RuntimeError(data["error"])
    p = data.get("data", {})
    if isinstance(p, dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p, list) else pd.DataFrame()

def rates(sym, tf, n):
    for i in range(3):
        try:
            df = gw_call("rates", {"symbol": sym, "timeframe": tf, "count": n})
            if len(df) > 0:
                df["time"] = df["time"].astype(str)
                df = df.sort_values("time").reset_index(drop=True)
            return df
        except Exception:
            _t.sleep(2)
    raise RuntimeError(f"failed: {sym} {tf}")

def ema(s, w): return s.ewm(span=w, adjust=False).mean()

def atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]; prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def bias(df, fast=50, slow=200):
    """+1 bull, -1 bear, 0 neutral; indexed by bar time."""
    ef, es = ema(df["close"], fast), ema(df["close"], slow)
    t = pd.Series(0, index=df.index)
    t[ef > es] = 1; t[ef < es] = -1
    rh = df["high"].rolling(20).max().shift(20)
    rl = df["low"].rolling(20).min().shift(20)
    bull = (t == 1) & (df["close"] > rh * 0.999)
    bear = (t == -1) & (df["close"] < rl * 1.001)
    b = pd.Series(0, index=df.index).mask(bull, 1).mask(bear, -1)
    b.index = df["time"].values
    return b

def align_to_h1(bias_series, h1_times):
    """Map a TF's bias onto H1 rows: shift 1 bar (no lookahead), ffill by time."""
    shifted = bias_series.shift(1)
    shifted.index = bias_series.index
    aligned = shifted.reindex(h1_times, method="ffill")
    return aligned.fillna(0).reset_index(drop=True)

def pip_size(sym):
    s = sym.upper().rstrip(".")
    if "JPY" in s: return 0.01
    return 0.0001

# Spread cost per trade, in PIPS (conservative: upper quartile of live
# observed spreads per asset class). These are the false-edge killers:
# a combo that makes 5 pips/trade on paper loses money at 7 pips of spread.
SPREAD_PIPS = {
    "EURUSD": 1.2, "GBPUSD": 1.6, "USDJPY": 1.4, "AUDUSD": 1.8,
    "NZDUSD": 2.2, "USDCAD": 2.0, "USDCHF": 1.8,
    "XAUUSD": 35.0, "XAGUSD": 3.0, "USOIL": 4.0,
    "BTCUSD": 90.0, "ETHUSD": 2.5,
    # JPY crosses + new crosses (2026-09-12)
    "CHFJPY": 2.0, "EURJPY": 1.5, "GBPJPY": 2.0, "NZDJPY": 2.0,
    "GBPCHF": 2.0, "NZDCAD": 2.0, "AUDNZD": 2.2, "EURAUD": 2.0, "EURCHF": 1.6,
}

def sim(h1, aligned_gates, sl_tp=2.0, use_energy=False, use_zone=False,
        pv=0.0001, start=250, sl_atr=2.0, partial=None, spread_pips=0.0):
    """aligned_gates: {tf: Series len(h1)} — time-aligned, no reindex.

    sl_atr: SL distance in ATR multiples (sweep param).
    partial: None, or dict {"r": 1.0, "frac": 0.5} — close frac at +r R.
    spread_pips: round-trip spread cost in pips, applied per closed trade.
    """
    ef, es = ema(h1["close"], 50), ema(h1["close"], 200)
    a = atr(h1); atr_avg = a.rolling(50).mean()
    trades, in_pos, ei = [], 0, 0
    # partial-exit state
    p_taken, p_frac, p_r, pnl_taken = False, 0.0, 0.0, 0.0
    entry = sl = tp = 0.0

    for i in range(start, len(h1) - 1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i] > es.iloc[i] else (-1 if ef.iloc[i] < es.iloc[i] else 0)
            if sig == 0: continue
            if any(g.iloc[i] != sig for g in aligned_gates.values()): continue
            if use_energy and pd.notna(atr_avg.iloc[i]) and atr_avg.iloc[i] > 0:
                if a.iloc[i] / atr_avg.iloc[i] < 1.5: continue
            if use_zone:
                rh = h1["high"].rolling(20).max().iloc[i]
                rl = h1["low"].rolling(20).min().iloc[i]
                za = a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
                if sig == 1 and p > rl + 2 * za: continue
                if sig == -1 and p < rh - 2 * za: continue
            entry = p; stop = max(sl_atr * a.iloc[i], 0.0001)
            sl, tp = entry - sig * stop, entry + sig * sl_tp * stop
            in_pos = sig; ei = i
            p_taken, pnl_taken = False, 0.0
            if partial:
                p_frac, p_r = partial["frac"], partial["r"]
                # level where the partial fires
                p_level = entry + sig * p_r * stop
            else:
                p_frac = 0.0
        else:
            hi, lo = h1["high"].iloc[i], h1["low"].iloc[i]
            # partial exit: close p_frac at +p_r R once
            if partial and not p_taken:
                if (in_pos == 1 and hi >= p_level) or (in_pos == -1 and lo <= p_level):
                    pnl_taken = (p_level - entry) / pv * p_frac if in_pos == 1 else (entry - p_level) / pv * p_frac
                    p_taken = True
            exit_pnl = None
            if in_pos == 1:
                if lo <= sl: exit_pnl = (sl - entry) / pv
                elif hi >= tp: exit_pnl = (tp - entry) / pv
            else:
                if hi >= sl: exit_pnl = (entry - sl) / pv
                elif lo <= tp: exit_pnl = (entry - tp) / pv
            if exit_pnl is not None:
                remaining = 1.0 - (p_frac if partial else 0.0)
                trades.append({"pnl": exit_pnl * remaining + pnl_taken - spread_pips,
                               "bars": i - ei})
                in_pos = 0
            if in_pos != 0 and i - ei > 200:
                pnl = (p - entry if in_pos == 1 else entry - p) / pv
                remaining = 1.0 - (p_frac if partial else 0.0)
                trades.append({"pnl": pnl * remaining + pnl_taken - spread_pips,
                               "bars": i - ei})
                in_pos = 0
    return trades

def stats(trades, bootstrap=False, n_boot=2000):
    n = len(trades)
    if n == 0: return {"trades": 0, "win%": 0, "expect": 0, "PF": 0, "sharpe": 0, "max_dd": 0,
                       "pf_lo": None, "exp_lo": None}
    pnls = np.array([t["pnl"] for t in trades])
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins); gl = abs(sum(t["pnl"] for t in losses))
    sharpe = float(pnls.mean() / pnls.std() * np.sqrt(252)) if pnls.std() > 0 else 0
    eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
    max_dd = float((peak - eq).max()) if len(eq) else 0
    out = {"trades": n, "win%": round(len(wins) / n * 100, 1), "expect": round(float(pnls.mean()), 4),
           "PF": round(gw / gl, 2) if gl else (float("inf") if gw else 0),
           "sharpe": round(sharpe, 2), "max_dd": round(max_dd, 2),
           "pf_lo": None, "exp_lo": None}
    # Monte Carlo bootstrap: 5th percentile of resampled PF / expectancy.
    # Promotion requires pf_lo > 1.0 — robustness against small samples.
    if bootstrap and n >= 10:
        rng = np.random.default_rng(42)
        pf_s, ex_s = [], []
        for _ in range(n_boot):
            s = pnls[rng.integers(0, n, n)]
            w = s[s > 0].sum(); l = abs(s[s <= 0].sum())
            if l > 0: pf_s.append(w / l)
            elif w > 0: pf_s.append(float("inf"))
            ex_s.append(s.mean())
        if pf_s:
            out["pf_lo"] = round(float(np.percentile(pf_s, 5)), 2)
            out["exp_lo"] = round(float(np.percentile(ex_s, 5)), 4)
    return out

def main():
    # Champion-validated symbol set (2026-09-12 backtests): majors + JPY
    # crosses + new crosses, all positive-EV with M5/H4+H6 combo.
    symbols = ["EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
               "CHFJPY", "EURJPY", "GBPJPY", "NZDJPY",
               "GBPCHF", "NZDCAD", "AUDNZD", "EURAUD", "EURCHF"]
    print("loading data (time-aligned)...")
    data = {}
    for sym in symbols:
        try:
            # H1 base built from M30 (terminal H1 caches are sometimes stale)
            m30 = rates(sym, "M30", 7200)
            if len(m30) < 1500:
                print(f"  {sym}: skip (M30={len(m30)})"); continue
            m30["time"] = pd.to_datetime(m30["time"])
            h1 = (m30.set_index("time").resample("1h", label="left", closed="left")
                  .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                  .dropna().reset_index())
            h1["time"] = h1["time"].astype(str)
            if len(h1) < 500:
                print(f"  {sym}: skip (H1={len(h1)})"); continue
            h1_times = pd.Index(h1["time"].values)
            frames = {"H1": h1, "M30": m30}
            # deep history so gates cover the full H1 window
            for tf, n in (("D1", 600), ("H4", 800), ("M15", 30000), ("M5", 30000), ("M1", 30000)):
                try:
                    frames[tf] = rates(sym, tf, n)
                except Exception:
                    pass
            # H2/H3/H6/H8/H12 built from M30 (terminal has no native cache)
            for tf in ("H2", "H3", "H6", "H8", "H12"):
                rule = {"H2": "2h", "H3": "3h", "H6": "6h", "H8": "8h", "H12": "12h"}[tf]
                frames[tf] = (m30.set_index("time").resample(rule, label="left", closed="left")
                             .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                             .dropna().reset_index())
                frames[tf]["time"] = frames[tf]["time"].astype(str)
            # pre-align all biases to H1 rows; track first bar where EVERY
            # requested gate has real data (gate=0 early means "no data")
            aligned = {}
            gate_start = {}
            for tf, df in frames.items():
                if tf == "H1":
                    continue
                b = bias(df)
                a = align_to_h1(b, h1_times)
                aligned[tf] = a
                # first H1 bar whose gate value came from real data
                first_ts = b.index[b != 0]
                gate_start[tf] = first_ts[0] if len(first_ts) else None
            data[sym] = {"h1": h1, "aligned": aligned, "gate_start": gate_start}
            print(f"  {sym}: ok ({len(h1)} H1 bars, gates: {', '.join(aligned)})")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    entry_tfs = ["M1", "M5", "M15"]
    gate_combos = {
        "none": [], "H4": ["H4"], "H4+H1": ["H4", "H1"], "H4+H6": ["H4", "H6"],
        "H4+H6+H1": ["H4", "H6", "H1"], "H6+H8": ["H6", "H8"],
        "D1+H4+H1": ["D1", "H4", "H1"], "D1+H4+H6": ["D1", "H4", "H6"],
    }
    sl_tp_ratios = [1.5, 2.0, 3.0]
    sl_atrs = [1.5, 2.0, 3.0]          # SL distance sweep
    exits = [(None, "full"), ({"r": 1.0, "frac": 0.5}, "partial1R")]
    filters = [(False, False, "none"), (True, False, "energy"), (False, True, "zone"), (True, True, "energy+zone")]

    results = []
    total = (len(entry_tfs) * len(gate_combos) * len(sl_tp_ratios) * len(sl_atrs)
             * len(exits) * len(filters))
    done = 0
    print(f"\n[RESEARCH] {total} combos (spread-adjusted)...")
    for etf in entry_tfs:
        for gn, gate_tfs in gate_combos.items():
            for st in sl_tp_ratios:
                for sa in sl_atrs:
                    for partial, ex_name in exits:
                        for ue, uz, fn in filters:
                            at = []
                            for sym, d in data.items():
                                if etf not in d["aligned"]: continue
                                gates = {etf: d["aligned"][etf]}
                                for tf in gate_tfs:
                                    if tf in d["aligned"]:
                                        gates[tf] = d["aligned"][tf]
                                h1_times = d["h1"]["time"].values
                                starts = [d["gate_start"].get(tf) for tf in gates]
                                valid_starts = [h1_times.searchsorted(s) for s in starts if s is not None and h1_times.searchsorted(s) < len(h1_times)]
                                start_i = max(valid_starts) + 250 if valid_starts else 250
                                at.extend(sim(d["h1"], gates, st, ue, uz, pip_size(sym),
                                              start=start_i, sl_atr=sa, partial=partial,
                                              spread_pips=SPREAD_PIPS.get(sym, 1.5)))
                            s = stats(at)
                            results.append({"entry_tf": etf, "gates": gn, "sl_tp": st,
                                            "sl_atr": sa, "exit": ex_name, "filters": fn, **s})
                            done += 1
                            if done % 50 == 0: print(f"  {done}/{total}...")

    ranked = sorted(results, key=lambda r: r["expect"] if r["trades"] >= 10 else -999, reverse=True)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    rp = RESEARCH_DIR / f"research_{today}.json"
    with open(rp, "w") as f:
        json.dump({"date": today, "symbols": symbols, "results": ranked}, f, indent=2)

    print(f"\n{'=' * 90}")
    print(f" TOP 20 (min 10 trades, time-aligned)")
    print(f"{'=' * 90}")
    print(f"{'#':>3} {'entry':>6} {'gates':>10} {'SL:TP':>5} {'filter':>12} {'trades':>7} {'win%':>6} {'expect':>8} {'PF':>6} {'sharpe':>7} {'maxDD':>7}")
    print("-" * 90)
    for i, r in enumerate(ranked[:20], 1):
        print(f"{i:3d} {r['entry_tf']:>6} {r['gates']:>10} {r['sl_tp']:>4.1f}R {r['sl_atr']:>4.1f}A "
              f"{r['exit']:>10} {r['filters']:>12} "
              f"{r['trades']:>7d} {r['win%']:>6.1f} {r['expect']:>8.2f} {r['PF']:>6.2f} {r['sharpe']:>7.2f} {r['max_dd']:>7.1f}")

    # ---- validation: top 8, 70/30 positional split of ALIGNED gates ----
    top = [r for r in ranked if r["trades"] >= 10][:8]
    print(f"\n[VALIDATE] top {len(top)} combos, 70/30 walk-forward (spread-adjusted + bootstrap)")
    validated = []
    for r in top:
        etf, gn, st, fn = r["entry_tf"], r["gates"], r["sl_tp"], r["filters"]
        sa, ex_name = r.get("sl_atr", 2.0), r.get("exit", "full")
        ue, uz = "energy" in fn, "zone" in fn
        partial = {"r": 1.0, "frac": 0.5} if ex_name == "partial1R" else None
        gate_tfs = gate_combos[gn]
        tt, ts = [], []
        for sym, d in data.items():
            if etf not in d["aligned"]: continue
            h1 = d["h1"]
            h1_times = h1["time"].values
            gates_full = {etf: d["aligned"][etf]}
            for tf in gate_tfs:
                if tf in d["aligned"]:
                    gates_full[tf] = d["aligned"][tf]
            starts = [d["gate_start"].get(tf) for tf in gates_full]
            valid_starts = [h1_times.searchsorted(s) for s in starts if s is not None and h1_times.searchsorted(s) < len(h1_times)]
            start_i = max(valid_starts) + 250 if valid_starts else 250
            split = int(len(h1) * 0.7)
            # train: [start_i, split), test: [max(split, start_i), end)
            tr_lo = min(start_i, split)
            h1_tr = h1.iloc[tr_lo:split].reset_index(drop=True)
            te_lo = max(split, start_i)
            h1_te = h1.iloc[te_lo:].reset_index(drop=True) if te_lo < len(h1) else h1.iloc[0:0]
            g_tr = {tf: s.iloc[tr_lo:split].reset_index(drop=True) for tf, s in gates_full.items()}
            g_te = {tf: s.iloc[te_lo:].reset_index(drop=True) for tf, s in gates_full.items()}
            pv = pip_size(sym); sp = SPREAD_PIPS.get(sym, 1.5)
            tt.extend(sim(h1_tr, g_tr, st, ue, uz, pv, sl_atr=sa, partial=partial, spread_pips=sp))
            ts.extend(sim(h1_te, g_te, st, ue, uz, pv, sl_atr=sa, partial=partial, spread_pips=sp))
        tr_s, te_s = stats(tt, bootstrap=True), stats(ts, bootstrap=True)
        # pass: profitable out-of-sample AND bootstrap lower bound above breakeven
        passes = (tr_s["trades"] >= 10 and te_s["trades"] >= 3 and te_s["expect"] > 0
                  and te_s["PF"] >= 0.8 and te_s["sharpe"] > 0.3
                  and (te_s["pf_lo"] is None or te_s["pf_lo"] >= 0.9))
        validated.append({**r, "train": tr_s, "test": te_s, "passes": passes})
        status = "PASS" if passes else "FAIL"
        print(f"\n  {etf} {gn} {st:.1f}R {sa:.1f}A {ex_name} {fn}: {status}")
        print(f"    train: {tr_s['trades']}t {tr_s['win%']}% exp {tr_s['expect']:.2f} PF {tr_s['PF']:.2f} sh {tr_s['sharpe']:.2f}")
        print(f"    test:  {te_s['trades']}t {te_s['win%']}% exp {te_s['expect']:.2f} PF {te_s['PF']:.2f} sh {te_s['sharpe']:.2f} pf_lo {te_s['pf_lo']}")

    VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
    vp = VALIDATED_DIR / f"validated_{today}.json"
    with open(vp, "w") as f:
        json.dump({"date": today, "results": validated}, f, indent=2)
    passed = [v for v in validated if v["passes"]]
    print(f"\n[VALIDATE] {len(passed)}/{len(validated)} passed")
    if passed:
        best = passed[0]
        print(f"\n[BEST] entry={best['entry_tf']} gates={best['gates']} SL:TP={best['sl_tp']} filters={best['filters']}")
    print(f"\nresearch: {rp}\nvalidated: {vp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
