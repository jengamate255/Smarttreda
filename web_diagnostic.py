"""Diagnostic Dashboard API — pipeline health and per-symbol gate traces."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/diagnostic", tags=["diagnostic"])

logger = logging.getLogger("web_diagnostic")

SETTINGS_PATH = Path(__file__).parent / "mt5_mcp" / "settings.json"
CONSENSUS_PATH = Path(__file__).parent / "mt5_mcp" / "memory" / "consensus.json"


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("cannot read settings.json: %s", exc)
        return {}


def _load_consensus_board() -> dict:
    try:
        return json.loads(CONSENSUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"votes": {}, "blocked": []}


ENGINE_SYMBOLS = {
    "desk": [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "CADCHF",
        "AUDNZD", "EURGBP", "GBPJPY", "NZDJPY", "USDCHF", "EURJPY",
        "CHFJPY", "AUDJPY", "GBPCHF", "XAUUSD", "XAGUSD", "SPX500",
        "JP225", "ETHUSD", "BTCUSD",
    ],
    "confirm": ["AUDNZD", "USDJPY", "USDCAD", "NZDCAD", "EURUSD", "JP225"],
    "breakout": [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
        "USDCHF", "EURJPY", "GBPJPY",
    ],
}


def _all_engine_symbols() -> set[str]:
    all_syms: set[str] = set()
    for syms in ENGINE_SYMBOLS.values():
        all_syms.update(syms)
    return all_syms


@router.get("/learning")
def diagnostic_learning() -> dict[str, Any]:
    from mt5_mcp.learning_loop import learning_snapshot

    return learning_snapshot()


@router.get("/quality")
def diagnostic_quality() -> dict[str, Any]:
    from mt5_mcp.config import get_settings
    from mt5_mcp.paper_learning import snapshot

    quality = get_settings().quality
    return {"enabled": quality.enabled, "entry_timeframe": quality.entry_timeframe,
            "max_entry_drift_atr": quality.max_entry_drift_atr,
            "max_zone_distance_atr": quality.max_zone_distance_atr,
            "ml_role": "advisory", "paper_min_trades": quality.shadow_min_trades,
            "paper_min_profit_factor": quality.shadow_min_profit_factor,
            "paper_min_expectancy_r": quality.shadow_min_expectancy_r,
            "shadow": snapshot() if quality.enabled else None}


@router.get("/runtime")
def diagnostic_runtime() -> dict[str, Any]:
    """Sanitized runtime telemetry; never returns credentials or changes policy."""
    from mt5_mcp.config import get_settings
    from mt5_mcp.consensus import _cfg
    from mt5_mcp.compliance import evaluate

    risk = get_settings().risk
    consensus = _cfg()  # Consensus config is hot-read by the executors.
    disk = _load_settings().get("risk", {})
    keys = ("max_risk_percent", "max_daily_loss_percent", "max_trades_per_day",
            "confidence_floor_percent", "reject_orders", "blocked_symbols", "max_spread_points", "max_spread_relative",
            "blocked_setup_prefixes", "allowed_hours_utc")
    drift = [key for key in keys if key in disk and disk[key] != getattr(risk, key, None)]
    try:
        comp = evaluate()
        # Explicit allowlist protects this read-only endpoint from future additions.
        compliance = {key: comp.get(key) for key in (
            "mode", "halt_reason", "daily_pnl", "daily_limit", "remaining_daily_buffer",
            "open_risk", "max_open_risk", "remaining_drawdown_buffer",
            "overall_drawdown_percent", "trades_today", "max_trades_per_day",
            "per_trade_risk_cap_percent", "news_blocked", "news_blocked_reason",
            "news_stale", "trading_allowed", "warnings", "performance_risk", "prop_risk")}
    except Exception as exc:
        logger.warning("runtime compliance unavailable: %s", exc)
        compliance = {"error": "Compliance telemetry unavailable"}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "desk": {"mode": os.getenv("MT5_DESK_MODE", "SIM").upper(),
                 "enabled": os.getenv("MT5_DESK_ENABLED", "false").lower() == "true"},
        "risk": {key: getattr(risk, key, None) for key in keys},
        "quality": {"enabled":bool(getattr(getattr(get_settings(),"quality",None),"enabled",False)),"ml_role":"advisory"},
        "consensus": {key: consensus.get(key) for key in (
            "enabled", "allowed_symbols", "risk_percent", "veto_score",
            "min_agree", "agree_min_score", "vote_ttl_sec")},
        "settings_drift": drift,
        "policy_scope": "Dashboard process; executor processes may retain cached settings",
        "compliance": compliance,
    }


@router.get("/summary")
def diagnostic_summary() -> dict[str, Any]:
    """High-level system health: config conflicts, gate pass rates, consensus state."""
    settings = _load_settings()
    risk_cfg = settings.get("risk", {})
    strat_cfg = settings.get("strategy", {})
    consensus_cfg = settings.get("consensus", {})

    blocked = set(risk_cfg.get("blocked_symbols", []))
    allowed = set(s.upper() for s in consensus_cfg.get("allowed_symbols", []))
    all_engine = _all_engine_symbols()

    conflicts = sorted(all_engine & blocked)
    missing = sorted(all_engine - allowed)
    tradable = sorted(all_engine - blocked - (all_engine - allowed))

    board = _load_consensus_board()
    votes = board.get("votes", {})
    now = time.time()
    ttl = float(consensus_cfg.get("vote_ttl_sec", 1800))
    fresh_votes = 0
    stale_votes = 0
    for sym, src_votes in votes.items():
        for src, v in (src_votes or {}).items():
            if now - float(v.get("ts", 0)) <= ttl:
                fresh_votes += 1
            else:
                stale_votes += 1

    blocked_attempts = board.get("blocked", [])[-10:]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "risk_percent": risk_cfg.get("default_risk_percent", 0.5),
            "conservative_risk_percent": risk_cfg.get("conservative_risk_percent", 0.5),
            "min_risk_reward": risk_cfg.get("min_risk_reward", 2.0),
            "confidence_floor": risk_cfg.get("confidence_floor_percent", 60),
            "min_zone_score": strat_cfg.get("min_zone_score", 60),
            "min_trade_score": strat_cfg.get("min_trade_score", 60),
            "require_mtf_alignment": strat_cfg.get("require_mtf_alignment", False),
            "max_positions": risk_cfg.get("max_positions", 3),
            "max_trades_per_day": risk_cfg.get("max_trades_per_day", 3),
        },
        "symbols": {
            "total_engine": len(all_engine),
            "blocked_by_compliance": len(conflicts),
            "missing_from_allowlist": len(missing),
            "tradable": len(tradable),
            "conflict_list": conflicts,
            "missing_list": missing,
            "tradable_list": tradable,
        },
        "consensus": {
            "enabled": consensus_cfg.get("enabled", True),
            "fresh_votes": fresh_votes,
            "stale_votes": stale_votes,
            "recent_blocked_attempts": blocked_attempts,
        },
        "health": {
            "config_ok": len(conflicts) == 0 and len(missing) == 0,
            "issues": [],
        },
    }


@router.get("/config")
def diagnostic_config() -> dict[str, Any]:
    """Full config with conflict detection."""
    settings = _load_settings()
    risk_cfg = settings.get("risk", {})
    strat_cfg = settings.get("strategy", {})
    consensus_cfg = settings.get("consensus", {})

    blocked = set(risk_cfg.get("blocked_symbols", []))
    allowed = set(s.upper() for s in consensus_cfg.get("allowed_symbols", []))
    all_engine = _all_engine_symbols()

    conflicts = sorted(all_engine & blocked)
    missing = sorted(all_engine - allowed)

    issues: list[str] = []
    if conflicts:
        issues.append(
            f"COMPLIANCE BLOCK: {', '.join(conflicts)} are blocked but used by engines"
        )
    if missing:
        issues.append(
            f"CONSENSUS MISSING: {', '.join(missing)} used by engines but not in allowlist"
        )
    if risk_cfg.get("conservative_risk_percent", 0.5) < 0.3:
        issues.append("RISK CAP: conservative_risk_percent < 0.3% may reject valid setups")
    if strat_cfg.get("min_zone_score", 60) > 75:
        issues.append("ZONE THRESHOLD: min_zone_score > 75 is very restrictive")
    if strat_cfg.get("require_mtf_alignment", False):
        issues.append("MTF GATE: require_mtf_alignment may filter most setups")

    return {
        "risk": risk_cfg,
        "strategy": strat_cfg,
        "consensus": consensus_cfg,
        "engine_symbols": ENGINE_SYMBOLS,
        "conflicts": conflicts,
        "missing_from_allowlist": missing,
        "issues": issues,
    }


@router.get("/consensus")
def diagnostic_consensus() -> dict[str, Any]:
    """Consensus board state: who voted for what, stale/fresh, blocked attempts."""
    board = _load_consensus_board()
    settings = _load_settings()
    ttl = float(settings.get("consensus", {}).get("vote_ttl_sec", 1800))
    now = time.time()

    votes_by_symbol: dict[str, list[dict]] = {}
    for sym, src_votes in (board.get("votes", {}) or {}).items():
        entries = []
        for src, v in (src_votes or {}).items():
            ts = float(v.get("ts", 0))
            age_sec = now - ts
            entries.append({
                "source": src,
                "side": v.get("side", "NONE"),
                "score": v.get("score", 0),
                "age_sec": round(age_sec),
                "fresh": age_sec <= ttl,
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if ts > 0 else None,
            })
        votes_by_symbol[sym] = entries

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ttl_sec": ttl,
        "votes": votes_by_symbol,
        "blocked_attempts": board.get("blocked", [])[-20:],
    }


@router.get("/symbols")
def diagnostic_symbols(
    symbols: str = Query(default="", description="Comma-separated symbols (empty = all engine symbols)"),
) -> dict[str, Any]:
    """Per-symbol pipeline trace: strategy eval + compliance + consensus gates."""
    if symbols:
        target_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        target_symbols = sorted(_all_engine_symbols())

    settings = _load_settings()
    risk_cfg = settings.get("risk", {})
    consensus_cfg = settings.get("consensus", {})
    blocked_set = set(risk_cfg.get("blocked_symbols", []))
    allowed_set = set(s.upper() for s in consensus_cfg.get("allowed_symbols", []))

    results: list[dict[str, Any]] = []
    for sym in target_symbols:
        engines = [e for e, syms in ENGINE_SYMBOLS.items() if sym in syms]
        gate_trace: dict[str, Any] = {"symbol": sym, "engines": engines}

        is_blocked = sym in blocked_set
        is_allowed = sym in allowed_set or len(allowed_set) == 0

        gate_trace["compliance_blocked"] = is_blocked
        gate_trace["consensus_allowed"] = is_allowed
        gate_trace["can_reach_strategy"] = not is_blocked and is_allowed

        try:
            from mt5_mcp.client import get_client
            client = get_client()
            info = client.symbol_info(sym)
            if info is None:
                gate_trace["mt5_available"] = False
                gate_trace["reason"] = "symbol not found in MT5"
            else:
                gate_trace["mt5_available"] = info.get("visible", False)
                if not info.get("visible", False):
                    gate_trace["reason"] = "symbol exists but not visible in Market Watch"
        except Exception as exc:
            gate_trace["mt5_available"] = False
            gate_trace["reason"] = f"MT5 error: {exc}"

        if gate_trace.get("can_reach_strategy") and gate_trace.get("mt5_available"):
            try:
                from mt5_mcp.strategy import evaluate_strategy
                plan = evaluate_strategy(sym)
                plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
                gate_trace["strategy"] = {
                    "action": plan_dict.get("action", "NONE"),
                    "confidence": plan_dict.get("confidence", 0),
                    "trade_score": plan_dict.get("trade_score", 0),
                    "reason": plan_dict.get("reason", ""),
                    "htf_trend": plan_dict.get("htf_trend", ""),
                    "zone_strength": plan_dict.get("zone_strength", 0),
                    "warnings": plan_dict.get("warnings", []),
                    "errors": plan_dict.get("errors", []),
                }
                gate_trace["would_trade"] = plan_dict.get("action") in ("BUY", "SELL")
            except Exception as exc:
                gate_trace["strategy"] = {"error": str(exc)}
                gate_trace["would_trade"] = False
        else:
            gate_trace["strategy"] = None
            gate_trace["would_trade"] = False
            if not gate_trace.get("reason"):
                if is_blocked:
                    gate_trace["reason"] = "blocked by compliance"
                elif not is_allowed:
                    gate_trace["reason"] = "not in consensus allowlist"

        results.append(gate_trace)

    tradable_count = sum(1 for r in results if r.get("would_trade"))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "tradable_now": tradable_count,
        "symbols": results,
    }
