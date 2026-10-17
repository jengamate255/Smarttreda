#!/usr/bin/env python3
"""Paper trading journal helper for Boom/Crash spike_drift trades."""
import csv
import json
from datetime import datetime
from pathlib import Path

JOURNAL_FILE = Path(__file__).parent / "boom_crash_journal.csv"
STATS_FILE = Path(__file__).parent / "boom_crash_stats.json"

COLUMNS = [
    "date", "time", "symbol", "action", "lots", "entry_price",
    "sl_price", "tp_price", "sl_pips", "tp_pips", "rr_ratio",
    "risk_amount", "pnl", "pnl_pips", "exit_time", "exit_price",
    "exit_reason", "setup", "session", "h1_trend", "h1_rsi",
    "spike_detected", "confirmation", "notes"
]


def record_trade(trade: dict):
    """Append a trade to the journal CSV."""
    now = datetime.utcnow()
    row = {col: "" for col in COLUMNS}
    row["date"] = now.strftime("%Y-%m-%d")
    row["time"] = now.strftime("%H:%M")
    row["setup"] = "boom_crash_spike_drift"
    row.update(trade)

    write_header = not JOURNAL_FILE.exists() or JOURNAL_FILE.stat().st_size == 0
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    print(f"Trade recorded: {trade.get('symbol')} {trade.get('action')} @ {trade.get('entry_price')}")


def update_stats(pnl: float, win: bool, session: str = ""):
    """Update running stats in boom_crash_stats.json."""
    if STATS_FILE.exists():
        stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    else:
        stats = {"stats": {}, "trade_log": []}

    s = stats.setdefault("stats", {})
    s["total_trades"] = s.get("total_trades", 0) + 1
    if win:
        s["wins"] = s.get("wins", 0) + 1
    else:
        s["losses"] = s.get("losses", 0) + 1
    s["win_rate"] = s["wins"] / s["total_trades"] if s["total_trades"] else 0
    s["total_pnl"] = round(s.get("total_pnl", 0) + pnl, 2)

    log = stats.setdefault("trade_log", [])
    log.append({
        "time": datetime.utcnow().isoformat(),
        "pnl": pnl,
        "win": win,
        "session": session
    })

    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"Stats updated: {s['total_trades']} trades, {s['win_rate']:.1%} WR, ${s['total_pnl']:.2f} PnL")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python boom_crash_journal.py record|stats")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "stats" and STATS_FILE.exists():
        print(STATS_FILE.read_text(encoding="utf-8"))
    elif cmd == "record":
        print("Use record_trade() from code or import this module.")
    else:
        print(f"Unknown command: {cmd}")
