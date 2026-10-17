"""News / Risk / Alerts / Compliance API routes (extracted from web_dashboard.py).

Endpoints:
    GET  /api/news                      - economic calendar
    GET  /api/risk                      - risk manager state
    GET  /api/alerts                    - list alerts
    POST /api/alerts                    - create alert
    DELETE /api/alerts/{id}             - delete alert
    POST /api/alerts/{id}/toggle        - toggle alert active state
    GET  /api/alerts/check              - evaluate alerts against live prices
    GET  /api/compliance                - compliance dashboard data

Dashboard coupling (connection guard + tick cache) injected via configure().
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("web_dashboard")

router = APIRouter()

_require_connected: Optional[Callable[[], None]] = None
_tick_quote_fn: Optional[Callable] = None


def configure(require_connected: Callable[[], None], tick_quote: Callable) -> None:
    """Called once by web_dashboard at startup to inject shared helpers."""
    global _require_connected, _tick_quote_fn
    _require_connected = require_connected
    _tick_quote_fn = tick_quote


def _guard() -> None:
    if _require_connected:
        _require_connected()


@router.get("/api/news")
def news(horizon_hours: int = Query(24, ge=1, le=72)):
    """Upcoming economic events from the ForexFactory weekly calendar."""
    try:
        from tools.news import news_filter

        report = news_filter(horizon_hours=horizon_hours)
        events = [
            {
                "time": e.time.strftime("%Y-%m-%d %H:%M"),
                "currency": e.currency,
                "impact": e.impact,
                "title": e.title,
                "forecast": e.forecast,
                "previous": e.previous,
            }
            for e in report["events"]
        ]
        return {
            "provider": report["provider"],
            "events": events,
            "high_impact_count": report["high_impact_count"],
            "high_impact_imminent": report["high_impact_imminent"],
            "trading_blocked": report["trading_blocked"],
            "imminent_events": [
                {"currency": e.currency, "title": e.title}
                for e in report["imminent_events"]
            ],
        }
    except Exception as exc:  # noqa: BLE001 - news is best-effort
        logger.warning("news endpoint failed: %s", exc)
        return {"events": [], "high_impact_imminent": False, "trading_blocked": False, "error": str(exc)}


@router.get("/api/risk")
def risk():
    """Daily/weekly loss usage, open risk and exposure from the risk manager."""
    _guard()
    try:
        from tools.risk import risk_report

        report = risk_report()
        # Correlation risk: group open positions by correlated pairs
        try:
            from mt5_mcp.client import get_client
            client = get_client()
            client.ensure_connected()
            positions = client.positions()
            # Correlation groups (same currency basket)
            corr_groups = {
                "EURX": ["EURUSD", "EURJPY", "EURGBP"],
                "USDJPX": ["USDJPY", "EURJPY", "JP225"],
                "XAUAG": ["XAUUSD", "XAGUSD"],
                "SPXNDX": ["SPX500", "NAS100", "US30"],
                "BTCETH": ["BTCUSD", "ETHUSD"],
                "GBPX": ["GBPUSD", "GBPJPY"],
            }
            group_risk: dict[str, float] = {}
            for pos in positions:
                sym = pos.get("symbol", "")
                risk_val = abs(float(pos.get("volume", 0)) * float(pos.get("sl_pips", 0) or 0))
                for gname, syms in corr_groups.items():
                    if sym in syms:
                        group_risk[gname] = group_risk.get(gname, 0) + risk_val
            report["correlation_groups"] = {
                k: round(v, 2) for k, v in group_risk.items() if v > 0
            }
        except Exception as exc:  # noqa: BLE001 - correlation is best-effort
            logger.debug("correlation risk merge failed: %s", exc)
            report["correlation_groups"] = {}

        # Merge the watcher compliance state (mode, trade slots) so the UI
        # can show "slots left today" next to the loss buffers.
        try:
            from mt5_mcp.compliance import evaluate as compliance_evaluate

            comp = compliance_evaluate()
            report.update(
                {
                    "mode": comp.get("mode"),
                    "trades_today": comp.get("trades_today", 0),
                    "max_trades_per_day": comp.get("max_trades_per_day", 0),
                    "max_trades_per_day_base": comp.get(
                        "max_trades_per_day_base",
                        comp.get("max_trades_per_day", 0),
                    ),
                    "max_trades_per_day_green_bonus": comp.get(
                        "max_trades_per_day_green_bonus", 0
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - compliance is best-effort
            logger.debug("compliance merge failed: %s", exc)
        return report
    except Exception as exc:  # noqa: BLE001 - risk is best-effort
        logger.warning("risk endpoint failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/alerts")
def get_alerts(include_triggered: bool = Query(True), include_prices: bool = Query(True)):
    """Get all configured price/signal alerts, annotated with the live price."""
    try:
        from mt5_mcp.alerts import list_alerts

        alerts = list_alerts(include_triggered=include_triggered)
        # Annotate each alert with the current top-of-book price so the UI can
        # show live distance-to-trigger without extra per-symbol quote calls.
        if include_prices and alerts and _tick_quote_fn:
            for a in alerts:
                try:
                    q = _tick_quote_fn(a["symbol"])
                    a["current_price"] = float(q["bid"]) if q and q.get("bid") else None
                except Exception:  # noqa: BLE001 - quote is best-effort
                    a["current_price"] = None
        return {"alerts": alerts}
    except Exception as exc:
        logger.warning("get alerts failed: %s", exc)
        return {"error": str(exc)}


@router.post("/api/alerts")
def create_alert(payload: dict):
    """Create a new price/signal alert."""
    try:
        from mt5_mcp.alerts import create_alert as _create_alert

        required = ["symbol", "price", "condition"]
        for field in required:
            if field not in payload:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        alert = _create_alert(
            symbol=payload["symbol"],
            price=float(payload["price"]),
            condition=payload["condition"],
            name=payload.get("name", ""),
        )
        return {"ok": True, "alert": alert}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("create alert failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: str):
    """Delete an alert."""
    try:
        from mt5_mcp.alerts import delete_alert as _delete_alert

        ok = _delete_alert(alert_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("delete alert failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/alerts/{alert_id}/toggle")
def toggle_alert(alert_id: str):
    """Toggle an alert's active state."""
    try:
        from mt5_mcp.alerts import toggle_alert as _toggle_alert

        alert = _toggle_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
        return {"ok": True, "alert": alert}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("toggle alert failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/alerts/check")
async def check_alerts():
    """Check all alerts against current prices (for WebSocket push)."""
    try:
        from mt5_mcp.alerts import check_alerts as _check_alerts

        fired = _check_alerts(_tick_quote_fn or (lambda s: None))
        return {"triggered": fired}
    except Exception as exc:
        logger.warning("check alerts failed: %s", exc)
        return {"error": str(exc)}


@router.get("/api/compliance")
def compliance():
    """Dedicated compliance dashboard: mode, drawdown gauge, daily loss, trade slots, DD gauge."""
    _guard()
    try:
        import json

        from mt5_mcp.client import get_client
        from mt5_mcp.compliance import evaluate as compliance_evaluate
        from mt5_mcp.config import get_settings

        comp = compliance_evaluate()
        client = get_client()
        client.ensure_connected()

        account = client.account_info()
        equity = float(account.get("equity", 0.0))
        balance = float(account.get("balance", 0.0))

        # Get compliance state file for peak equity
        state_path = Path(__file__).resolve().parent / "mt5_mcp" / "memory" / "compliance_state.json"
        peak_equity = 0
        if state_path.exists():
            try:
                with open(state_path) as f:
                    state_data = json.load(f)
                    peak_equity = state_data.get("peak_equity", 0)
            except Exception:
                pass

        dd_pct = comp.get("dd_pct", 0) or 0
        max_dd = comp.get("max_drawdown", 0) or 0

        settings = get_settings().risk
        halt_threshold = max_dd * 0.95

        return {
            "mode": comp.get("mode", "normal"),
            "halt_reason": comp.get("halt_reason", ""),
            "equity": equity,
            "balance": balance,
            "daily_pnl": comp.get("daily_pnl", 0),
            "daily_used_pct": round(comp.get("daily_used", 0) * 100, 1),
            "dd_pct": round(dd_pct, 2),
            "peak_equity": peak_equity or comp.get("peak_equity", 0),
            "drawdown_pct": round(dd_pct, 2),
            "max_drawdown_pct": max_dd,
            "halt_threshold_pct": round(halt_threshold, 2),
            "dd_gauge_pct": min(100, round(dd_pct / max(0.01, halt_threshold) * 100, 1)),
            "max_daily_loss_pct": comp.get("max_daily_loss", 0),
            "trades_today": comp.get("trades_today", 0),
            "max_trades": comp.get("max_trades_per_day", 0),
            "trades_remaining": max(0, comp.get("max_trades_per_day", 0) - comp.get("trades_today", 0)),
            "green_bonus": comp.get("max_trades_per_day_green_bonus", 0),
            "mode_color": {"normal": "#4caf50", "conservative": "#ff9800", "halted": "#f44336"}.get(comp.get("mode", "normal"), "#9e9e9e"),
        }
    except Exception as exc:
        logger.warning("compliance endpoint failed: %s", exc)
        return {"error": str(exc)}
