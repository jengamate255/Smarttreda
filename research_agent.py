"""Research Agent — runs the exhaustive TF/gate/filter sweep as a background task.

Usage:
    from research_agent import agent
    agent.start()           # trigger sweep (non-blocking)
    status = agent.status()  # progress dict
    results = agent.latest() # last completed results
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("research_agent")

PROJECT = Path(r"E:\Project\Mt5 Ea")
GW = "http://127.0.0.1:8060"
RESEARCH_DIR = PROJECT / "memory" / "research"
VALIDATED_DIR = PROJECT / "memory" / "validated"

# --- gateway helper (urllib, no fastapi dependency) ---
import urllib.request as _req

token = ""
for line in (PROJECT / ".env").read_text().splitlines():
    if line.startswith("WEB_AUTH_TOKEN="):
        token = line.split("=", 1)[1].strip()
        break


def _gw_call(method, kwargs):
    body = json.dumps(kwargs).encode()
    req = _req.Request(
        GW + "/call/" + method,
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": token},
    )
    with _req.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    if data.get("error"):
        raise RuntimeError(data["error"])
    p = data.get("data", {})
    if isinstance(p, dict) and "columns" in p:
        return pd.DataFrame(p["rows"], columns=p["columns"])
    return pd.DataFrame(p) if isinstance(p, list) else pd.DataFrame()


def _rates(sym, tf, n):
    for i in range(3):
        try:
            df = _gw_call("rates", {"symbol": sym, "timeframe": tf, "count": n})
            if len(df) > 0:
                df["time"] = df["time"].astype(str)
                df = df.sort_values("time").reset_index(drop=True)
            return df
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"failed: {sym} {tf}")


# --- indicators (duplicated from research_v2 to avoid circular import) ---
def _ema(s, w):
    return s.ewm(span=w, adjust=False).mean()


def _atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def _bias(df, fast=50, slow=200):
    ef, es = _ema(df["close"], fast), _ema(df["close"], slow)
    t = pd.Series(0, index=df.index)
    t[ef > es] = 1
    t[ef < es] = -1
    rh = df["high"].rolling(20).max().shift(20)
    rl = df["low"].rolling(20).min().shift(20)
    bull = (t == 1) & (df["close"] > rh * 0.999)
    bear = (t == -1) & (df["close"] < rl * 1.001)
    b = pd.Series(0, index=df.index).mask(bull, 1).mask(bear, -1)
    b.index = df["time"].values
    return b


def _align_to_h1(bias_series, h1_times):
    shifted = bias_series.shift(1)
    shifted.index = bias_series.index
    aligned = shifted.reindex(h1_times, method="ffill")
    return aligned.fillna(0).reset_index(drop=True)


def _pip_size(sym):
    s = sym.upper().rstrip(".")
    if "JPY" in s:
        return 0.01
    return 0.0001


SPREAD_PIPS = {
    "EURUSD": 1.2, "GBPUSD": 1.6, "USDJPY": 1.4, "AUDUSD": 1.8,
    "NZDUSD": 2.2, "USDCAD": 2.0, "USDCHF": 1.8,
    "XAUUSD": 35.0, "XAGUSD": 3.0, "USOIL": 4.0,
    "BTCUSD": 90.0, "ETHUSD": 2.5,
    "CHFJPY": 2.0, "EURJPY": 1.5, "GBPJPY": 2.0, "NZDJPY": 2.0,
    "GBPCHF": 2.0, "NZDCAD": 2.0, "AUDNZD": 2.2, "EURAUD": 2.0, "EURCHF": 1.6,
}

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "CHFJPY", "EURJPY", "GBPJPY", "NZDJPY",
    "GBPCHF", "NZDCAD", "AUDNZD", "EURAUD", "EURCHF",
]

ENTRY_TFS = ["M1", "M5", "M15"]
GATE_COMBOS = {
    "none": [], "H4": ["H4"], "H4+H1": ["H4", "H1"], "H4+H6": ["H4", "H6"],
    "H4+H6+H1": ["H4", "H6", "H1"], "H6+H8": ["H6", "H8"],
    "D1+H4+H1": ["D1", "H4", "H1"], "D1+H4+H6": ["D1", "H4", "H6"],
}
SL_TP_RATIOS = [1.5, 2.0, 3.0]
SL_ATRS = [1.5, 2.0, 3.0]
EXITS = [(None, "full"), ({"r": 1.0, "frac": 0.5}, "partial1R")]
FILTERS = [
    (False, False, "none"), (True, False, "energy"),
    (False, True, "zone"), (True, True, "energy+zone"),
]


def _sim(h1, aligned_gates, sl_tp=2.0, use_energy=False, use_zone=False,
         pv=0.0001, start=250, sl_atr=2.0, partial=None, spread_pips=0.0):
    ef, es = _ema(h1["close"], 50), _ema(h1["close"], 200)
    a = _atr(h1)
    atr_avg = a.rolling(50).mean()
    trades, in_pos, ei = [], 0, 0
    p_taken, p_frac, p_r, pnl_taken = False, 0.0, 0.0, 0.0
    entry = sl = tp = 0.0

    for i in range(start, len(h1) - 1):
        p = h1["close"].iloc[i]
        if in_pos == 0:
            sig = 1 if ef.iloc[i] > es.iloc[i] else (-1 if ef.iloc[i] < es.iloc[i] else 0)
            if sig == 0:
                continue
            if any(g.iloc[i] != sig for g in aligned_gates.values()):
                continue
            if use_energy and pd.notna(atr_avg.iloc[i]) and atr_avg.iloc[i] > 0:
                if a.iloc[i] / atr_avg.iloc[i] < 1.5:
                    continue
            if use_zone:
                rh = h1["high"].rolling(20).max().iloc[i]
                rl = h1["low"].rolling(20).min().iloc[i]
                za = a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
                if sig == 1 and p > rl + 2 * za:
                    continue
                if sig == -1 and p < rh - 2 * za:
                    continue
            entry = p
            stop = max(sl_atr * a.iloc[i], 0.0001)
            sl, tp = entry - sig * stop, entry + sig * sl_tp * stop
            in_pos = sig
            ei = i
            p_taken, pnl_taken = False, 0.0
            if partial:
                p_frac, p_r = partial["frac"], partial["r"]
                p_level = entry + sig * p_r * stop
            else:
                p_frac = 0.0
        else:
            hi, lo = h1["high"].iloc[i], h1["low"].iloc[i]
            if partial and not p_taken:
                if (in_pos == 1 and hi >= p_level) or (in_pos == -1 and lo <= p_level):
                    pnl_taken = (p_level - entry) / pv * p_frac if in_pos == 1 else (entry - p_level) / pv * p_frac
                    p_taken = True
            exit_pnl = None
            if in_pos == 1:
                if lo <= sl:
                    exit_pnl = (sl - entry) / pv
                elif hi >= tp:
                    exit_pnl = (tp - entry) / pv
            else:
                if hi >= sl:
                    exit_pnl = (entry - sl) / pv
                elif lo <= tp:
                    exit_pnl = (entry - tp) / pv
            if exit_pnl is not None:
                remaining = 1.0 - (p_frac if partial else 0.0)
                trades.append({"pnl": exit_pnl * remaining + pnl_taken - spread_pips, "bars": i - ei})
                in_pos = 0
            if in_pos != 0 and i - ei > 200:
                pnl = (p - entry if in_pos == 1 else entry - p) / pv
                remaining = 1.0 - (p_frac if partial else 0.0)
                trades.append({"pnl": pnl * remaining + pnl_taken - spread_pips, "bars": i - ei})
                in_pos = 0
    return trades


def _stats(trades, bootstrap=False, n_boot=2000):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win%": 0, "expect": 0, "PF": 0, "sharpe": 0, "max_dd": 0,
                "pf_lo": None, "exp_lo": None}
    pnls = np.array([t["pnl"] for t in trades])
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    sharpe = float(pnls.mean() / pnls.std() * np.sqrt(252)) if pnls.std() > 0 else 0
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    max_dd = float((peak - eq).max()) if len(eq) else 0
    out = {
        "trades": n, "win%": round(len(wins) / n * 100, 1),
        "expect": round(float(pnls.mean()), 4),
        "PF": round(gw / gl, 2) if gl else (float("inf") if gw else 0),
        "sharpe": round(sharpe, 2), "max_dd": round(max_dd, 2),
        "pf_lo": None, "exp_lo": None,
    }
    if bootstrap and n >= 10:
        rng = np.random.default_rng(42)
        pf_s, ex_s = [], []
        for _ in range(n_boot):
            s = pnls[rng.integers(0, n, n)]
            w = s[s > 0].sum()
            l = abs(s[s <= 0].sum())
            if l > 0:
                pf_s.append(w / l)
            elif w > 0:
                pf_s.append(float("inf"))
            ex_s.append(s.mean())
        if pf_s:
            out["pf_lo"] = round(float(np.percentile(pf_s, 5)), 2)
            out["exp_lo"] = round(float(np.percentile(ex_s, 5)), 4)
    return out


# ---------------------------------------------------------------------------
# Agent singleton
# ---------------------------------------------------------------------------
class ResearchAgent:
    """Background research sweep with live progress tracking."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = {
            "running": False,
            "phase": "idle",       # idle / loading / sweep / validating / done / error
            "progress": 0,         # 0-100
            "current": "",         # human-readable status line
            "combos_total": 0,
            "combos_done": 0,
            "symbols_loaded": 0,
            "symbols_total": len(SYMBOLS),
            "elapsed_s": 0,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        self._results: dict | None = None  # last completed run

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def latest(self) -> dict | None:
        with self._lock:
            return self._results

    def start(self) -> bool:
        """Trigger a research sweep. Returns False if already running."""
        with self._lock:
            if self._state["running"]:
                return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run(self):
        t0 = time.time()
        self._set(running=True, phase="loading", progress=0, error=None,
                  started_at=datetime.datetime.utcnow().isoformat(), finished_at=None,
                  _t0=time.time())
        try:
            # --- Phase 1: load data ---
            data = {}
            for idx, sym in enumerate(SYMBOLS):
                self._set(current=f"Loading {sym} ({idx+1}/{len(SYMBOLS)})...",
                          progress=int((idx / len(SYMBOLS)) * 20))
                try:
                    m30 = _rates(sym, "M30", 7200)
                    if len(m30) < 1500:
                        logger.info("  %s: skip (M30=%d)", sym, len(m30))
                        continue
                    m30["time"] = pd.to_datetime(m30["time"])
                    h1 = (m30.set_index("time").resample("1h", label="left", closed="left")
                          .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                          .dropna().reset_index())
                    h1["time"] = h1["time"].astype(str)
                    if len(h1) < 500:
                        logger.info("  %s: skip (H1=%d)", sym, len(h1))
                        continue
                    h1_times = pd.Index(h1["time"].values)
                    frames = {"H1": h1, "M30": m30}
                    for tf, n in (("D1", 600), ("H4", 800), ("M15", 30000), ("M5", 30000), ("M1", 30000)):
                        try:
                            frames[tf] = _rates(sym, tf, n)
                        except Exception:
                            pass
                    for tf in ("H2", "H3", "H6", "H8", "H12"):
                        rule = {"H2": "2h", "H3": "3h", "H6": "6h", "H8": "8h", "H12": "12h"}[tf]
                        frames[tf] = (m30.set_index("time").resample(rule, label="left", closed="left")
                                     .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                                     .dropna().reset_index())
                        frames[tf]["time"] = frames[tf]["time"].astype(str)
                    aligned = {}
                    gate_start = {}
                    for tf, df in frames.items():
                        if tf == "H1":
                            continue
                        b = _bias(df)
                        a = _align_to_h1(b, h1_times)
                        aligned[tf] = a
                        first_ts = b.index[b != 0]
                        gate_start[tf] = first_ts[0] if len(first_ts) else None
                    data[sym] = {"h1": h1, "aligned": aligned, "gate_start": gate_start}
                    logger.info("  %s: ok (%d H1 bars)", sym, len(h1))
                except Exception as e:
                    logger.warning("  %s: ERROR %s", sym, e)

            self._set(symbols_loaded=len(data), progress=20, phase="sweep")

            if not data:
                self._set(phase="error", error="No symbols loaded", running=False)
                return

            # --- Phase 2: sweep ---
            total = (len(ENTRY_TFS) * len(GATE_COMBOS) * len(SL_TP_RATIOS) * len(SL_ATRS)
                     * len(EXITS) * len(FILTERS))
            self._set(combos_total=total, combos_done=0)
            results = []
            done = 0
            for etf in ENTRY_TFS:
                for gn, gate_tfs in GATE_COMBOS.items():
                    for st in SL_TP_RATIOS:
                        for sa in SL_ATRS:
                            for partial, ex_name in EXITS:
                                for ue, uz, fn in FILTERS:
                                    at = []
                                    for sym, d in data.items():
                                        if etf not in d["aligned"]:
                                            continue
                                        gates = {etf: d["aligned"][etf]}
                                        for tf in gate_tfs:
                                            if tf in d["aligned"]:
                                                gates[tf] = d["aligned"][tf]
                                        h1_times_vals = d["h1"]["time"].values
                                        starts = [d["gate_start"].get(tf) for tf in gates]
                                        valid_starts = [h1_times_vals.searchsorted(s) for s in starts
                                                        if s is not None and h1_times_vals.searchsorted(s) < len(h1_times_vals)]
                                        start_i = max(valid_starts) + 250 if valid_starts else 250
                                        at.extend(_sim(d["h1"], gates, st, ue, uz, _pip_size(sym),
                                                       start=start_i, sl_atr=sa, partial=partial,
                                                       spread_pips=SPREAD_PIPS.get(sym, 1.5)))
                                    s = _stats(at)
                                    results.append({"entry_tf": etf, "gates": gn, "sl_tp": st,
                                                    "sl_atr": sa, "exit": ex_name, "filters": fn, **s})
                                    done += 1
                                    if done % 50 == 0 or done == total:
                                        pct = 20 + int((done / total) * 60)
                                        self._set(combos_done=done, progress=pct,
                                                  current=f"Sweep {done}/{total} ({etf} {gn} {st}R {sa}A {ex_name} {fn})",
                                                  elapsed_s=round(time.time() - self._state.get("_t0", t0), 1))
                # yield between entry TFs
                self._set(combos_done=done, progress=20 + int((done / total) * 60))

            ranked = sorted(results, key=lambda r: r["expect"] if r["trades"] >= 10 else -999, reverse=True)

            # save research
            RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
            today = datetime.date.today().isoformat()
            rp = RESEARCH_DIR / f"research_{today}.json"
            with open(rp, "w") as f:
                json.dump({"date": today, "symbols": SYMBOLS, "results": ranked}, f, indent=2)

            # --- Phase 3: validate top 8 ---
            self._set(phase="validating", progress=80, current="Walk-forward validation...")
            top = [r for r in ranked if r["trades"] >= 10][:8]
            validated = []
            for vi, r in enumerate(top):
                etf, gn, st, fn = r["entry_tf"], r["gates"], r["sl_tp"], r["filters"]
                sa, ex_name = r.get("sl_atr", 2.0), r.get("exit", "full")
                ue, uz = "energy" in fn, "zone" in fn
                partial = {"r": 1.0, "frac": 0.5} if ex_name == "partial1R" else None
                gate_tfs = GATE_COMBOS[gn]
                self._set(current=f"Validating {vi+1}/{len(top)}: {etf} {gn} {st}R...")
                tt, ts = [], []
                for sym, d in data.items():
                    if etf not in d["aligned"]:
                        continue
                    h1 = d["h1"]
                    h1_times_vals = h1["time"].values
                    gates_full = {etf: d["aligned"][etf]}
                    for tf in gate_tfs:
                        if tf in d["aligned"]:
                            gates_full[tf] = d["aligned"][tf]
                    starts = [d["gate_start"].get(tf) for tf in gates_full]
                    valid_starts = [h1_times_vals.searchsorted(s) for s in starts
                                    if s is not None and h1_times_vals.searchsorted(s) < len(h1_times_vals)]
                    start_i = max(valid_starts) + 250 if valid_starts else 250
                    split = int(len(h1) * 0.7)
                    tr_lo = min(start_i, split)
                    h1_tr = h1.iloc[tr_lo:split].reset_index(drop=True)
                    te_lo = max(split, start_i)
                    h1_te = h1.iloc[te_lo:].reset_index(drop=True) if te_lo < len(h1) else h1.iloc[0:0]
                    g_tr = {tf: s.iloc[tr_lo:split].reset_index(drop=True) for tf, s in gates_full.items()}
                    g_te = {tf: s.iloc[te_lo:].reset_index(drop=True) for tf, s in gates_full.items()}
                    pv = _pip_size(sym)
                    sp = SPREAD_PIPS.get(sym, 1.5)
                    tt.extend(_sim(h1_tr, g_tr, st, ue, uz, pv, sl_atr=sa, partial=partial, spread_pips=sp))
                    ts.extend(_sim(h1_te, g_te, st, ue, uz, pv, sl_atr=sa, partial=partial, spread_pips=sp))
                tr_s, te_s = _stats(tt, bootstrap=True), _stats(ts, bootstrap=True)
                passes = (tr_s["trades"] >= 10 and te_s["trades"] >= 3 and te_s["expect"] > 0
                          and te_s["PF"] >= 0.8 and te_s["sharpe"] > 0.3
                          and (te_s["pf_lo"] is None or te_s["pf_lo"] >= 0.9))
                validated.append({**r, "train": tr_s, "test": te_s, "passes": passes})
                self._set(progress=80 + int(((vi + 1) / len(top)) * 15))

            VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
            vp = VALIDATED_DIR / f"validated_{today}.json"
            with open(vp, "w") as f:
                json.dump({"date": today, "results": validated}, f, indent=2)

            passed = [v for v in validated if v["passes"]]
            elapsed = round(time.time() - t0, 1)
            self._set(
                phase="done", progress=100, running=False,
                current=f"Done — {len(passed)}/{len(validated)} passed, {len(ranked)} combos in {elapsed}s",
                finished_at=datetime.datetime.utcnow().isoformat(), elapsed_s=elapsed,
            )
            self._results = {
                "date": today,
                "symbols": SYMBOLS,
                "combos": len(ranked),
                "top": ranked[:20],
                "validated": validated,
                "passed_count": len(passed),
                "elapsed_s": elapsed,
            }
            logger.info("Research done: %d combos, %d/%d passed, %.1fs", len(ranked), len(passed), len(validated), elapsed)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set(phase="error", error=str(e), running=False,
                      finished_at=datetime.datetime.utcnow().isoformat())


# singleton
agent = ResearchAgent()


# ---------------------------------------------------------------------------
# Subprocess-compatible function
# ---------------------------------------------------------------------------
def run_research(payload: dict) -> dict:
    """Run a full research sweep in a subprocess.

    Reuses the proven ResearchAgent._run logic and mirrors its state
    to the subprocess status file so the dashboard can track progress.
    """
    from subprocess_agent import _write_status, _finish

    a = ResearchAgent()

    # Mirror agent._set() calls to the subprocess status file
    orig_set = a._set

    def mirrored_set(**kw):
        orig_set(**kw)
        with a._lock:
            state = dict(a._state)
        # strip internal keys
        state.pop("_t0", None)
        # subprocess_agent._write_status defaults running=True; done/error
        # phases set running=False explicitly in the agent state
        _write_status("research", **state)

    a._set = mirrored_set

    a._run()  # runs the full proven sweep: load -> sweep -> validate

    if a._state.get("phase") == "error":
        _write_status("research", phase="error", running=False,
                      error=a._state.get("error"))
        return {"error": a._state.get("error")}

    r = a.latest() or {}
    # Normalise keys for the pipeline (which expects `top` + `passed_count`)
    top = r.get("top", [])
    result = {
        "top": top,
        "validated": r.get("validated", []),
        "passed_count": r.get("passed_count", 0),
        "combos": r.get("combos", 0),
        "total_combos": r.get("combos", 0),
        "total_results": r.get("combos", 0),
        "symbols": r.get("symbols", SYMBOLS),
        "elapsed_s": r.get("elapsed_s", 0),
    }
    return result
