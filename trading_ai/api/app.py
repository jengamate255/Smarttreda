"""FastAPI application for the Trading AI platform."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from trading_ai.agents.orchestrator import AgentOrchestrator
from trading_ai.api.dashboard import router as dashboard_router
from trading_ai.api.dashboard_data import router as dashboard_data_router
from trading_ai.api.dashboard_data import set_services as set_dashboard_services
from trading_ai.config.events import EventType, PerformanceUpdated, TradeClosed
from trading_ai.config.settings import settings
from trading_ai.evolution.self_learning import SelfLearningLoop
from trading_ai.execution.mt5_adapter import MT5Adapter
from trading_ai.execution.paper import PaperBroker
from trading_ai.execution.paper_journal import PaperJournalBridge
from trading_ai.monitoring.health import HealthMonitor

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trading AI Platform",
    version="0.1.0",
    description="Forex Trading AI with event-driven orchestration",
)

app.include_router(dashboard_router)
app.include_router(dashboard_data_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Singletons
# ------------------------------------------------------------------

orchestrator = AgentOrchestrator()
paper_broker = PaperBroker()
mt5_adapter = MT5Adapter()
health_monitor = HealthMonitor()
self_learning = SelfLearningLoop(
    db_url_prefix="api_self_learn",
    multi_regime=bool(getattr(settings, "self_learn_multi_regime", True)),
    population_size=int(getattr(settings, "self_learn_population_size", 3)),
)
# Paper fills → TradeJournal (memory stream for research hypotheses)
paper_journal = PaperJournalBridge(
    paper_broker,
    self_learning.journal,
    strategy_id=self_learning.state.best_genome_id or "paper-default",
)
paper_broker.default_position_metadata.update({
    "strategy_id": self_learning.state.best_genome_id,
    "timeframe": "M5",
    "session": "london",
    "regime": "ranging",
})

# Dashboard JSON APIs read these singletons
set_dashboard_services(broker=paper_broker, loop=self_learning, health=health_monitor)

_market_data: dict[str, dict[str, Any]] = {}
_market_state: dict[str, dict[str, Any]] = {}
_strategies: dict[str, dict[str, Any]] = {}
_trades: list[dict[str, Any]] = []
_performance: dict[str, Any] = {
    "total_trades": 0,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "sharpe_ratio": 0.0,
    "max_drawdown": 0.0,
    "expectancy": 0.0,
}
_hypotheses: list[dict[str, Any]] = []
_experiments: list[dict[str, Any]] = []
_live_clients: list[WebSocket] = []


# ------------------------------------------------------------------
# Pydantic request models
# ------------------------------------------------------------------

class ExperimentRequest(BaseModel):
    hypothesis_id: str | None = None
    parent_strategy_id: str | None = None
    candidate_strategy_id: str | None = None
    name: str = ""
    description: str = ""


class PaperStartRequest(BaseModel):
    initial_balance: float = 10000.0
    spread_model: str = "variable"
    slippage_model: str = "random"


# ------------------------------------------------------------------
# Startup / shutdown
# ------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    await health_monitor.start()
    await paper_broker.start()

    # PERFORMANCE_UPDATED → auto-start recursive self-learning (once per cooldown)
    _last_autostart = {"t": 0.0}

    async def _on_performance(event: Any) -> None:
        import time as _time
        now = _time.time()
        cooldown = float(getattr(settings, "auto_self_learn_interval_s", 3600))
        if not getattr(settings, "auto_self_learn", True):
            return
        if self_learning.state.running or self_learning.safety.state.stopped:
            return
        if now - _last_autostart["t"] < cooldown:
            return
        _last_autostart["t"] = now
        started = self_learning.start(interval_s=cooldown, max_iterations=None)
        logger.info("Auto-start self-learning from PERFORMANCE_UPDATED: %s", started)

    async def _on_trade_closed(event: Any) -> None:
        # Mirror closed trades into journal for weakness analysis
        try:
            from trading_ai.memory.journal import (
                TradeEntry, MarketRegime, SessionType, TradeOutcome, ExitReason,
            )
            from datetime import datetime as _dt
            pnl = float(getattr(event, "pnl", 0) or 0)
            outcome = TradeOutcome.WIN if pnl > 0 else (
                TradeOutcome.LOSS if pnl < 0 else TradeOutcome.BREAKEVEN
            )
            entry = TradeEntry(
                timestamp=_dt.utcnow(),
                strategy_version=self_learning.state.best_genome_id or "orchestrator",
                market_regime=MarketRegime.RANGING,
                symbol="EURUSD",
                timeframe="M5",
                session=SessionType.LONDON,
                direction="BUY",
                entry_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                spread_at_entry=0.00015,
                volatility_at_entry=0.0,
                entry_reason="trade_closed_event",
                outcome=outcome,
                exit_price=float(getattr(event, "exit_price", 0) or 0),
                r_multiple=float(getattr(event, "r_multiple", 0) or 0),
                pnl_currency=pnl,
            )
            self_learning.journal.record_trade(entry)
        except Exception:
            logger.debug("Failed to journal TradeClosed event", exc_info=True)

    orchestrator.subscribe(EventType.PERFORMANCE_UPDATED, _on_performance)
    orchestrator.subscribe(EventType.TRADE_CLOSED, _on_trade_closed)
    try:
        await orchestrator.start()
    except Exception:
        logger.debug("Orchestrator start skipped", exc_info=True)

    # Optional: kick off self-learning immediately when enabled
    if getattr(settings, "auto_self_learn", True) and not self_learning.state.running:
        self_learning.start(interval_s=float(getattr(settings, "auto_self_learn_interval_s", 3600)))

    logger.info("Trading AI API started")


@app.on_event("shutdown")
async def shutdown() -> None:
    await self_learning.stop()
    await health_monitor.stop()
    await paper_broker.stop()
    await orchestrator.stop()
    logger.info("Trading AI API stopped")


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@app.get("/health")
async def get_health() -> dict[str, Any]:
    return health_monitor.get_status()


# ------------------------------------------------------------------
# Market data
# ------------------------------------------------------------------

@app.get("/market/data/{symbol}")
async def get_market_data(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    data = _market_data.get(symbol)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No market data for {symbol}")
    return data


@app.get("/market/state/{symbol}")
async def get_market_state(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    state = _market_state.get(symbol)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No market state for {symbol}")
    return state


# ------------------------------------------------------------------
# Strategies
# ------------------------------------------------------------------

@app.get("/strategies")
async def list_strategies() -> list[dict[str, Any]]:
    return list(_strategies.values())


@app.get("/strategies/{strategy_id}/lineage")
async def get_strategy_lineage(strategy_id: str) -> dict[str, Any]:
    strategy = _strategies.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")

    lineage: list[dict[str, Any]] = []
    current = strategy
    while current is not None:
        lineage.append(current)
        parent_id = current.get("parent_id")
        current = _strategies.get(parent_id) if parent_id else None

    return {
        "strategy_id": strategy_id,
        "lineage": lineage,
        "depth": len(lineage),
    }


# ------------------------------------------------------------------
# Trades
# ------------------------------------------------------------------

@app.get("/trades")
async def list_trades(
    symbol: str | None = None,
    side: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filtered = _trades
    if symbol:
        filtered = [t for t in filtered if t.get("symbol") == symbol.upper()]
    if side:
        filtered = [t for t in filtered if t.get("side") == side.upper()]
    return filtered[offset : offset + limit]


# ------------------------------------------------------------------
# Performance
# ------------------------------------------------------------------

@app.get("/performance")
async def get_performance() -> dict[str, Any]:
    return _performance


@app.get("/performance/segmented")
async def get_segmented_performance() -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for trade in _trades:
        sym = trade.get("symbol", "UNKNOWN")
        by_symbol.setdefault(sym, []).append(trade)

    segments: dict[str, dict[str, Any]] = {}
    for sym, trades in by_symbol.items():
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        segments[sym] = {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(trades), 2) if trades else 0.0,
        }

    return {
        "overall": _performance,
        "by_symbol": segments,
    }


# ------------------------------------------------------------------
# Research
# ------------------------------------------------------------------

@app.get("/research/hypotheses")
async def list_hypotheses() -> list[dict[str, Any]]:
    return _hypotheses


@app.get("/research/experiments")
async def list_experiments() -> list[dict[str, Any]]:
    return _experiments


@app.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str) -> dict[str, Any]:
    for exp in _experiments:
        if exp.get("experiment_id") == experiment_id:
            return exp
    raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} not found")


@app.post("/experiments")
async def create_experiment(request: ExperimentRequest) -> dict[str, Any]:
    experiment = {
        "experiment_id": str(uuid4()),
        "hypothesis_id": request.hypothesis_id,
        "parent_strategy_id": request.parent_strategy_id,
        "candidate_strategy_id": request.candidate_strategy_id,
        "name": request.name,
        "description": request.description,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {},
    }
    _experiments.append(experiment)
    return experiment


# ------------------------------------------------------------------
# Evolution
# ------------------------------------------------------------------

@app.get("/evolution/lineage")
async def get_evolution_lineage() -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for strat in _strategies.values():
        sid = strat.get("strategy_id", "")
        parent = strat.get("parent_id")
        if sid not in tree:
            tree[sid] = {"strategy_id": sid, "children": [], **strat}
        if parent and parent in tree:
            tree[parent]["children"].append(sid)
        elif parent:
            tree[parent] = {"strategy_id": parent, "children": [sid]}

    roots = [s for s in tree.values() if not any(
        s["strategy_id"] in t.get("children", []) for t in tree.values()
    )]
    return {"tree": list(tree.values()), "roots": [r["strategy_id"] for r in roots]}


# ------------------------------------------------------------------
# Risk
# ------------------------------------------------------------------

@app.get("/risk/status")
async def get_risk_status() -> dict[str, Any]:
    account = paper_broker.get_account_info()
    kill_status = mt5_adapter.get_kill_switch_status()
    return {
        "account": account,
        "kill_switch": kill_status,
        "risk_per_trade": settings.risk_per_trade,
        "min_risk_reward": settings.min_risk_reward,
        "daily_loss_limit": settings.kill_switch.daily_loss_limit,
        "max_drawdown": settings.kill_switch.max_drawdown,
    }


# ------------------------------------------------------------------
# Paper trading
# ------------------------------------------------------------------

@app.post("/paper/start")
async def start_paper_trading(request: PaperStartRequest) -> dict[str, Any]:
    if paper_broker._running:
        return {"status": "already_running", "account": paper_broker.get_account_info()}

    paper_broker._initial_balance = request.initial_balance
    paper_broker._spread_model = paper_broker._spread_model.__class__(request.spread_model)
    paper_broker._slippage_model = paper_broker._slippage_model.__class__(request.slippage_model)
    await paper_broker.start()
    return {"status": "started", "account": paper_broker.get_account_info()}


@app.post("/paper/stop")
async def stop_paper_trading() -> dict[str, Any]:
    if not paper_broker._running:
        return {"status": "not_running"}
    await paper_broker.stop()
    return {"status": "stopped", "account": paper_broker.get_account_info()}


@app.get("/paper/account")
async def get_paper_account() -> dict[str, Any]:
    """Get paper trading account info for dashboard."""
    return paper_broker.get_account_info()


@app.get("/paper/positions")
async def get_paper_positions() -> list[dict[str, Any]]:
    """Get open paper positions for dashboard."""
    return paper_broker.get_positions()


# ------------------------------------------------------------------
# Self-learning loop (recursive auto-improvement)
# ------------------------------------------------------------------

class SelfLearnStartRequest(BaseModel):
    interval_s: float = 30.0
    max_iterations: int | None = None


@app.get("/evolution/self-learning/status")
async def self_learning_status() -> dict[str, Any]:
    return self_learning.get_status()


@app.get("/evolution/self-learning/history")
async def self_learning_history(limit: int = 50) -> list[dict[str, Any]]:
    return self_learning.get_history(limit=limit)


@app.post("/evolution/self-learning/step")
async def self_learning_step() -> dict[str, Any]:
    """Run one synchronous iteration of the self-learning loop."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, self_learning.run_once)
    return {
        "iteration": result.iteration,
        "decision": result.decision,
        "hypothesis_id": result.hypothesis_id,
        "parent_id": result.parent_id,
        "candidate_id": result.candidate_id,
        "improved": result.improved,
        "reasons": result.reasons,
        "parent_metrics": result.parent_metrics,
        "candidate_metrics": result.candidate_metrics,
        "duration_s": result.duration_s,
        "error": result.error,
        "status": self_learning.get_status(),
    }


@app.post("/evolution/self-learning/start")
async def self_learning_start(request: SelfLearnStartRequest | None = None) -> dict[str, Any]:
    req = request or SelfLearnStartRequest()
    started = self_learning.start(interval_s=req.interval_s, max_iterations=req.max_iterations)
    return {
        "status": "started" if started else "already_running",
        "interval_s": req.interval_s,
        "max_iterations": req.max_iterations,
        "state": self_learning.get_status(),
    }


@app.post("/evolution/self-learning/stop")
async def self_learning_stop() -> dict[str, Any]:
    was_running = self_learning.state.running
    await self_learning.stop()
    return {
        "status": "stopped" if was_running else "not_running",
        "state": self_learning.get_status(),
    }


@app.get("/evolution/self-learning/credits")
async def self_learning_credits() -> list[dict[str, Any]]:
    """Mutation credit ledger: which mutation types historically help."""
    return self_learning.get_mutation_credits()


class PhaseRequest(BaseModel):
    phase: str  # "explore" | "exploit"


@app.post("/evolution/self-learning/phase")
async def self_learning_phase(request: PhaseRequest) -> dict[str, Any]:
    if request.phase not in ("explore", "exploit"):
        raise HTTPException(status_code=400, detail="phase must be explore|exploit")
    self_learning.force_phase(request.phase)
    return {"phase": request.phase, "safety": self_learning.safety.to_dict()}


@app.post("/evolution/self-learning/reset-safety")
async def self_learning_reset_safety() -> dict[str, Any]:
    self_learning.reset_safety()
    return {"status": "reset", "safety": self_learning.safety.to_dict()}


class PaperMetricsRequest(BaseModel):
    strategy_id: str
    metrics: dict[str, Any]


@app.post("/evolution/self-learning/paper-metrics")
async def self_learning_paper_metrics(request: PaperMetricsRequest) -> dict[str, Any]:
    """Submit paper-trade results; promotes stage to LIVE_PENDING when confirmed."""
    return self_learning.submit_paper_metrics(request.strategy_id, request.metrics)


class ApproveLiveRequest(BaseModel):
    strategy_id: str
    approver: str = "user"


@app.post("/evolution/self-learning/approve-live")
async def self_learning_approve_live(request: ApproveLiveRequest) -> dict[str, Any]:
    """Human-in-the-loop: explicit approval required before LIVE stage."""
    try:
        return self_learning.approve_live(request.strategy_id, request.approver)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RejectLiveRequest(BaseModel):
    strategy_id: str
    reason: str = "rejected"


@app.post("/evolution/self-learning/reject-live")
async def self_learning_reject_live(request: RejectLiveRequest) -> dict[str, Any]:
    return self_learning.reject_live(request.strategy_id, request.reason)


@app.get("/evolution/self-learning/deployment")
async def self_learning_deployment() -> dict[str, Any]:
    return self_learning.deployment.to_dict()


@app.get("/evolution/self-learning/journal-summary")
async def self_learning_journal_summary() -> dict[str, Any]:
    """Journal feed status (paper fills recorded for research)."""
    return {
        "paper_bridge_recorded": getattr(paper_journal, "recorded", 0),
        "journal_db": str(self_learning.journal.engine.url),
    }


@app.get("/evolution/self-learning/pending-approvals")
async def self_learning_pending_approvals() -> dict[str, Any]:
    """
    Dashboard endpoint: everything needing human attention.
    
    Returns strategies at LIVE_PENDING (need approve/reject),
    safety stops, and paper-trade confirmations.
    """
    dep = self_learning.deployment
    records = dep.to_dict()
    
    live_pending = {
        sid: info for sid, info in records.items()
        if info["stage"] == "live_pending"
    }
    paper_awaiting = {
        sid: info for sid, info in records.items()
        if info["stage"] == "paper"
    }
    sandbox_promoted = {
        sid: info for sid, info in records.items()
        if info["stage"] == "paper" and info.get("notes", "").startswith("Backtest promoted")
    }
    
    safety = self_learning.safety.to_dict()
    safety_alerts = []
    if safety.get("stopped"):
        safety_alerts.append({
            "type": "circuit_breaker",
            "message": f"Self-learning STOPPED: {safety.get('stop_reason', 'unknown')}",
            "severity": "critical"
        })
    if safety.get("stagnation", 0) >= safety.get("limits", {}).get("max_stagnation", 15) * 0.8:
        safety_alerts.append({
            "type": "stagnation",
            "message": f"Approaching phase transition: {safety.get('stagnation', 0)}/{safety.get('limits', {}).get('max_stagnation', 15)} stagnation",
            "severity": "warning"
        })
    
    return {
        "live_pending": live_pending,
        "paper_awaiting_confirmation": paper_awaiting,
        "recently_promoted_to_paper": sandbox_promoted,
        "safety_alerts": safety_alerts,
        "summary": {
            "need_live_approval": len(live_pending),
            "in_paper_trading": len(paper_awaiting),
            "safety_stopped": safety.get("stopped", False),
            "current_phase": safety.get("phase", "explore"),
        },
        "actions": {
            "approve_live": "/evolution/self-learning/approve-live",
            "reject_live": "/evolution/self-learning/reject-live",
            "submit_paper": "/evolution/self-learning/paper-metrics",
        }
    }


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    await websocket.accept()
    _live_clients.append(websocket)
    logger.info("WebSocket client connected (%d total)", len(_live_clients))
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type", "")

            if msg_type == "subscribe":
                await websocket.send_json({"type": "subscribed", "symbols": msg.get("symbols", [])})
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "time": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _live_clients:
            _live_clients.remove(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(_live_clients))


async def broadcast_update(event_type: str, data: dict[str, Any]) -> None:
    payload = json.dumps({"type": event_type, "data": data, "time": datetime.now(timezone.utc).isoformat()})
    disconnected: list[WebSocket] = []
    for client in _live_clients:
        try:
            await client.send_text(payload)
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        _live_clients.remove(client)
