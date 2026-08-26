"""Research Agent.

Periodic analysis of all completed trades (intended to run weekly, or on demand
over any window). Produces the research report:

* win rate, expectancy, profit factor, average R multiple
* max drawdown over the window
* best and worst symbols and setups
* most profitable sessions and most common mistakes
* data-driven improvement suggestions - every one backed by the numbers in
  the same report, never by assumption.

Reports are persisted to ``memory/reports/`` as JSON + Markdown so the
trader has a paper trail of what was concluded and when.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.memory.statistics import max_drawdown
from mt5_mcp.memory.trade_journal import Journal, get_journal

logger = logging.getLogger("mt5_mcp.research")

REPORT_DIR = Path(__file__).resolve().parent / "memory" / "reports"

# Baseline for drawdown percentages (mirrors the backtester's $10k default).
ASSUMED_START_BALANCE = 10_000.0


def _within_window(trade: dict, cutoff: datetime) -> bool:
    raw = trade.get("close_time") or trade.get("created_at")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _window_trades(days: int, journal: Journal) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    trades = [
        t
        for t in journal.list_trades(limit=100_000)
        if t.get("pnl") is not None and _within_window(t, cutoff)
    ]
    trades.sort(key=lambda t: t.get("close_time") or t.get("created_at") or "")
    return trades


def _group_stats(trades: list[dict], key: str, min_trades: int = 1) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        value = str(trade.get(key) or "?")
        if key == "symbol":
            value = value.upper()
        grouped[value].append(trade)
    rows: list[dict] = []
    for name, items in grouped.items():
        if len(items) < min_trades:
            continue
        wins = [t for t in items if float(t.get("pnl", 0.0) or 0.0) > 0]
        net = sum(float(t.get("pnl", 0.0) or 0.0) for t in items)
        rows.append(
            {
                "name": name,
                "trades": len(items),
                "win_rate": round(len(wins) / len(items) * 100.0, 1),
                "net_pnl": round(net, 2),
                "avg_pnl": round(net / len(items), 2),
            }
        )
    rows.sort(key=lambda r: -r["net_pnl"])
    return rows


def _window_mistakes(trades: list[dict]) -> list[dict]:
    counts: Counter = Counter()
    for trade in trades:
        raw = trade.get("mistakes")
        if not raw:
            continue
        for part in str(raw).replace(";", ",").replace("|", ",").split(","):
            mistake = part.strip().lower()
            if mistake:
                counts[mistake] += 1
    return [
        {"mistake": name, "occurrences": count}
        for name, count in counts.most_common(10)
    ]


def _suggestions(
    trades: list[dict], stats: dict, symbols: list[dict], setups: list[dict],
    sessions: list[dict], mistakes: list[dict],
) -> list[dict]:
    out: list[dict] = []
    n = stats["trades"]
    if n < 5:
        return out

    def add(severity: str, suggestion: str, support: str, action: str) -> None:
        out.append(
            {"severity": severity, "suggestion": suggestion, "support": support, "action": action}
        )

    if n >= 8 and stats["win_rate"] < 45.0:
        add(
            "high",
            "raise the strategy entry bar",
            f"win rate {stats['win_rate']:.1f}% over {n} trades",
            "increase min_confidence_percent / min_zone_score in settings.json "
            "until win rate recovers above 50%",
        )
    if n >= 5 and stats["profit_factor"] < 1.0:
        add(
            "high",
            "cut position size until profitability returns",
            f"profit factor {stats['profit_factor']:.2f} (below 1.0)",
            "trade at 0.25% risk (conservative mode) until two consecutive "
            "profitable weeks",
        )
    if stats["net_pnl"] < 0:
        add(
            "medium",
            "risk per trade is too high for current edge",
            f"net P&L {stats['net_pnl']:+.2f} over {n} trades",
            "halve default risk to 0.25% until the weekly report turns positive",
        )

    for row in symbols:
        if row["trades"] >= 3 and row["net_pnl"] < 0:
            add(
                "medium",
                f"avoid {row['name']} until conditions improve",
                f"{row['trades']} trades, {row['net_pnl']:+.2f} net, "
                f"{row['win_rate']:.0f}% win rate",
                "remove the symbol from the watchlist or require higher "
                "confidence/zone scores before trading it",
            )
    for row in symbols:
        if row["trades"] >= 3 and row["win_rate"] >= 55.0 and row["net_pnl"] > 0:
            add(
                "low",
                f"concentrate on {row['name']}",
                f"{row['win_rate']:.0f}% win rate, {row['net_pnl']:+.2f} net "
                f"over {row['trades']} trades",
                "keep it on the watchlist and take every A+ setup it offers",
            )

    for row in setups:
        if row["trades"] >= 3 and row["net_pnl"] < 0:
            add(
                "medium",
                f"review or drop the '{row['name']}' setup",
                f"{row['trades']} trades, {row['net_pnl']:+.2f} net, "
                f"{row['win_rate']:.0f}% win rate",
                "stop taking it as-is; add a stronger confirmation filter or "
                "remove it from the setup list",
            )

    for row in sessions:
        if row["trades"] >= 3 and row["net_pnl"] < 0:
            add(
                "medium",
                f"avoid the {row['name']} session",
                f"{row['trades']} trades, {row['net_pnl']:+.2f} net, "
                f"{row['win_rate']:.0f}% win rate",
                "block entries during that UTC hour (session filter in the "
                "strategy or manually)",
            )

    if mistakes:
        top = mistakes[0]
        if top["occurrences"] >= 2:
            add(
                "medium",
                f"stop repeating '{top['mistake']}'",
                f"{top['occurrences']} occurrences in this window",
                "add it to the pre-trade checklist and journal the mistake "
                "every time it happens so the count is tracked",
            )

    if stats["average_rr"] and stats["average_rr"] < 3.0:
        add(
            "low",
            "winners are being cut short",
            f"average realised R multiple {stats['average_rr']:.2f} (target 3.0)",
            "let winners reach the full TP or trail stops; do not close early",
        )

    dd_pct = stats["max_drawdown"].get("max_drawdown_percent", 0.0)
    if dd_pct >= 5.0:
        add(
            "high",
            "drawdown needs protection",
            f"max drawdown {dd_pct:.2f}% in the window",
            "check compliance buffers; consider daily loss halts earlier in "
            "the day",
        )

    return out


def _markdown(report: dict) -> str:
    lines = [
        f"# Research Report - {report['generated_at'][:10]}",
        "",
        f"Window: {report['window_days']} day(s) from {report['window_start'][:10]} "
        f"to {report['window_end'][:10]}",
        "",
        f"- Trades: {report['trades']}",
        f"- Win rate: {report['win_rate']:.1f}%",
        f"- Expectancy: {report['expectancy']:+.2f} per trade",
        f"- Profit factor: {report['profit_factor']:.2f}",
        f"- Average R multiple: {report['average_rr']:.2f}",
        f"- Net P&L: {report['net_pnl']:+.2f}",
        f"- Max drawdown: {report['max_drawdown']['max_drawdown_percent']:.2f}%",
        "",
        "## Best symbols",
    ]
    for row in report["best_symbols"]:
        lines.append(f"- {row['name']}: {row['trades']} trades, {row['win_rate']:.0f}% win, {row['net_pnl']:+.2f}")
    lines += ["", "## Worst symbols"]
    for row in report["worst_symbols"]:
        lines.append(f"- {row['name']}: {row['trades']} trades, {row['win_rate']:.0f}% win, {row['net_pnl']:+.2f}")
    lines += ["", "## Setups"]
    for row in report["setups"]:
        lines.append(f"- {row['name']}: {row['trades']} trades, {row['net_pnl']:+.2f}")
    lines += ["", "## Mistakes"]
    if report["mistakes"]:
        for row in report["mistakes"]:
            lines.append(f"- {row['mistake']} x{row['occurrences']}")
    else:
        lines.append("- none recorded")
    lines += ["", "## Suggestions"]
    if report["suggestions"]:
        for row in report["suggestions"]:
            lines.append(
                f"- [{row['severity']}] {row['suggestion']} - {row['support']} "
                f"(action: {row['action']})"
            )
    else:
        lines.append("- not enough data for statistically significant suggestions")
    return "\n".join(lines) + "\n"


def report(days: int = 7, min_trades: int = 1, save: bool = True) -> dict:
    """Research report over the last `days` of completed trades."""
    journal = get_journal()
    trades = _window_trades(days, journal)
    n = len(trades)
    window_start = (
        trades[0].get("close_time") or trades[0].get("created_at") or ""
        if trades
        else datetime.now(timezone.utc).isoformat()
    )
    window_end = (
        trades[-1].get("close_time") or trades[-1].get("created_at") or ""
        if trades
        else datetime.now(timezone.utc).isoformat()
    )

    stats: dict[str, Any] = {
        "trades": n,
        "win_rate": 0.0,
        "expectancy": 0.0,
        "profit_factor": 0.0,
        "average_rr": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "net_pnl": 0.0,
        "max_drawdown": {"max_drawdown": 0.0, "max_drawdown_percent": 0.0},
    }

    symbols: list[dict] = []
    setups: list[dict] = []
    sessions: list[dict] = []
    mistakes: list[dict] = []
    suggestions: list[dict] = []

    if trades:
        pnls = [float(t.get("pnl", 0.0) or 0.0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        net = sum(pnls)
        rrs = [float(t.get("rr", 0.0) or 0.0) for t in trades if t.get("rr")]

        curve = [{"time": None, "equity": ASSUMED_START_BALANCE}]
        equity = ASSUMED_START_BALANCE
        for trade in trades:
            equity += float(trade.get("pnl", 0.0) or 0.0)
            curve.append(
                {
                    "time": trade.get("close_time") or trade.get("created_at"),
                    "equity": round(equity, 2),
                }
            )

        stats = {
            "trades": n,
            "win_rate": round(len(wins) / n * 100.0, 1),
            "expectancy": round(net / n, 2),
            "profit_factor": round(gross_win / gross_loss, 2)
            if gross_loss
            else (99.99 if gross_win else 0.0),
            "average_rr": round(sum(rrs) / len(rrs), 2) if rrs else 0.0,
            "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "average_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "net_pnl": round(net, 2),
            "max_drawdown": max_drawdown(curve),
        }

        symbols = _group_stats(trades, "symbol", min_trades)
        setups = _group_stats(trades, "setup", min_trades)
        sessions = [
            row
            for row in _group_stats(trades, "session_hour", min_trades)
            if row["name"].isdigit()
        ]
        for row in sessions:
            row["name"] = f"{int(row['name']):02d}:00 UTC"
        mistakes = _window_mistakes(trades)
        suggestions = _suggestions(
            trades, stats, symbols, setups, sessions, mistakes
        )

    generated = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "generated_at": generated,
        "window_days": days,
        "window_start": window_start,
        "window_end": window_end,
        **stats,
        "best_symbols": symbols[:5],
        "worst_symbols": symbols[-3:][::-1],
        "setups": setups[:8],
        "sessions": sessions[:8],
        "mistakes": mistakes[:5],
        "suggestions": suggestions,
    }

    if save:
        result["markdown"] = _markdown(result)
        result["saved_to"] = _save(result)
    return result


def weekly_report(save: bool = True) -> dict:
    """Convenience wrapper: the standard weekly research report."""
    return report(days=7, save=save)


def _save(result: dict, report_dir: str | Path | None = None) -> dict:
    directory = Path(report_dir or REPORT_DIR)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("cannot create report dir %s: %s", directory, exc)
        return {"json": None, "markdown": None, "error": str(exc)}
    stamp = result["generated_at"][:10]
    json_path = directory / f"research_{stamp}.json"
    md_path = directory / f"research_{stamp}.md"
    try:
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        md_path.write_text(result.get("markdown", ""), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(md_path)}
    except OSError as exc:
        logger.warning("cannot save research report: %s", exc)
        return {"json": None, "markdown": None, "error": str(exc)}
