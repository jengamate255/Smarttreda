"""Learning queries over the trade journal.

Answers questions like:

* "What mistakes am I repeating?"
* "What setups win the most?"
* "Which pairs perform best?"
* "Which sessions perform best?"
"""

from __future__ import annotations

from collections import Counter, defaultdict

from memory.trade_journal import Journal, get_journal


def _split_mistakes(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = str(raw).replace(";", ",").replace("|", ",").split(",")
    return [p.strip().lower() for p in parts if p.strip()]


def repeated_mistakes(limit: int = 10) -> list[dict]:
    journal: Journal = get_journal()
    trades = journal.list_trades(limit=5000)
    counts: Counter = Counter()
    pnl_by_mistake: dict[str, float] = defaultdict(float)
    last_by_mistake: dict[str, str] = {}
    for trade in trades:
        for mistake in _split_mistakes(trade.get("mistakes")):
            counts[mistake] += 1
            pnl_by_mistake[mistake] += float(trade.get("pnl", 0.0) or 0.0)
            close_time = trade.get("close_time") or trade.get("created_at")
            if close_time and close_time > last_by_mistake.get(mistake, ""):
                last_by_mistake[mistake] = close_time
    results = [
        {
            "mistake": mistake,
            "occurrences": count,
            "total_pnl": round(pnl_by_mistake[mistake], 2),
            "last_seen": last_by_mistake.get(mistake),
        }
        for mistake, count in counts.most_common(limit)
    ]
    return results


def best_setups(min_trades: int = 1) -> list[dict]:
    journal: Journal = get_journal()
    trades = journal.list_trades(limit=5000)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get("setup") or "manual")].append(trade)
    results = []
    for setup, items in grouped.items():
        if len(items) < min_trades:
            continue
        wins = [t for t in items if (t.get("pnl") or 0) > 0]
        net = sum(float(t.get("pnl", 0.0) or 0.0) for t in items)
        results.append(
            {
                "setup": setup,
                "trades": len(items),
                "win_rate": round(len(wins) / len(items) * 100.0, 1),
                "net_pnl": round(net, 2),
                "avg_pnl": round(net / len(items), 2),
                "avg_confidence": round(
                    sum(float(t.get("confidence", 0.0) or 0.0) for t in items)
                    / len(items),
                    1,
                ),
            }
        )
    results.sort(key=lambda r: -r["net_pnl"])
    return results


def best_pairs(min_trades: int = 1) -> list[dict]:
    journal: Journal = get_journal()
    trades = journal.list_trades(limit=5000)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get("symbol") or "?").upper()].append(trade)
    results = []
    for symbol, items in grouped.items():
        if len(items) < min_trades:
            continue
        wins = [t for t in items if (t.get("pnl") or 0) > 0]
        net = sum(float(t.get("pnl", 0.0) or 0.0) for t in items)
        results.append(
            {
                "symbol": symbol,
                "trades": len(items),
                "win_rate": round(len(wins) / len(items) * 100.0, 1),
                "net_pnl": round(net, 2),
                "avg_pnl": round(net / len(items), 2),
            }
        )
    results.sort(key=lambda r: -r["net_pnl"])
    return results


def best_sessions() -> list[dict]:
    journal: Journal = get_journal()
    trades = journal.list_trades(limit=5000)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        hour = trade.get("session_hour")
        if hour is None:
            continue
        grouped[f"{int(hour):02d}:00 UTC"].append(trade)
    results = []
    for hour, items in grouped.items():
        wins = [t for t in items if (t.get("pnl") or 0) > 0]
        net = sum(float(t.get("pnl", 0.0) or 0.0) for t in items)
        results.append(
            {
                "session": hour,
                "trades": len(items),
                "win_rate": round(len(wins) / len(items) * 100.0, 1),
                "net_pnl": round(net, 2),
            }
        )
    results.sort(key=lambda r: -r["net_pnl"])
    return results


def learning_report() -> dict:
    return {
        "repeated_mistakes": repeated_mistakes(),
        "best_setups": best_setups(),
        "best_pairs": best_pairs(),
        "best_sessions": best_sessions(),
    }
