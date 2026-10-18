"""Backtest Agents — Walk-Forward, Sensitivity, Sweep, Correlation, Monte Carlo.

All follow the same singleton pattern as research_agent: start(), status(), latest().
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("backtest_agents")

PROJECT = Path(r"E:\Project\Mt5 Ea")
RESULTS_DIR = PROJECT / "mt5_mcp" / "backtest" / "results"
MONTH_DIR = str(RESULTS_DIR / "month")
FORWARD_DIR = str(RESULTS_DIR / "forward")


def _load_frames(symbol, tfs, bars, **kw):
    from backtest import load_frames
    return load_frames(symbol, tfs, bars, **kw)


def _get_info(symbol):
    from mt5_mcp.client import get_client
    try:
        return get_client().symbol_info(symbol)
    except Exception:
        return {}


def _bt_params_from(payload):
    from backtest import BacktestParams
    symbol = (payload.get("symbol") or "EURUSD").upper().strip()
    trend_tf = payload.get("trend_tf", "H4")
    confirm_tf = payload.get("confirm_tf", "H1")
    zone_tfs = payload.get("zone_tfs", "H1")
    if isinstance(zone_tfs, str):
        zone_tfs = [t.strip().upper() for t in zone_tfs.split(",") if t.strip()]
    bars = int(payload.get("bars") or 3000)
    risk = float(payload.get("risk") or 0.5)
    min_zone = float(payload.get("min_zone_score") or 40.0)
    min_rr = float(payload.get("min_rr") or 2.0)
    min_conf = float(payload.get("min_confidence_percent") or 55.0)
    min_trade = float(payload.get("min_trade_score") or 50.0)
    tp_min = float(payload.get("tp_min") or 0.0)
    sl_min = float(payload.get("sl_min") or 0.0)
    slippage = float(payload.get("slippage_points") or 1.0)
    commission = float(payload.get("commission_per_lot") or 7.0)
    spread = float(payload.get("spread_points") or 15.0)
    trail = payload.get("use_trailing_stop", False)
    trail_arm = float(payload.get("trailing_arm_r") or 2.0)
    trail_atr = float(payload.get("trailing_atr_mult") or 2.0)
    partial = payload.get("use_partial_tp", False)
    partial_r = float(payload.get("partial_tp_r") or 2.0)
    partial_pct = float(payload.get("partial_tp_pct") or 0.5)
    params = BacktestParams(
        symbol=symbol, bars=bars, risk_percent=risk,
        trend_timeframe=trend_tf, confirmation_timeframe=confirm_tf,
        zone_timeframes=zone_tfs, min_zone_score=min_zone,
        min_rr=min_rr, min_confidence_percent=min_conf,
        min_trade_score=min_trade, tp_min=tp_min, sl_min=sl_min,
        slippage_points=slippage, commission_per_lot=commission,
        spread_points=spread, use_trailing_stop=trail,
        trailing_arm_r=trail_arm, trailing_atr_mult=trail_atr,
        use_partial_tp=partial, partial_tp_r=partial_r,
        partial_tp_pct=partial_pct,
    )
    since = payload.get("since")
    return params, since


class _BaseAgent:
    """Shared singleton pattern for background agents."""

    def __init__(self, name: str):
        self.name = name
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False, "phase": "idle", "progress": 0,
            "current": "", "elapsed_s": 0, "error": None,
            "started_at": None, "finished_at": None,
        }
        self._results: dict | None = None

    def status(self) -> dict:
        with self._lock:
            s = dict(self._state)
            if s.get("_t0"):
                s["elapsed_s"] = round(time.time() - s["_t0"], 1)
            return s

    def latest(self) -> dict | None:
        with self._lock:
            return self._results

    def start(self, **kwargs) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
        self._thread = threading.Thread(target=self._run_wrapper, args=(kwargs,), daemon=True)
        self._thread.start()
        return True

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run_wrapper(self, kwargs):
        self._set(running=True, phase="starting", progress=0, error=None,
                  started_at=datetime.datetime.utcnow().isoformat(),
                  finished_at=None, _t0=time.time())
        try:
            self._run(kwargs)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set(phase="error", error=str(e), running=False,
                      finished_at=datetime.datetime.utcnow().isoformat())
        finally:
            with self._lock:
                self._state.pop("_t0", None)

    def _run(self, kwargs):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. WALK-FORWARD AGENT
# ---------------------------------------------------------------------------
class WalkForwardAgent(_BaseAgent):
    def __init__(self):
        super().__init__("walk_forward")

    def _run(self, kwargs):
        from walk_forward import WalkForwardEngine, WalkForwardParams
        from dataclasses import asdict

        payload = kwargs.get("payload", {})
        symbol = (payload.get("symbol") or "EURUSD").upper().strip()
        train_bars = int(payload.get("train_bars") or 2000)
        test_bars = int(payload.get("test_bars") or 500)
        step_bars = int(payload.get("step_bars") or 500)
        trend_tf = payload.get("trend_tf", "H4")
        zone_tfs = payload.get("zone_tfs", "H1,H4")
        confirm_tf = payload.get("confirm_tf", "M5")
        if isinstance(zone_tfs, str):
            zone_tfs = [t.strip().upper() for t in zone_tfs.split(",") if t.strip()]
        min_zone_scores = payload.get("min_zone_scores", [40.0, 70.0])
        min_rrs = payload.get("min_rrs", [2.0, 3.0])
        risk_pct = float(payload.get("risk") or 0.5)
        slippage = float(payload.get("slippage_points") or 1.0)
        commission = float(payload.get("commission_per_lot") or 7.0)
        spread = float(payload.get("spread_points") or 15.0)
        bars_total = int(payload.get("bars_total") or 10000)

        self._set(phase="loading", current=f"Loading {symbol} data...", progress=5)

        wf_params = WalkForwardParams(
            symbol=symbol, train_bars=train_bars, test_bars=test_bars,
            step_bars=step_bars, trend_timeframe=trend_tf,
            zone_timeframes=zone_tfs, confirmation_timeframe=confirm_tf,
            min_zone_scores=min_zone_scores,
            min_rrs=min_rrs, risk_percent=risk_pct,
            slippage_points=slippage, commission_per_lot=commission,
            spread_points=spread, bars_total=bars_total,
        )

        # Walk-forward engine has internal steps — wrap for progress
        self._set(phase="sweep", current=f"Running walk-forward on {symbol}...", progress=10)
        engine = WalkForwardEngine(wf_params)
        result = engine.run()
        out = asdict(result)
        out["elapsed_s"] = round(time.time() - self._state.get("_t0", time.time()), 1)

        self._results = out
        self._set(phase="done", progress=100, running=False,
                  current=f"Done — {symbol} walk-forward complete",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 2. SENSITIVITY AGENT
# ---------------------------------------------------------------------------
class SensitivityAgent(_BaseAgent):
    def __init__(self):
        super().__init__("sensitivity")

    def _run(self, kwargs):
        from backtest import sensitivity_analysis

        payload = kwargs.get("payload", {})
        params, _ = _bt_params_from(payload)
        sweep_params = payload.get("sweep_params") or [
            "min_zone_score", "min_trade_score", "min_confidence_percent",
            "tp_min", "sl_min", "min_rr",
            "trailing_arm_r", "trailing_atr_mult", "partial_tp_r",
        ]
        pct_range = float(payload.get("pct_range") or 0.20)
        steps = int(payload.get("steps") or 5)

        self._set(phase="loading", current=f"Loading {params.symbol} frames...", progress=5)
        frames = _load_frames(
            params.symbol,
            sorted(set([params.trend_timeframe, params.confirmation_timeframe] + params.zone_timeframes)),
            params.bars, use_cache=True, allow_mt5=True, ttl=900,
        )
        info = _get_info(params.symbol)

        total_steps = len(sweep_params) * steps
        self._set(phase="sweep", current=f"Sweeping {len(sweep_params)} params ({steps} steps each)...",
                  progress=10, combos_total=total_steps, combos_done=0)

        # sensitivity_analysis runs all params — we can't hook into its internals
        # easily, so just run it and report
        result = sensitivity_analysis(params, frames, info, sweep_params, pct_range, steps)
        result["elapsed_s"] = round(time.time() - self._state.get("_t0", time.time()), 1)

        self._results = result
        self._set(phase="done", progress=100, running=False, combos_done=total_steps,
                  current=f"Done — {params.symbol} sensitivity: {len(result.get('params', []))} params analyzed",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 3. SWEEP AGENT (trailing/partial combos)
# ---------------------------------------------------------------------------
class SweepAgent(_BaseAgent):
    def __init__(self):
        super().__init__("sweep")

    def _run(self, kwargs):
        from backtest import Backtester, BacktestParams, sensitivity_variants

        payload = kwargs.get("payload", {})
        symbol = (payload.get("symbol") or "EURUSD").upper().strip()
        bars = int(payload.get("bars") or 3000)

        self._set(phase="loading", current=f"Loading {symbol} frames...", progress=5)
        variants = sensitivity_variants(symbol, bars=bars)
        frames = _load_frames(symbol, ["H1", "H4", "D1"], bars, use_cache=True, allow_mt5=True, ttl=900)
        info = _get_info(symbol)

        total = len(variants)
        self._set(phase="sweep", current=f"Sweeping {total} trailing/partial combos...",
                  progress=10, combos_total=total, combos_done=0)

        results = []
        for i, p in enumerate(variants):
            bt = Backtester(p, frames, info)
            r = bt.run()
            results.append({
                "trail": p.use_trailing_stop,
                "trail_arm": p.trailing_arm_r,
                "trail_atr": p.trailing_atr_mult,
                "partial": p.use_partial_tp,
                "partial_r": p.partial_tp_r,
                "partial_pct": p.partial_tp_pct,
                "pf": r.profit_factor,
                "wr": r.win_rate,
                "net_pnl": r.net_pnl,
                "trades": r.n_trades,
                "max_dd": r.max_drawdown_pct,
                "expectancy": r.expectancy,
                "sharpe": r.sharpe_ratio,
                "partial_count": r.partial_tp_count,
                "trail_count": r.trailing_stop_count,
            })
            if (i + 1) % 5 == 0 or i + 1 == total:
                pct = 10 + int(((i + 1) / total) * 85)
                self._set(combos_done=i + 1, progress=pct,
                          current=f"Sweep {i+1}/{total} ({symbol})")

        results.sort(key=lambda x: x["pf"], reverse=True)
        self._results = {
            "symbol": symbol, "combos": len(results),
            "results": results,
            "elapsed_s": round(time.time() - self._state.get("_t0", time.time()), 1),
        }
        self._set(phase="done", progress=100, running=False,
                  current=f"Done — {symbol}: {len(results)} combos swept",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 4. CORRELATION AGENT
# ---------------------------------------------------------------------------
class CorrelationAgent(_BaseAgent):
    def __init__(self):
        super().__init__("correlation")

    def _run(self, kwargs):
        from backtest import Backtester, BacktestParams, correlation_overlay

        payload = kwargs.get("payload", {})
        symbols = [s.strip().upper() for s in str(payload.get("symbols", "")).split(",") if s.strip()]
        if len(symbols) < 2:
            self._set(phase="error", error="at least 2 symbols required", running=False)
            return
        bars = int(payload.get("bars") or 3000)

        total = len(symbols)
        self._set(phase="loading", current=f"Loading {total} symbols...", progress=5,
                  combos_total=total, combos_done=0)

        all_results = []
        for i, sym in enumerate(symbols):
            self._set(current=f"Backtesting {sym} ({i+1}/{total})...",
                      progress=5 + int(((i) / total) * 80),
                      combos_done=i)
            params = BacktestParams(symbol=sym, bars=bars)
            frames = _load_frames(sym, ["H1", "H4"], bars, use_cache=True, allow_mt5=True, ttl=900)
            info = _get_info(sym)
            bt = Backtester(params, frames, info)
            r = bt.run()
            all_results.append(r)

        combined = correlation_overlay(all_results, symbols)
        combined["elapsed_s"] = round(time.time() - self._state.get("_t0", time.time()), 1)
        self._results = combined
        self._set(phase="done", progress=100, running=False, combos_done=total,
                  current=f"Done — {total} symbols correlated",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 5. MONTE CARLO AGENT
# ---------------------------------------------------------------------------
class MonteCarloAgent(_BaseAgent):
    def __init__(self):
        super().__init__("monte_carlo")

    def _run(self, kwargs):
        payload = kwargs.get("payload", {})
        symbol = payload.get("symbol", "")
        simulations = int(payload.get("simulations") or 1000)

        self._set(phase="loading", current="Loading journal trades...", progress=10)
        from mt5_mcp.memory.trade_journal import get_journal
        journal = get_journal()
        trades = journal.list_trades(limit=500)
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]
        if not trades:
            self._set(phase="error", error="No trades found", running=False)
            return

        pnls = [t.get("pnl", 0) for t in trades if t.get("pnl") is not None]
        if not pnls:
            self._set(phase="error", error="No PnL data", running=False)
            return

        self._set(phase="simulating", current=f"Running {simulations} simulations...",
                  progress=20)

        # Run simulations
        import random
        results = []
        for i in range(simulations):
            sample = [pnls[j % len(pnls)] for j in range(len(pnls))]
            random.shuffle(sample)
            cumulative = 0
            curve = [10000]
            for p in sample:
                cumulative += p
                curve.append(10000 + cumulative)
            results.append(curve)
            if (i + 1) % 100 == 0:
                self._set(progress=20 + int(((i + 1) / simulations) * 70),
                          current=f"Simulating {i+1}/{simulations}...")

        finals = [r[-1] for r in results]
        positives = sum(1 for f in finals if f > 10000)
        blown = sum(1 for f in finals if f < 5000)

        self._results = {
            "simulations": len(results),
            "avg_return": round(sum(finals) / len(finals) - 10000, 2),
            "p_positive": round(positives / len(finals) * 100, 1),
            "p_blown": round(blown / len(finals) * 100, 1),
            "worst_case": round(min(finals) - 10000, 2),
            "best_case": round(max(finals) - 10000, 2),
            "curves": [r[:100] for r in results[:200]],
            "finals": sorted(finals),
            "elapsed_s": round(time.time() - self._state.get("_t0", time.time()), 1),
        }
        self._set(phase="done", progress=100, running=False,
                  current=f"Done — {simulations} sims, {round(positives/len(finals)*100,1)}% pass",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 6. ML RANKER AGENT
# ---------------------------------------------------------------------------
class MLRankerAgent(_BaseAgent):
    def __init__(self):
        super().__init__("ml_ranker")

    def _run(self, kwargs):
        from mt5_mcp.ml_ranker import (
            load_all_trades, build_dataset, time_ordered_evaluate,
            fit_and_save, MODEL_PATH,
        )

        self._set(phase="loading", current="Loading backtest results...", progress=10)
        results_dir = PROJECT / "mt5_mcp" / "backtest" / "results"
        if not results_dir.exists():
            self._set(phase="error", error="no backtest results directory", running=False)
            return

        t0 = time.time()
        trades = load_all_trades(results_dir, verbose=False)
        if not trades:
            self._set(phase="error", error="no backtest trades found", running=False)
            return

        self._set(phase="building", current=f"Building dataset from {len(trades)} trades...",
                  progress=30)
        X, y, meta = build_dataset(trades)
        if len(set(y)) < 2 or len(y) < 20:
            self._set(phase="error",
                      error=f"not enough data: {len(y)} usable trades, {len(set(y))} classes",
                      running=False)
            return

        self._set(phase="training", current=f"Time-ordered CV on {len(y)} samples...",
                  progress=60)
        metrics = time_ordered_evaluate(X, y, n_folds=5)

        self._set(phase="saving", current="Saving model...", progress=90)
        payload = fit_and_save(X, y, MODEL_PATH, metrics=metrics)
        elapsed = round(time.time() - t0, 1)

        self._results = {
            "ok": True, "elapsed_s": elapsed,
            "n_trades": len(y),
            "base_win_rate": metrics["base_win_rate"],
            "time_cv_accuracy": metrics["time_cv_accuracy"],
            "time_cv_auc": metrics["time_cv_auc"],
            "high_conf_win_rate": metrics["high_conf_win_rate"],
            "high_conf_n": metrics["high_conf_n"],
            "features": payload["features"],
            "weights": payload["weights"],
            "trained_at": payload["trained_at"],
            "feature_importance": payload.get("feature_importance", {}),
        }
        self._set(phase="done", progress=100, running=False,
                  current=f"Done — {len(y)} trades, AUC {metrics['time_cv_auc']:.3f}",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 7. EXPORT AGENT (Colab data)
# ---------------------------------------------------------------------------
class ExportAgent(_BaseAgent):
    def __init__(self):
        super().__init__("export")

    def _run(self, kwargs):
        payload = kwargs.get("payload", {})
        symbols = payload.get("symbols") or [
            "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        ]
        tfs = payload.get("timeframes") or [
            "M1", "M5", "M15", "M30", "H1", "H4", "D1", "H12",
        ]
        count = int(payload.get("count") or 5000)
        out_dir = Path(payload.get("out_dir") or str(PROJECT / "data" / "agent_export"))
        out_dir.mkdir(parents=True, exist_ok=True)

        total = len(symbols) * len(tfs)
        self._set(phase="exporting", current=f"Exporting {len(symbols)} symbols × {len(tfs)} TFs...",
                  progress=0, combos_total=total, combos_done=0)

        import urllib.request as _req
        token = ""
        for line in (PROJECT / ".env").read_text().splitlines():
            if line.startswith("WEB_AUTH_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break

        GW = "http://127.0.0.1:8060"
        done = 0
        errors = []
        for sym in symbols:
            for tf in tfs:
                self._set(current=f"Exporting {sym} {tf}...",
                          progress=int((done / total) * 95),
                          combos_done=done)
                try:
                    body = json.dumps({"symbol": sym, "timeframe": tf, "count": count}).encode()
                    req = _req.Request(
                        GW + "/call/rates", data=body,
                        headers={"Content-Type": "application/json", "X-Auth-Token": token},
                    )
                    with _req.urlopen(req, timeout=300) as r:
                        data = json.loads(r.read().decode())
                    p = data.get("data", {})
                    if isinstance(p, dict) and "columns" in p:
                        df = pd.DataFrame(p["rows"], columns=p["columns"])
                    elif isinstance(p, list):
                        df = pd.DataFrame(p)
                    else:
                        df = pd.DataFrame()
                    if len(df) > 0:
                        fp = out_dir / f"{sym}_{tf}.parquet"
                        df.to_parquet(str(fp), index=False)
                except Exception as e:
                    errors.append(f"{sym}/{tf}: {e}")
                done += 1

        self._results = {
            "symbols": symbols, "timeframes": tfs,
            "files": len(list(out_dir.glob("*.parquet"))),
            "out_dir": str(out_dir),
            "errors": errors[:20],
            "elapsed_s": round(time.time() - self._state.get("_t0", time.time()), 1),
        }
        self._set(phase="done", progress=100, running=False,
                  current=f"Done — {len(list(out_dir.glob('*.parquet')))} files exported",
                  finished_at=datetime.datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
walk_forward_agent = WalkForwardAgent()
sensitivity_agent = SensitivityAgent()
sweep_agent = SweepAgent()
correlation_agent = CorrelationAgent()
monte_carlo_agent = MonteCarloAgent()
ml_ranker_agent = MLRankerAgent()
export_agent = ExportAgent()


# ---------------------------------------------------------------------------
# Subprocess-compatible functions (called by subprocess_agent_runner.py)
# ---------------------------------------------------------------------------
def _rep(name, **kw):
    from subprocess_agent import _write_status
    _write_status(name, **kw)


def run_sensitivity(payload: dict) -> dict:
    name = "sensitivity"
    _rep(name, phase="running", progress=10, current="Loading frames...")
    sym = payload.get("symbol", "EURUSD")
    bars = int(payload.get("bars") or 10000)
    tf = payload.get("timeframe", "M15")
    n = int(payload.get("sensitivity_range") or 10)

    results = []
    total = n * 7
    done = 0
    for i, risk in enumerate([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]):
        for j, tp in enumerate([x / 10.0 for x in range(10, 10 + n)]):
            _rep(name, progress=10 + int((done / total) * 80),
                 current=f"TP={tp:.1f} SL_mult={risk:.1f}")
            try:
                from mt5_mcp.client import get_client
                c = get_client("PROP")
                df = c.rates(sym, tf, bars)
                from backtest import backtest_mtf
                stats = backtest_mtf(df, risk, tp, tf)
                results.append({"risk": risk, "tp": tp, **stats})
            except Exception as e:
                results.append({"risk": risk, "tp": tp, "error": str(e)})
            done += 1

    results.sort(key=lambda x: x.get("expectancy", 0), reverse=True)
    return {"results": results[:20], "best": results[0] if results else None}


def run_sweep(payload: dict) -> dict:
    name = "sweep"
    _rep(name, phase="running", progress=10, current="Starting sweep...")
    symbols = payload.get("symbols", ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"])
    tfs = payload.get("timeframes", ["M15", "M5", "M30"])
    results = []
    total = len(symbols) * len(tfs)
    done = 0
    for sym in symbols:
        for tf in tfs:
            _rep(name, progress=10 + int((done / total) * 80),
                 current=f"{sym} {tf}")
            try:
                from mt5_mcp.client import get_client
                c = get_client("PROP")
                df = c.rates(sym, tf, 10000)
                from backtest import backtest_mtf
                stats = backtest_mtf(df, 3.0, 1.5, tf)
                results.append({"symbol": sym, "timeframe": tf, **stats})
            except Exception as e:
                results.append({"symbol": sym, "timeframe": tf, "error": str(e)})
            done += 1

    results.sort(key=lambda x: x.get("expectancy", 0), reverse=True)
    return {"results": results, "total": total}


def run_correlation(payload: dict) -> dict:
    name = "correlation"
    _rep(name, phase="running", progress=10, current="Loading data...")
    symbols = payload.get("symbols", ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"])
    try:
        from mt5_mcp.client import get_client
        c = get_client("PROP")
        frames = {}
        for sym in symbols:
            frames[sym] = c.rates(sym, "H1", 5000)
            _rep(name, progress=10 + int((len(frames) / len(symbols)) * 40),
                 current=f"Loaded {sym}")

        import pandas as pd
        closes = pd.DataFrame({sym: f["close"] for sym, f in frames.items() if len(f) > 100})
        corr = closes.corr().to_dict()

        groups = {}
        for s1 in corr:
            for s2 in corr[s1]:
                if s1 != s2 and abs(corr[s1][s2]) > 0.7:
                    groups.setdefault(s1, []).append(s2)

        return {"correlation": corr, "groups": groups, "symbols": list(closes.columns)}
    except Exception as e:
        return {"error": str(e)}


def run_monte_carlo(payload: dict) -> dict:
    name = "monte-carlo"
    _rep(name, phase="running", progress=10, current="Loading journal...")
    simulations = int(payload.get("simulations") or 1000)
    symbol = payload.get("symbol", "")

    from mt5_mcp.memory.trade_journal import get_journal
    journal = get_journal()
    trades = journal.list_trades(limit=500)
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    pnls = [t.get("pnl", 0) for t in trades if t.get("pnl") is not None]
    if not pnls:
        return {"error": "No trades"}

    import random
    results = []
    for i in range(simulations):
        sample = [pnls[j % len(pnls)] for j in range(len(pnls))]
        random.shuffle(sample)
        cumulative = 0
        curve = [10000]
        for p in sample:
            cumulative += p
            curve.append(10000 + cumulative)
        results.append(curve)
        if (i + 1) % 100 == 0:
            _rep(name, progress=10 + int(((i + 1) / simulations) * 80),
                 current=f"Simulating {i+1}/{simulations}")

    p_positive = sum(1 for r in results if r[-1] > 10000) / len(results) * 100
    p_blown = sum(1 for r in results if min(r) < 9000) / len(results) * 100
    avg_final = sum(r[-1] for r in results) / len(results)

    return {"p_positive": round(p_positive, 1), "p_blown": round(p_blown, 1),
            "avg_final": round(avg_final, 2), "simulations": simulations}


def run_ml_ranker(payload: dict) -> dict:
    name = "ml-rank"
    _rep(name, phase="loading", progress=10, current="Loading backtest results...")
    try:
        from mt5_mcp.ml_ranker import (
            load_all_trades, build_dataset, time_ordered_evaluate,
            fit_and_save, MODEL_PATH,
        )
        results_dir = Path(r"E:\Project\Mt5 Ea\mt5_mcp\backtest\results")
        if not results_dir.exists():
            return {"error": "no backtest results directory"}

        trades = load_all_trades(results_dir, verbose=False)
        if not trades:
            return {"error": "no backtest trades found"}

        _rep(name, phase="building", progress=30,
             current=f"Building dataset from {len(trades)} trades...")
        X, y, meta = build_dataset(trades)
        if len(set(y)) < 2 or len(y) < 20:
            return {"error": f"not enough data: {len(y)} usable trades, {len(set(y))} classes"}

        _rep(name, phase="training", progress=60,
             current=f"Time-ordered CV on {len(y)} samples...")
        metrics = time_ordered_evaluate(X, y, n_folds=5)

        _rep(name, phase="saving", progress=90, current="Saving model...")
        model_payload = fit_and_save(X, y, MODEL_PATH, metrics=metrics)

        return {"ok": True, "n_trades": len(y),
                "base_win_rate": metrics["base_win_rate"],
                "time_cv_accuracy": metrics["time_cv_accuracy"],
                "time_cv_auc": metrics["time_cv_auc"],
                "high_conf_win_rate": metrics["high_conf_win_rate"],
                "high_conf_n": metrics["high_conf_n"],
                "features": model_payload["features"],
                "weights": model_payload["weights"],
                "trained_at": model_payload["trained_at"],
                "feature_importance": model_payload.get("feature_importance", {})}
    except Exception as e:
        return {"error": str(e)}


def run_export(payload: dict) -> dict:
    name = "export"
    _rep(name, phase="running", progress=10, current="Exporting data...")
    try:
        from mt5_mcp.client import get_client
        c = get_client("PROP")
        symbols = payload.get("symbols", ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"])
        exported = []
        for i, sym in enumerate(symbols):
            _rep(name, progress=10 + int((i / len(symbols)) * 70), current=f"Exporting {sym}")
            df = c.rates(sym, "M15", 5000)
            out_dir = Path(r"E:\Project\Mt5 Ea\data\all_export")
            out_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out_dir / f"{sym}_M15.parquet", index=False)
            exported.append(sym)

        return {"exported": exported, "count": len(exported)}
    except Exception as e:
        return {"error": str(e)}


def run_walk_forward(payload: dict) -> dict:
    """Run walk-forward validation using the proven WalkForwardEngine."""
    name = "walk-forward"
    _rep(name, phase="loading", progress=5, current="Loading data...")
    try:
        from walk_forward import WalkForwardEngine, WalkForwardParams
        from dataclasses import asdict
        import time as _time
        t0 = _time.time()

        symbol = (payload.get("symbol") or "EURUSD").upper().strip()
        train_bars = int(payload.get("train_bars") or 2000)
        test_bars = int(payload.get("test_bars") or 500)
        step_bars = int(payload.get("step_bars") or 500)
        trend_tf = payload.get("trend_tf", "H4")
        zone_tfs = payload.get("zone_tfs", "H1,H4")
        confirm_tf = payload.get("confirm_tf", "M5")
        if isinstance(zone_tfs, str):
            zone_tfs = [t.strip().upper() for t in zone_tfs.split(",") if t.strip()]
        min_zone_scores = payload.get("min_zone_scores", [40.0, 70.0])
        min_rrs = payload.get("min_rrs", [2.0, 3.0])
        risk_pct = float(payload.get("risk") or 0.5)
        slippage = float(payload.get("slippage_points") or 1.0)
        commission = float(payload.get("commission_per_lot") or 7.0)
        spread = float(payload.get("spread_points") or 15.0)
        bars_total = int(payload.get("bars_total") or 10000)

        _rep(name, phase="sweep", progress=10,
             current=f"Running walk-forward on {symbol}...")
        wf_params = WalkForwardParams(
            symbol=symbol, train_bars=train_bars, test_bars=test_bars,
            step_bars=step_bars, trend_timeframe=trend_tf,
            zone_timeframes=zone_tfs, confirmation_timeframe=confirm_tf,
            min_zone_scores=min_zone_scores,
            min_rrs=min_rrs, risk_percent=risk_pct,
            slippage_points=slippage, commission_per_lot=commission,
            spread_points=spread, bars_total=bars_total,
        )
        engine = WalkForwardEngine(wf_params)
        result = engine.run()
        out = asdict(result)
        out["elapsed_s"] = round(_time.time() - t0, 1)
        out["ok"] = True
        return out
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
