"""Backtest-to-live divergence monitor.

Compares live trading performance (from journal) against backtest expectations
(from multi_strategy.py BACKTEST_PF). Sends a Telegram alert when live metrics
fall significantly below backtest benchmarks.

Trigger conditions (either):
  - live win rate < backtest win rate * 0.8
  - live profit factor < backtest PF * 0.8

Usage:
    from mt5_mcp.divergence_monitor import check_divergence
    result = check_divergence()   # returns dict with status + alerts
"""

from __future__ import annotations

import logging
from typing import Any

from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.multi_strategy import BACKTEST_PF, STRATEGY_ROUTING
from mt5_mcp.notify import notify

logger = logging.getLogger("mt5_mcp.divergence_monitor")

# Minimum trades before divergence checks are meaningful
MIN_TRADES_FOR_CHECK = 10

# Threshold: alert when live metric < backtest * this factor
DIVERGENCE_THRESHOLD = 0.8


def _get_recent_trades(limit: int = 30) -> list[dict]:
    """Fetch the most recent closed trades from the journal."""
    journal = get_journal()
    return journal.list_trades(limit=limit, outcome="")


def _calc_live_metrics(trades: list[dict]) -> dict[str, Any]:
    """Calculate live win rate and PF from a list of trades."""
    if not trades:
        return {"win_rate": 0.0, "pf": 0.0, "n": 0, "wins": 0, "losses": 0,
                "gross_win": 0.0, "gross_loss": 0.0}

    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) < 0]
    gross_win = sum(float(t.get("pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("pnl") or 0) for t in losses))

    n = len(trades)
    win_rate = len(wins) / n * 100 if n else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    return {
        "win_rate": round(win_rate, 1),
        "pf": round(pf, 2),
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
    }


def _get_backtest_expectations(trades: list[dict]) -> dict[str, Any]:
    """Look up the backtest PF for the strategies used in these trades.

    Maps each trade's setup (strategy name) and symbol to the BACKTEST_PF
    dictionary. Returns weighted average backtest expectations.
    """
    pf_values = []
    for t in trades:
        symbol = (t.get("symbol") or "").upper()
        setup = t.get("setup") or ""

        # Direct lookup: (symbol, setup) in BACKTEST_PF
        key = (symbol, setup)
        if key in BACKTEST_PF:
            pf_values.append(BACKTEST_PF[key])
            continue

        # Try routed strategy for this symbol
        routed = STRATEGY_ROUTING.get(symbol)
        if routed:
            routed_key = (symbol, routed)
            if routed_key in BACKTEST_PF:
                pf_values.append(BACKTEST_PF[routed_key])
                continue

    if not pf_values:
        return {"avg_bt_pf": 0.0, "avg_bt_wr": 0.0, "n_matched": 0}

    avg_pf = sum(pf_values) / len(pf_values)
    # Rough backtest win rate estimate from PF:
    # For a system with avg RR ~2.0, WR ~ PF/(PF+1) * 100 is a rough proxy
    # But we use a simpler heuristic: assume avg RR ~2.0
    # PF = (WR * avg_win) / ((1-WR) * avg_loss)
    # If avg_win/avg_loss ~ 2, then PF = 2*WR/(1-WR) => WR = PF/(PF+2)
    avg_wr = avg_pf / (avg_pf + 2.0) * 100 if avg_pf > 0 else 0.0

    return {
        "avg_bt_pf": round(avg_pf, 2),
        "avg_bt_wr": round(avg_wr, 1),
        "n_matched": len(pf_values),
    }


def _per_strategy_divergence(trades: list[dict]) -> list[dict]:
    """Check divergence per strategy/symbol combination."""
    # Group trades by (symbol, setup)
    groups: dict[tuple[str, str], list[dict]] = {}
    for t in trades:
        symbol = (t.get("symbol") or "").upper()
        setup = t.get("setup") or "manual"
        key = (symbol, setup)
        groups.setdefault(key, []).append(t)

    alerts = []
    for (symbol, setup), group_trades in groups.items():
        if len(group_trades) < 3:
            continue  # too few trades to draw conclusions

        live = _calc_live_metrics(group_trades)
        bt_key = (symbol, setup)
        bt_pf = BACKTEST_PF.get(bt_key, 0.0)

        if bt_pf <= 0:
            continue

        # Check PF divergence
        if live["pf"] < bt_pf * DIVERGENCE_THRESHOLD and live["n"] >= 5:
            alerts.append({
                "symbol": symbol,
                "setup": setup,
                "type": "profit_factor",
                "live_value": live["pf"],
                "backtest_value": bt_pf,
                "threshold": round(bt_pf * DIVERGENCE_THRESHOLD, 2),
                "trades": live["n"],
                "severity": "high" if live["pf"] < bt_pf * 0.6 else "medium",
            })

    return alerts


def check_divergence(
    limit: int = 30,
    threshold: float = DIVERGENCE_THRESHOLD,
    send_alert: bool = True,
) -> dict[str, Any]:
    """Check if live performance has diverged from backtest expectations.

    Args:
        limit: Number of recent trades to analyze.
        threshold: Alert when live < backtest * threshold (default 0.8).
        send_alert: Whether to send a Telegram alert if divergence detected.

    Returns:
        Dict with status, live metrics, backtest expectations, and any alerts.
    """
    global DIVERGENCE_THRESHOLD
    DIVERGENCE_THRESHOLD = threshold

    trades = _get_recent_trades(limit)
    live = _calc_live_metrics(trades)
    bt = _get_backtest_expectations(trades)
    per_strat = _per_strategy_divergence(trades)

    alerts = []
    overall_status = "ok"

    if live["n"] < MIN_TRADES_FOR_CHECK:
        return {
            "status": "insufficient_data",
            "live": live,
            "backtest": bt,
            "message": f"Only {live['n']} trades (need {MIN_TRADES_FOR_CHECK}+)",
            "alerts": [],
        }

    # Check overall win rate divergence
    if bt["avg_bt_wr"] > 0 and live["win_rate"] < bt["avg_bt_wr"] * threshold:
        alerts.append({
            "type": "win_rate",
            "live": live["win_rate"],
            "expected": bt["avg_bt_wr"],
            "threshold": round(bt["avg_bt_wr"] * threshold, 1),
            "detail": f"Live WR {live['win_rate']:.1f}% < expected {bt['avg_bt_wr']:.1f}% * {threshold}",
        })
        overall_status = "divergence"

    # Check overall PF divergence
    if bt["avg_bt_pf"] > 0 and live["pf"] < bt["avg_bt_pf"] * threshold:
        alerts.append({
            "type": "profit_factor",
            "live": live["pf"],
            "expected": bt["avg_bt_pf"],
            "threshold": round(bt["avg_bt_pf"] * threshold, 2),
            "detail": f"Live PF {live['pf']:.2f} < expected {bt['avg_bt_pf']:.2f} * {threshold}",
        })
        overall_status = "divergence"

    # Add per-strategy alerts
    alerts.extend(per_strat)
    if per_strat:
        overall_status = "divergence"

    result = {
        "status": overall_status,
        "live": live,
        "backtest": bt,
        "alerts": alerts,
        "trades_analyzed": live["n"],
    }

    # Send Telegram alert if divergence detected
    if send_alert and alerts and overall_status == "divergence":
        try:
            html = _format_divergence_alert(result)
            notify(
                "divergence_alert",
                f"Divergence detected: {len(alerts)} alert(s) — "
                f"live WR {live['win_rate']:.1f}%, PF {live['pf']:.2f}",
                force=True,
                html=html,
            )
            logger.info("Divergence alert sent: %d alerts", len(alerts))
        except Exception:
            logger.exception("Failed to send divergence alert")

    return result


def _format_divergence_alert(result: dict) -> str:
    """Format divergence results as an HTML Telegram message."""
    live = result["live"]
    bt = result["backtest"]
    alerts = result["alerts"]

    lines = [
        "<b>\u26a0\ufe0f Backtest-to-Live Divergence Alert</b>",
        "",
        f"<b>Trades analyzed:</b> {result['trades_analyzed']}",
        f"<b>Live Win Rate:</b> {live['win_rate']:.1f}% (expected ~{bt['avg_bt_wr']:.1f}%)",
        f"<b>Live PF:</b> {live['pf']:.2f} (expected ~{bt['avg_bt_pf']:.2f})",
        "",
        "<b>\U0001f6a8 Alerts:</b>",
    ]

    for a in alerts:
        if a.get("symbol"):
            # Per-strategy alert
            severity = "\U0001f534" if a.get("severity") == "high" else "\U0001f7e1"
            lines.append(
                f"  {severity} <b>{a['symbol']}</b> ({a['setup']}): "
                f"PF {a['live_value']:.2f} vs BT {a['backtest_value']:.2f} "
                f"[{a['trades']} trades]"
            )
        else:
            # Overall alert
            lines.append(f"  \u26a0\ufe0f {a['detail']}")

    lines.append("")
    lines.append("<i>Review strategy routing or pause affected symbols.</i>")

    return "\n".join(lines)
