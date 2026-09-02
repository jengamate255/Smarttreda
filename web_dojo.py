"""ForexDojo API — Web endpoints for the RL training engine."""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mt5_mcp.forex_dojo import (
    ForexDojo,
    DojoConfig,
    quick_lesson,
    full_session,
    list_trained_models,
)

router = APIRouter(prefix="/api/dojo", tags=["dojo"])

logger = logging.getLogger("web_dojo")

# Training state
_training_lock = threading.Lock()
_training_active: dict[str, bool] = {}  # symbol -> is training
_train_stop: dict[str, threading.Event] = {}  # symbol -> stop signal
_live_progress: dict[str, dict] = {}  # symbol -> latest live progress (in-memory)


# ═══════════════════════════════════════════════════════════════════════════
# Research Lab findings (2026-09-01 / 2026-09-02 research sessions)
# Served to the Dojo UI so the honest evidence base is visible in-app.
# ═══════════════════════════════════════════════════════════════════════════

_RESEARCH_FINDINGS = {
    "generated": "2026-09-02",
    "rl_verdict": {
        "summary": "RL agents (forex_rl PPO) have NO edge — parked.",
        "detail": (
            "After fixing 5 accounting bugs (10x P&L understatement, double "
            "commission, truncation leak, SL/TP trade-log wipe, random eval "
            "starts) and reconciling trade log vs equity to $0.00 drift, all "
            "4 symbols are net-negative: EURUSD -$491, GBPUSD -$806, USDJPY "
            "-$739, XAUUSD -$2,727. PF 0.58-0.90. Root cause: cost churn — "
            "avg hold < 1h, spread+slippage+commission consume ~25% of the "
            "risked amount per trade. The agents lose to the spread, not the "
            "market. Earlier 'wins' were accounting illusions."
        ),
        "honest_baseline": [
            {"symbol": "EURUSD", "trades": 38, "net": -491, "pf": 0.58},
            {"symbol": "GBPUSD", "trades": 64, "net": -806, "pf": 0.60},
            {"symbol": "USDJPY", "trades": 112, "net": -739, "pf": 0.75},
            {"symbol": "XAUUSD", "trades": 248, "net": -2727, "pf": 0.66},
        ],
        "rejections": [
            "Flat inaction penalty — manufactures activity, not alpha",
            "Regime-gated reward — 2/4 beat baseline but 0/4 profitable",
            "All v1-v3 reported numbers — produced by engines hiding SL/TP losses",
        ],
    },
    "price_action_verdict": {
        "summary": "JP225 is the ONLY validated price-action family — deployed live.",
        "detail": (
            "Deep validation of 6 candidate families with perturbation sweeps "
            "plus a 5-day out-of-sample window roll killed 5 of 6: SPX500 "
            "(PF 1.54→0.22), XAGUSD (2.28→0.52), EURGBP (1.39→0.17), EURUSD "
            "(10.5→0.69), USOIL (live +$115 was luck; backtest -$7/trade). "
            "JP225 alone survived: reproduced OOS (PF 1.545→1.64), 100% "
            "perturbation retention, edge survives plain SL/TP (PF 1.16 — "
            "the edge is in the entries)."
        ),
        "jp225": {
            "config": "H4 trend, H1+H4 zones, score≥40, RR≥3, TP 80-100",
            "stats": "29 trades, WR 41.4%, PF 1.64, +$534/6mo, MaxDD 2.36%",
            "dojo_stress": [
                {"test": "windows 3k/4k/5k/6k bars", "result": "PF 1.79/1.64/1.64/1.26", "pass": True},
                {"test": "plain SL/TP (exits off)", "result": "PF 1.16, +$135 — edge is in entries", "pass": True},
                {"test": "regime=trend filter", "result": "PF 1.31, cuts 55% net — DO NOT USE", "pass": False},
                {"test": "regime=range filter", "result": "0 trades — DO NOT USE", "pass": False},
                {"test": "loss guard 3/cd20", "result": "PF 1.64, zero cost — keep ON", "pass": True},
            ],
            "deployment": "auto_trader JP225_Z40 config, spread-gate fixed 2026-09-02, task MT5AutoTraderSIM enabled",
            "guardrails": [
                "Monthly: python -X utf8 validate_jp225_monthly.py — HALT verdict = disable",
                "Scale-up: no risk increase until 20+ live trades PF ≥ 1.2",
                "Kill switch: create mt5_mcp/memory/AUTO_TRADER_KILL",
            ],
        },
        "dead_families": [
            {"family": "SPX500 z60", "was": "PF 1.54 +$383", "now": "PF 0.22 -$317"},
            {"family": "XAGUSD z40", "was": "PF 2.28 +$331", "now": "PF 0.52 -$51"},
            {"family": "EURGBP z40", "was": "PF 1.39 +$120", "now": "PF 0.17 -$312"},
            {"family": "EURUSD H1-z70", "was": "PF 10.5 +$424", "now": "PF 0.69 -$56"},
            {"family": "USOIL z40", "was": "live +$115/12t", "now": "backtest PF 0.09 — luck"},
        ],
    },
    "deriv_verdict": {
        "summary": "Spike-drift during London open is the only profitable Deriv edge.",
        "detail": (
            "boom_crash_spike_drift: 5 trades, 80% WR, +$169, avg +$34/trade "
            "at 93.5 avg confidence — best setup in the entire journal. "
            "Manual Deriv trades: 77 trades, 33.8% WR, net -$19. The best "
            "window is 07:00-09:00 UTC (London open, +$148 across 16 trades). "
            "Avoid 11:00-12:00 and 18:00 UTC (combined -$169). Learned "
            "config enforces: confidence ≥ 90, spike age < 15 min, "
            "spike ≥ 2.5 ATR."
        ),
        "sessions_good": ["07:00-09:00 UTC (London open)"],
        "sessions_bad": ["11:00-12:00 UTC", "18:00 UTC"],
        "mistakes": [
            {"mistake": "entered_before_confirmation", "cost": -84.39},
            {"mistake": "stale_spike_age", "cost": -43.09},
            {"mistake": "ignored_spike_clustering", "cost": -43.09},
        ],
    },
    "reports": [
        {"name": "RL forensics + regime-gated v4", "path": "mt5_mcp/memory/research_lab/report_2026-09-01.md"},
        {"name": "JP225 consolidation + deployment", "path": "mt5_mcp/memory/research_lab/report_2026-09-02.md"},
    ],
}


@router.get("/research")
def get_research_findings():
    """Research Lab findings: RL honest baseline, family validation,
    JP225 deployment evidence, Deriv spike-drift edge."""
    return _RESEARCH_FINDINGS


class _StopTraining(Exception):
    """Raised by the per-episode callback when a stop was requested."""
    pass


@router.get("/models")
def get_models():
    """List all trained Dojo models."""
    return {"models": list_trained_models()}


@router.get("/progress/{symbol}")
def get_progress(symbol: str, timeframe: str = "H1"):
    """Get training progress for a symbol."""
    dojo = ForexDojo(DojoConfig(symbol=symbol, timeframe=timeframe))
    progress = dojo.load_progress(symbol, timeframe)
    if progress is None:
        raise HTTPException(404, f"No training data for {symbol} {timeframe}")
    from dataclasses import asdict
    return asdict(progress)


@router.post("/train")
def start_training(
    symbol: str = Query(...),
    timeframe: str = Query("H1"),
    episodes: int = Query(100),
    learning_rate: float = Query(0.001),
    spread_pips: float = Query(1.5),
    slippage_pips: float = Query(0.5),
    entropy_coeff: float = Query(0.01),
    algorithm: str = Query("ppo"),
    clip_epsilon: float = Query(0.2),
    ppo_epochs: int = Query(4),
    multi_timeframe: bool = Query(True),
):
    """Start a training session (async)."""
    with _training_lock:
        if _training_active.get(symbol):
            raise HTTPException(409, f"Training already active for {symbol}")
        _training_active[symbol] = True
    _train_stop[symbol] = threading.Event()

    def _train():
        stop_evt = _train_stop.get(symbol)
        start_time = time.time()
        try:
            config = DojoConfig(
                symbol=symbol, timeframe=timeframe,
                episodes=episodes, learning_rate=learning_rate,
                spread_pips=spread_pips, slippage_pips=slippage_pips,
                entropy_coeff=entropy_coeff,
                algorithm=algorithm,
                clip_epsilon=clip_epsilon,
                ppo_epochs=ppo_epochs,
                multi_timeframe=multi_timeframe,
            )
            dojo = ForexDojo(config)

            def _check_stop(result=None, episode_history=None, speed=None):
                # Called at each episode boundary - honours the stop signal
                # and also stores live progress for real-time frontend polling.
                if stop_evt and stop_evt.is_set():
                    raise _StopTraining()
                if result is not None:
                    with _training_lock:
                        _live_progress[symbol] = {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "episode": result.episode,
                            "total_episodes": episodes,
                            "reward": result.total_reward,
                            "pnl": result.pnl,
                            "win_rate": result.win_rate,
                            "skill_level": result.skill_level,
                            "trades": result.trades,
                            "wins": result.wins,
                            "losses": result.losses,
                            "training_time_seconds": time.time() - start_time,
                            "active": True,
                            "sharpe_ratio": getattr(result, 'sharpe_ratio', 0),
                            "profit_factor": getattr(result, 'profit_factor', 0),
                            "test_win_rate": 0,
                            "test_pnl": 0,
                            "test_sharpe": 0,
                            "episode_history": episode_history or [],
                            "speed": speed or {},
                            "phase": "training",
                            "algorithm": algorithm,
                        }

            def _step_update(step, total_steps, info):
                with _training_lock:
                    if symbol in _live_progress:
                        _live_progress[symbol].update({
                            "current_step": step,
                            "total_steps": total_steps,
                            "step_balance": round(info.get("balance", 0), 2),
                            "step_position": info.get("position", 0),
                            "step_trades": info.get("trades", 0),
                            "step_pnl": round(info.get("pnl", 0), 2),
                            "step_drawdown": round(info.get("drawdown", 0), 2),
                        })

            dojo.train(callback=_check_stop, step_callback=_step_update)
        except _StopTraining:
            logger.info("Dojo training stopped early for %s", symbol)
        except Exception:
            logger.exception("Dojo training failed for %s", symbol)
        finally:
            with _training_lock:
                _training_active[symbol] = False
                # Mark as inactive but keep last progress for display
                if symbol in _live_progress:
                    _live_progress[symbol]["active"] = False
                    _live_progress[symbol]["phase"] = "done"
            _train_stop.pop(symbol, None)

    t = threading.Thread(target=_train, daemon=True)
    t.start()
    return {"status": "started", "symbol": symbol, "episodes": episodes}


@router.post("/quick-lesson")
def quick_lesson_endpoint(
    symbol: str = Query(...),
    timeframe: str = Query("H1"),
    episodes: int = Query(10),
):
    """Run a quick lesson (synchronous, ~10 episodes)."""
    from dataclasses import asdict
    progress = quick_lesson(symbol, episodes, timeframe)
    return asdict(progress)


@router.get("/status")
def training_status():
    """Check which symbols are currently training."""
    return {"active": dict(_training_active)}


@router.get("/live/{symbol}")
def live_progress(symbol: str, timeframe: str = "H1"):
    """Get real-time training progress for a symbol (in-memory)."""
    with _training_lock:
        return _live_progress.get(symbol, {
            "symbol": symbol,
            "timeframe": timeframe,
            "episode": 0,
            "total_episodes": 0,
            "reward": 0,
            "pnl": 0,
            "win_rate": 0,
            "skill_level": "Unknown",
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "training_time_seconds": 0,
            "active": False,
            "sharpe_ratio": 0,
            "profit_factor": 0,
            "test_win_rate": 0,
            "test_pnl": 0,
            "test_sharpe": 0,
            "episode_history": [],
            "speed": {},
            "phase": "idle",
            "current_step": 0,
            "total_steps": 500,
            "step_balance": 0,
            "step_position": 0,
            "step_trades": 0,
            "step_pnl": 0,
            "step_drawdown": 0,
            "algorithm": "ppo",
        })


@router.get("/step-progress/{symbol}")
def step_progress(symbol: str):
    """Get step-level training progress (updated every ~10 steps during an episode)."""
    with _training_lock:
        data = _live_progress.get(symbol, {})
    return {
        "symbol": symbol,
        "episode": data.get("episode", 0),
        "total_episodes": data.get("total_episodes", 0),
        "step": data.get("current_step", 0),
        "total_steps": data.get("total_steps", 500),
        "balance": data.get("step_balance", 0),
        "position": data.get("step_position", 0),
        "trades": data.get("step_trades", 0),
        "pnl": data.get("step_pnl", 0),
        "drawdown": data.get("step_drawdown", 0),
        "phase": data.get("phase", "idle"),
        "episode_history": data.get("episode_history", []),
        "speed": data.get("speed", {}),
        "active": data.get("active", False),
        "algorithm": data.get("algorithm", "ppo"),
    }


@router.delete("/stop/{symbol}")
def stop_training(symbol: str):
    """Signal a running training session to stop (interrupts at the next
    episode boundary via the per-episode callback)."""
    with _training_lock:
        if not _training_active.get(symbol):
            raise HTTPException(404, f"No training active for {symbol}")
    evt = _train_stop.get(symbol)
    if evt is not None:
        evt.set()
        return {"status": "stopping", "symbol": symbol}
    raise HTTPException(404, f"No training active for {symbol}")


@router.get("/leaderboard")
def leaderboard():
    """Rank all trained models by skill score."""
    models = list_trained_models()
    ranked = sorted(models, key=lambda m: m.get("sharpe_ratio", 0), reverse=True)
    return {"leaderboard": ranked}


@router.get("/compare")
def compare_models(symbols: str = Query(..., description="Comma-separated symbols to compare")):
    """Compare multiple trained models side by side."""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    results = []
    for sym in symbol_list:
        dojo = ForexDojo(DojoConfig(symbol=sym))
        progress = dojo.load_progress(sym)
        if progress is None:
            results.append({"symbol": sym, "status": "not trained"})
            continue
        from dataclasses import asdict
        result = asdict(progress)
        # Add metadata
        config_path = Path(__file__).parent.parent / "dojo" / "models" / f"{sym}_H1" / "progress.json"
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            result["metadata"] = data.get("metadata", {})
        results.append(result)
    return {"models": results, "count": len(results)}


@router.post("/walk-forward")
def run_walk_forward(
    symbol: str = Query(...),
    timeframe: str = Query("H1"),
    episodes: int = Query(30),
    n_windows: int = Query(3),
):
    """Run walk-forward validation for a symbol."""
    from mt5_mcp.forex_dojo import ForexDojo, DojoConfig
    config = DojoConfig(symbol=symbol, timeframe=timeframe, episodes=episodes)
    dojo = ForexDojo(config)
    result = dojo.walk_forward_eval(n_windows=n_windows)
    return result


@router.get("/model/{symbol}")
def get_model_detail(symbol: str, timeframe: str = "H1"):
    """Get detailed model info: progress, episodes, config for the detail modal."""
    dojo = ForexDojo(DojoConfig(symbol=symbol, timeframe=timeframe))
    progress = dojo.load_progress(symbol, timeframe)
    if progress is None:
        raise HTTPException(404, f"No training data for {symbol} {timeframe}")
    from dataclasses import asdict
    result = asdict(progress)
    # Include config
    config_path = Path(__file__).parent.parent / "dojo" / "models" / f"{symbol}_{timeframe}" / "progress.json"
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        result["config"] = data.get("config", {})
        result["test_win_rate"] = data.get("test_win_rate", 0)
        result["test_pnl"] = data.get("test_pnl", 0)
        result["test_sharpe"] = data.get("test_sharpe", 0)
        result["metadata"] = data.get("metadata", {})
        result["is_stale"] = data.get("is_stale", False)
    else:
        result["metadata"] = {}
        result["is_stale"] = False
    return result


@router.delete("/model/{symbol}")
def delete_model(symbol: str, timeframe: str = "H1"):
    """Delete a trained model."""
    import shutil
    model_dir = Path(__file__).parent.parent / "dojo" / "models" / f"{symbol}_{timeframe}"
    if not model_dir.exists():
        raise HTTPException(404, f"No model for {symbol} {timeframe}")
    shutil.rmtree(model_dir)
    return {"status": "deleted", "symbol": symbol, "timeframe": timeframe}


@router.post("/train-batch")
def start_batch_training(
    symbols: str = Query(..., description="Comma-separated symbols"),
    timeframe: str = Query("H1"),
    episodes: int = Query(50),
    learning_rate: float = Query(0.001),
    algorithm: str = Query("ppo"),
):
    """Start training for multiple symbols sequentially."""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    results = []
    for sym in symbol_list:
        with _training_lock:
            if _training_active.get(sym):
                results.append({"symbol": sym, "status": "skipped (already training)"})
                continue
            _training_active[sym] = True
        _train_stop[sym] = threading.Event()

        def _train_batch(sym=sym):
            stop_evt = _train_stop.get(sym)
            start_time = time.time()
            try:
                config = DojoConfig(
                    symbol=sym, timeframe=timeframe,
                    episodes=episodes, learning_rate=learning_rate,
                    algorithm=algorithm,
                )
                dojo = ForexDojo(config)
                def _cb(result=None, episode_history=None, speed=None):
                    if stop_evt and stop_evt.is_set():
                        raise _StopTraining()
                dojo.train(callback=_cb)
            except _StopTraining:
                pass
            except Exception:
                logger.exception("Batch training failed for %s", sym)
            finally:
                with _training_lock:
                    _training_active[sym] = False
                _train_stop.pop(sym, None)

        t = threading.Thread(target=_train_batch, daemon=True)
        t.start()
        results.append({"symbol": sym, "status": "started"})

    return {"status": "started", "symbols": results}
