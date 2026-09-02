"""Research / AI Coach / learning-stats API routes (extracted from web_dashboard.py).

Endpoints:
    GET  /api/research-report          - periodic research report (N days)
    GET  /api/weekly-research-report   - standard 7-day report
    GET  /api/coach-review/latest      - post-trade review of most recent trade
    GET  /api/coach-review/{ticket}    - post-trade review by ticket
    GET  /api/sessions                 - best sessions/setups/pairs
    POST /api/ml-rank/retrain          - retrain the ML signal ranker
    GET  /api/ml-rank/info             - current model info
    GET  /api/pattern-win-rates        - win rate by setup/pattern type
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from memory.learning import best_pairs, best_sessions, best_setups
from mt5_mcp.coach import coach_review
from mt5_mcp.research import report as research_report
from mt5_mcp.research import weekly_report

logger = logging.getLogger("web_dashboard")

router = APIRouter()


@router.get("/api/research-report")
def api_research_report(days: int = 7, min_trades: int = 1, save: bool = True):
    """Periodic research report: stats + data-driven suggestions over the last N days."""
    try:
        return research_report(days=days, min_trades=min_trades, save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research report failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/weekly-research-report")
def api_weekly_research_report(save: bool = True):
    """Standard weekly report (last 7 days), saved to memory/reports/."""
    try:
        return weekly_report(save=save)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly research report failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/coach-review/latest")
def coach_review_latest():
    """Full post-trade review for the most recent closed trade."""
    try:
        return coach_review(None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review latest failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/coach-review/{ticket}")
def coach_review_by_ticket(ticket: int):
    """Full post-trade review for a specific closed trade ticket."""
    try:
        return coach_review(ticket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coach review failed for %s: %s", ticket, exc)
        return {"error": str(exc)}


@router.get("/api/sessions")
def sessions():
    """Best sessions/setups/pairs from the trade journal."""
    try:
        return {
            "sessions": best_sessions(),
            "setups": best_setups(),
            "pairs": best_pairs(),
        }
    except Exception as exc:  # noqa: BLE001 - learning is best-effort
        logger.warning("sessions endpoint failed: %s", exc)
        return {"sessions": [], "setups": [], "pairs": [], "error": str(exc)}


@router.get("/api/session-heatmap")
def session_heatmap(days: int = 120):
    """Hour-by-hour net P&L grid for the session heat-map.

    Returns hourly cells (UTC) with net P&L, trade count and win rate
    from the trade journal. Front-end renders green/red intensity tiles —
    the money windows vs dead zones.
    """
    try:
        rows = best_sessions() or []
        by_hour: dict = {}
        for r in rows:
            try:
                h = int(str(r.get("session", "0")).split(":")[0])
                by_hour[h] = {
                    "hour": h,
                    "label": r.get("session"),
                    "trades": r.get("trades", 0),
                    "win_rate": r.get("win_rate", 0),
                    "net": r.get("net_pnl", 0),
                }
            except (ValueError, TypeError):
                continue
        return {"days": days, "hours": by_hour}
    except Exception as exc:  # noqa: BLE001
        logger.warning("session heatmap failed: %s", exc)
        return {"days": days, "hours": {}, "error": str(exc)}


@router.get("/api/ml-rank/info")
def ml_rank_info():
    """Current ML signal ranker model info: features, weights, trained_at, CV AUC."""
    try:
        from mt5_mcp.ml_ranker import load_model, MODEL_PATH

        if not MODEL_PATH.exists():
            return {"trained": False, "error": "no model file found"}
        model = load_model()
        return {
            "trained": True,
            "features": model.get("features", []),
            "weights": model.get("weights", []),
            "intercept": model.get("intercept", 0),
            "n_trades": model.get("n_trades", 0),
            "cv_auc": model.get("cv_auc", 0),
            "trained_at": model.get("trained_at", ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml-rank info failed: %s", exc)
        return {"error": str(exc)}


@router.post("/api/ml-rank/retrain")
def ml_rank_retrain():
    """Retrain the ML signal ranker from backtest results."""
    try:
        from pathlib import Path
        from mt5_mcp.ml_ranker import load_result_trades, build_dataset, train_and_evaluate, fit_and_save, MODEL_PATH

        results_dir = Path(__file__).resolve().parent / "mt5_mcp" / "backtest" / "results"
        if not results_dir.exists():
            return {"error": "no backtest results directory found"}

        t0 = time.time()
        trades = load_result_trades(results_dir)
        if not trades:
            return {"error": "no backtest trades found in results directory"}

        X, y, meta = build_dataset(trades)
        if len(set(y)) < 2 or len(y) < 20:
            return {"error": f"not enough data: {len(y)} usable trades, {len(set(y))} classes (need 20+ with both wins and losses)"}

        metrics = train_and_evaluate(X, y)
        payload = fit_and_save(X, y, MODEL_PATH, cv_auc=metrics["cv_auc"])
        elapsed = round(time.time() - t0, 2)

        return {
            "ok": True,
            "elapsed_s": elapsed,
            "n_trades": len(y),
            "base_win_rate": metrics["base_win_rate"],
            "cv_accuracy": metrics["cv_accuracy"],
            "cv_auc": metrics["cv_auc"],
            "cv_win_rate_above_0_5": metrics["cv_win_rate_above_0_5"],
            "features": payload["features"],
            "weights": payload["weights"],
            "trained_at": payload["trained_at"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml-rank retrain failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/pattern-win-rates")
def pattern_win_rates():
    """Win rate and PnL breakdown by setup/pattern type from the journal."""
    try:
        from mt5_mcp.memory.trade_journal import get_journal

        journal = get_journal()
        trades = journal.list_trades(limit=5000)
        patterns: dict[str, list] = {}
        for t in trades:
            setup = t.get("setup") or "manual"
            if setup not in patterns:
                patterns[setup] = []
            pnl = t.get("pnl")
            if pnl is not None:
                patterns[setup].append(pnl)

        result = []
        for setup, pnls in sorted(patterns.items()):
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            total = sum(pnls)
            result.append({
                "setup": setup,
                "trades": len(pnls),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
                "total_pnl": round(total, 2),
                "avg_pnl": round(total / len(pnls), 2) if pnls else 0,
                "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            })
        return {"patterns": result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pattern-win-rates failed: %s", exc)
        return {"patterns": [], "error": str(exc)}


@router.get("/api/research/monte-carlo")
def monte_carlo(symbol: str = "", simulations: int = 1000):
    """Run Monte Carlo simulation on backtest or journal trades."""
    try:
        from monte_carlo import build_day_pool, simulate_month
        from mt5_mcp.memory.trade_journal import get_journal

        # Try journal trades first
        journal = get_journal()
        trades = journal.list_trades(limit=500)
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]

        if not trades:
            return {"error": "No trades found for Monte Carlo"}

        # Extract PnL values
        pnls = [t.get("pnl", 0) for t in trades if t.get("pnl") is not None]
        if not pnls:
            return {"error": "No PnL data found"}

        # Run simulations
        results = []
        for _ in range(min(simulations, 2000)):
            # Sample with replacement
            sample = [pnls[i % len(pnls)] for i in range(len(pnls))]
            import random
            random.shuffle(sample)
            cumulative = 0
            curve = [10000]
            for p in sample:
                cumulative += p
                curve.append(10000 + cumulative)
            results.append(curve)

        # Stats
        finals = [r[-1] for r in results]
        positives = sum(1 for f in finals if f > 10000)
        blown = sum(1 for f in finals if f < 5000)

        return {
            "simulations": len(results),
            "avg_return": round(sum(finals) / len(finals) - 10000, 2),
            "p_positive": round(positives / len(finals) * 100, 1),
            "p_blown": round(blown / len(finals) * 100, 1),
            "worst_case": round(min(finals) - 10000, 2),
            "best_case": round(max(finals) - 10000, 2),
            "curves": [r[:100] for r in results[:200]],  # first 100 bars for chart
            "finals": sorted(finals),
        }
    except Exception as exc:
        logger.warning("monte-carlo failed: %s", exc)
        return {"error": str(exc)}
