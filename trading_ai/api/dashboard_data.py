"""JSON data endpoints backing the dashboard pages.

Each endpoint aggregates real data from the running singletons (paper broker,
self-learning loop, health monitor, journals). The HTML pages in
``trading_ai.api.dashboard`` fetch from here.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from trading_ai.config.settings import settings
from trading_ai.memory.journal import FilterCriteria

router = APIRouter(prefix="/dashboard/api", tags=["dashboard-data"])

# Injected by app.py after singletons are constructed
paper_broker: Any = None
self_learning: Any = None
health_monitor: Any = None


def set_services(
    broker: Any = None, loop: Any = None, health: Any = None
) -> None:
    global paper_broker, self_learning, health_monitor
    if broker is not None:
        paper_broker = broker
    if loop is not None:
        self_learning = loop
    if health is not None:
        health_monitor = health


def _jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums/datetimes/non-finite floats to JSON-safe values."""
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    return str(obj)


def _agg_dict(result: Any) -> dict[str, Any]:
    d = asdict(result) if is_dataclass(result) else dict(result)
    return _jsonable(d)


# ------------------------------------------------------------------
# Trading: account, positions, equity curve, risk
# ------------------------------------------------------------------
@router.get("/trading")
def trading_data() -> dict[str, Any]:
    if paper_broker is None:
        return {"error": "paper_broker_not_ready"}

    account = paper_broker.get_account_info()
    positions = paper_broker.get_positions()
    closed = paper_broker.get_position_history(limit=200)
    curve = paper_broker.get_equity_curve(limit=1000)

    # Realized P&L stats from closed trades
    realized = [c["realized_pnl"] for c in closed]
    wins = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # Drawdown from equity curve
    peak = -math.inf
    max_dd = 0.0
    max_dd_pct = 0.0
    for point in curve:
        eq = point["equity"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak if peak else 0.0

    initial = curve[0]["balance"] if curve else account.get("balance", 0.0)
    return _jsonable({
        "account": account,
        "positions": positions,
        "closed_trades": closed[-50:],
        "equity_curve": curve,
        "stats": {
            "closed_count": len(closed),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / len(realized) if realized else None,
            "net_realized": round(sum(realized), 2),
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 6),
            "total_return_pct": round(
                (account.get("equity", 0.0) - initial) / initial * 100, 4
            ) if initial else 0.0,
        },
        "risk": {
            "risk_per_trade": settings.risk_per_trade,
            "min_risk_reward": settings.min_risk_reward,
            "daily_loss_limit": settings.kill_switch.daily_loss_limit,
            "max_drawdown_limit": settings.kill_switch.max_drawdown,
            "margin_used_pct": round(
                account["margin"] / account["equity"] * 100, 2
            ) if account.get("equity") else 0.0,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ------------------------------------------------------------------
# Performance: Sharpe/drawdown/win-rate by symbol/regime/timeframe/session
# ------------------------------------------------------------------
@router.get("/performance")
def performance_data() -> dict[str, Any]:
    if self_learning is None:
        return {"error": "self_learning_not_ready"}

    journal = self_learning.journal
    criteria = FilterCriteria()
    overall = journal.aggregate(criteria)

    groups: dict[str, dict[str, Any]] = {}
    for dim in ("symbol", "market_regime", "timeframe", "session"):
        try:
            groups[dim] = {
                key: _agg_dict(agg)
                for key, agg in journal.group_by(dim, criteria).items()
            }
        except Exception as exc:  # pragma: no cover - defensive
            groups[dim] = {"error": str(exc)}

    # Backtest fitness of the current best genome (regime breakdown)
    status = self_learning.get_status()
    backtest = status.get("best_metrics") or {}

    return _jsonable({
        "overall": _agg_dict(overall),
        "by_symbol": groups.get("symbol", {}),
        "by_regime": groups.get("market_regime", {}),
        "by_timeframe": groups.get("timeframe", {}),
        "by_session": groups.get("session", {}),
        "backtest": {
            "genome_id": status.get("best_genome_id"),
            "metrics": backtest,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ------------------------------------------------------------------
# Research: hypotheses, experiments, A/B comparisons, research tasks
# ------------------------------------------------------------------
@router.get("/research")
def research_data() -> dict[str, Any]:
    if self_learning is None:
        return {"error": "self_learning_not_ready"}

    hypotheses: list[Any] = []
    try:
        hypotheses = self_learning.research.get_hypotheses()
    except Exception:
        pass

    experiments: list[dict[str, Any]] = []
    try:
        experiments = self_learning.experiment_engine.list_experiments()
    except Exception:
        pass

    tasks: list[Any] = []
    try:
        tasks = self_learning.meta.get_tasks()
    except Exception:
        pass

    # A/B comparisons = self-learning history (parent vs candidate)
    history = self_learning.get_history(limit=100)
    comparisons = []
    for h in history:
        comparisons.append({
            "iteration": h.get("iteration"),
            "at": h.get("at"),
            "hypothesis_id": h.get("hypothesis_id"),
            "mutation_type": h.get("mutation_type"),
            "parent_pf": h.get("parent_pf"),
            "cand_pf": h.get("cand_pf"),
            "parent_trades": h.get("parent_trades"),
            "cand_trades": h.get("cand_trades"),
            "delta_pf": (
                round(h["cand_pf"] - h["parent_pf"], 4)
                if h.get("cand_pf") is not None and h.get("parent_pf") is not None
                else None
            ),
            "improved": h.get("improved"),
            "decision": h.get("decision"),
            "oos_passed": h.get("oos_passed"),
            "duration_s": h.get("duration_s"),
            "error": h.get("error"),
        })

    # Promotion / rejection tally for the summary
    summary = {
        "total_hypotheses": len(hypotheses),
        "open_hypotheses": sum(
            1 for h in hypotheses
            if str(getattr(h, "status", "")) in ("HypothesisStatus.PROPOSED", "proposed")
        ),
        "total_experiments": len(experiments),
        "experiments_completed": sum(
            1 for e in experiments if e.get("status") in ("completed", "passed")
        ),
        "total_tasks": len(tasks),
        "open_tasks": sum(
            1 for t in tasks if str(getattr(t, "status", "")) in ("pending", "ResearchTaskStatus.PENDING")
        ),
        "ab_comparisons": len(comparisons),
        "promotions": self_learning.state.promotions,
        "rejections": self_learning.state.rejections,
    }

    return _jsonable({
        "hypotheses": hypotheses,
        "experiments": experiments,
        "tasks": tasks,
        "comparisons": comparisons,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ------------------------------------------------------------------
# System: health, latency, db sizes, error rates
# ------------------------------------------------------------------
@router.get("/system")
def system_data() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent  # trading_ai/
    databases = []
    total_bytes = 0
    for db_path in sorted(root.glob("*.db")):
        try:
            size = db_path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        databases.append({
            "name": db_path.name,
            "size_kb": round(size / 1024, 1),
            "modified": datetime.fromtimestamp(
                db_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        })

    health: dict[str, Any] = {}
    if health_monitor is not None:
        try:
            health = health_monitor.get_status()
        except Exception:
            pass

    # Latency per component from health check
    latencies = {}
    for name, comp in (health.get("components") or {}).items():
        latencies[name] = comp.get("latency_ms")

    sl_errors = 0
    sl_running = False
    safety: dict[str, Any] = {}
    if self_learning is not None:
        try:
            st = self_learning.get_status()
            sl_errors = st.get("errors", 0)
            sl_running = st.get("running", False)
            safety = st.get("safety", {})
        except Exception:
            pass

    return _jsonable({
        "health": health,
        "latencies_ms": latencies,
        "databases": databases,
        "db_total_mb": round(total_bytes / (1024 * 1024), 2),
        "self_learning": {
            "running": sl_running,
            "errors": sl_errors,
            "safety": safety,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ------------------------------------------------------------------
# Strategy Lab: population, genomes, lineage, mutation credits, history
# ------------------------------------------------------------------
@router.get("/strategy")
def strategy_data() -> dict[str, Any]:
    if self_learning is None:
        return {"error": "self_learning_not_ready"}

    population = []
    for genome, metrics in getattr(self_learning, "_population", []):
        try:
            g = json.loads(genome.to_json())
        except Exception:
            g = {"strategy_id": getattr(genome, "strategy_id", "?")}
        population.append({"genome": g, "metrics": _jsonable(metrics)})

    lineage: list[Any] = []
    try:
        lineage = self_learning.lineage.get_all_versions()
    except Exception:
        pass

    credits = self_learning.get_mutation_credits()
    history = self_learning.get_history(limit=100)
    status = self_learning.get_status()

    return _jsonable({
        "population": population,
        "lineage": lineage,
        "credits": credits,
        "history": history,
        "status": {
            "running": status.get("running"),
            "phase": (status.get("safety") or {}).get("phase"),
            "iterations": status.get("iterations_completed"),
            "promotions": status.get("promotions"),
            "rejections": status.get("rejections"),
            "errors": status.get("errors"),
            "best_genome_id": status.get("best_genome_id"),
            "best_metrics": status.get("best_metrics"),
            "population_size": status.get("population_size"),
            "safety": status.get("safety"),
            "deployment": status.get("deployment"),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
