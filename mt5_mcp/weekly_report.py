"""Weekly performance report — Telegram summary of last 7 days of trades.

Queries the SQLite journal for closed trades, calculates aggregate stats
(total P&L, win rate, avg win/loss, largest win/loss, per-strategy breakdown),
formats an HTML Telegram message and sends via notifier.notify().

Scheduled via MT5R_WeeklyReport (weekly, Sunday 20:00).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.notify import notify

logger = logging.getLogger("mt5_mcp.weekly_report")


def _query_weekly_trades(days: int = 7) -> list[dict]:
    """Return all closed trades from the last N days."""
    journal = get_journal()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with journal._lock:
        conn = journal._conn()
        rows = conn.execute(
            "SELECT * FROM trades WHERE close_time >= ? AND pnl IS NOT NULL "
            "ORDER BY close_time ASC",
            [cutoff],
        ).fetchall()
    return [dict(r) for r in rows]


def _calc_stats(trades: list[dict]) -> dict[str, Any]:
    """Calculate aggregate statistics from a list of trades."""
    if not trades:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "profit_factor": 0.0,
            "avg_rr": 0.0,
            "by_strategy": {},
        }

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    wins_pnl = [p for p in pnls if p > 0]
    losses_pnl = [p for p in pnls if p < 0]
    breakeven = sum(1 for p in pnls if p == 0)

    gross_win = sum(wins_pnl)
    gross_loss = abs(sum(losses_pnl))

    rr_values = [float(t["rr"]) for t in trades if t.get("rr") is not None]

    # Per-strategy breakdown
    by_strategy: dict[str, dict] = {}
    for t in trades:
        strat = t.get("setup") or "manual"
        if strat not in by_strategy:
            by_strategy[strat] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "gross_win": 0.0,
                "gross_loss": 0.0,
            }
        s = by_strategy[strat]
        s["trades"] += 1
        pnl = float(t.get("pnl") or 0.0)
        s["pnl"] += pnl
        if pnl > 0:
            s["wins"] += 1
            s["gross_win"] += pnl
        elif pnl < 0:
            s["losses"] += 1
            s["gross_loss"] += abs(pnl)

    for s in by_strategy.values():
        total = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / total * 100, 1) if total else 0.0
        s["pf"] = (
            round(s["gross_win"] / s["gross_loss"], 2)
            if s["gross_loss"] > 0
            else (999.0 if s["gross_win"] > 0 else 0.0)
        )
        s["pnl"] = round(s["pnl"], 2)
        s["gross_win"] = round(s["gross_win"], 2)
        s["gross_loss"] = round(s["gross_loss"], 2)

    n_closed = len(pnls)
    n_wins = len(wins_pnl)
    n_losses = len(losses_pnl)

    return {
        "total_trades": n_closed,
        "total_pnl": round(sum(pnls), 2),
        "wins": n_wins,
        "losses": n_losses,
        "breakeven": breakeven,
        "win_rate": round(n_wins / n_closed * 100, 1) if n_closed else 0.0,
        "avg_win": round(gross_win / n_wins, 2) if n_wins else 0.0,
        "avg_loss": round(gross_loss / n_losses, 2) if n_losses else 0.0,
        "largest_win": round(max(pnls), 2) if pnls else 0.0,
        "largest_loss": round(min(pnls), 2) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (
            999.0 if gross_win > 0 else 0.0
        ),
        "avg_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0,
        "by_strategy": by_strategy,
    }


def _format_html(stats: dict[str, Any], days: int = 7) -> str:
    """Format stats as an HTML Telegram message."""
    if stats["total_trades"] == 0:
        return (
            "<b>\u2b50 Weekly Performance Report</b>\n"
            f"<i>Last {days} days</i>\n\n"
            "\u2796 No closed trades this week."
        )

    pnl_emoji = "\u2705" if stats["total_pnl"] >= 0 else "\u274c"
    pf_str = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] < 900 else "\u221e"

    lines = [
        "<b>\U0001f4ca Weekly Performance Report</b>",
        f"<i>Last {days} days</i>",
        "",
        f"{pnl_emoji} <b>Total P&amp;L:</b> ${stats['total_pnl']:+.2f}",
        f"\u2705 <b>Wins:</b> {stats['wins']}  |  "
        f"\u274c <b>Losses:</b> {stats['losses']}  |  "
        f"\u27a1 <b>BE:</b> {stats['breakeven']}",
        f"\U0001f3af <b>Win Rate:</b> {stats['win_rate']:.1f}%",
        f"\U0001f4c8 <b>Profit Factor:</b> {pf_str}",
        "",
        f"\u2b06 <b>Avg Win:</b> ${stats['avg_win']:.2f}  |  "
        f"\u2b07 <b>Avg Loss:</b> ${stats['avg_loss']:.2f}",
        f"\U0001f525 <b>Largest Win:</b> ${stats['largest_win']:.2f}  |  "
        f"\U0001f4a5 <b>Largest Loss:</b> ${stats['largest_loss']:.2f}",
    ]

    if stats.get("avg_rr"):
        lines.append(f"\U0001f4cf <b>Avg RR:</b> {stats['avg_rr']:.2f}R")

    # Per-strategy breakdown
    by_strat = stats.get("by_strategy", {})
    if by_strat:
        lines.append("")
        lines.append("<b>\U0001f4cb By Strategy:</b>")
        sorted_strats = sorted(by_strat.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for name, s in sorted_strats:
            pf_s = f"{s['pf']:.2f}" if s["pf"] < 900 else "\u221e"
            emoji = "\u2705" if s["pnl"] >= 0 else "\u274c"
            lines.append(
                f"  {emoji} <b>{name}</b>: {s['trades']} trades, "
                f"WR {s['win_rate']:.0f}%, PF {pf_s}, "
                f"P&amp;L ${s['pnl']:+.2f}"
            )

    return "\n".join(lines)


def generate_weekly_report(days: int = 7) -> dict[str, Any]:
    """Generate the weekly report: query trades, compute stats, format HTML.

    Returns a dict with stats and the formatted HTML message.
    """
    trades = _query_weekly_trades(days)
    stats = _calc_stats(trades)
    html = _format_html(stats, days)
    return {"stats": stats, "html": html, "trade_count": len(trades)}


def send_weekly_report(days: int = 7) -> bool:
    """Generate and send the weekly performance report via Telegram.

    Returns True if the notification was delivered.
    """
    try:
        report = generate_weekly_report(days)
        html = report["html"]
        delivered = notify(
            "weekly_report",
            f"Weekly report: {report['stats']['total_trades']} trades, "
            f"P&L ${report['stats']['total_pnl']:+.2f}",
            force=True,
            html=html,
        )
        logger.info(
            "Weekly report sent: %d trades, P&L $%.2f, delivered=%s",
            report["stats"]["total_trades"],
            report["stats"]["total_pnl"],
            delivered,
        )
        return delivered
    except Exception:
        logger.exception("Weekly report failed")
        return False
