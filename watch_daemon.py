"""Watch-level daemon: alerts the moment a trigger level prints.

The post-mortem of the missed XAUUSD break showed the gap: analysis produced
correct trigger levels, but nothing watched them between manual checks. This
daemon closes that gap.

Design:
* Runs continuously as a background service (scheduled task).
* Polls a JSON watchlist every POLL_SECONDS.
* A watch entry fires when its condition is met (price crosses a level, H1
  candle closes beyond a level, or price enters a zone).
* On fire: writes a journal event, sends a notification (Telegram/email via
  mt5_mcp.notify + Windows toast), and records the trigger in state so it
  doesn't re-fire.
* A "snooze" per entry prevents spam: once fired, the entry is marked and
  only re-armed if the price moves back through (re-cross) or after TTL.

Watchlist file: mt5_mcp/memory/watch_levels.json (hot-editable):
[
  {
    "id": "xau-retest",
    "symbol": "XAUUSD",
    "kind": "zone",            # zone | cross | h1_close
    "lower": 4365.0,           # zone: [lower, upper]; cross/h1_close: level
    "upper": 4385.0,
    "direction": "SELL",       # suggested action
    "note": "broken support -> resistance retest",
    "ttl_minutes": 360
  }
]

Usage:
  python watch_daemon.py            # foreground
  python watch_daemon.py --once     # single poll cycle (task-scheduler mode)
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[0]  # file lives at project root
MODULE_DIR = PROJECT_DIR / "mt5_mcp"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

load_dotenv(PROJECT_DIR / ".env")

from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.notify import notify  # noqa: E402
try:
    from mt5_mcp.notify import fmt_watch_alert  # noqa: E402
except ImportError:
    def fmt_watch_alert(symbol, direction, kind, level, note, price):  # type: ignore
        return f"{symbol} {direction} {kind} {level} price {price} — {note}"

LOG_PATH = PROJECT_DIR / "logs" / "watch_daemon.log"
WATCH_PATH = MODULE_DIR / "memory" / "watch_levels.json"
STATE_PATH = MODULE_DIR / "memory" / "watch_state.json"
POLL_SECONDS = int(__file__ and __import__("os").getenv("WATCH_POLL_SECONDS", "30"))

logger = logging.getLogger("mt5_watchd")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    try:  # console only when run interactively
        if sys.stdout is not None:
            logger.addHandler(logging.StreamHandler())
    except Exception:
        pass


def _load_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _condition_met(entry: dict[str, Any], price: float, h1_close: float | None) -> bool:
    kind = str(entry.get("kind", "cross")).lower()
    if kind == "zone":
        lo = float(entry.get("lower", 0.0))
        hi = float(entry.get("upper", 0.0))
        return lo <= price <= hi
    if kind == "h1_close":
        level = float(entry.get("level", 0.0))
        if h1_close is None or not level:
            return False
        above = bool(entry.get("above", True))
        return h1_close > level if above else h1_close < level
    # cross
    level = float(entry.get("level", 0.0))
    if not level:
        return False
    return price >= level if bool(entry.get("above", True)) else price <= level


def _format_alert(entry: dict[str, Any], price: float) -> str:
    action = entry.get("direction", "?")
    note = entry.get("note", "")
    sym = entry.get("symbol", "?")
    kind = entry.get("kind", "cross")
    where = (
        f"{entry['lower']}-{entry['upper']}" if kind == "zone"
        else str(entry.get("level", "?"))
    )
    return f"{sym} {action} trigger: price {price} at {kind} {where} — {note}"


def _format_alert_html(entry: dict[str, Any], price: float) -> str:
    action = entry.get("direction", "?")
    note = entry.get("note", "")
    sym = entry.get("symbol", "?")
    kind = entry.get("kind", "cross")
    level = (
        f"{entry['lower']}-{entry['upper']}" if kind == "zone"
        else str(entry.get("level", "?"))
    )
    return fmt_watch_alert(
        symbol=sym, direction=action, kind=kind,
        level=level, note=note, price=price,
    )


def poll_once(client) -> int:
    """One poll cycle. Returns number of alerts fired."""
    watch = _load_json(WATCH_PATH, [])
    if not isinstance(watch, list) or not watch:
        return 0
    state = _load_json(STATE_PATH, {})
    fired: dict[str, Any] = state.get("fired", {})

    # group symbols, fetch each tick once
    symbols = sorted({str(w.get("symbol", "")) for w in watch if w.get("symbol")})
    ticks: dict[str, float] = {}
    closes: dict[str, float] = {}
    for sym in symbols:
        try:
            t = client.tick(sym)
            if t and t.get("bid"):
                ticks[sym] = float(t["bid"])
        except Exception as exc:
            logger.warning("tick %s failed: %s", sym, exc)

    alerts = 0
    now = time.time()
    changed = False
    for entry in watch:
        eid = str(entry.get("id", ""))
        sym = str(entry.get("symbol", ""))
        if not eid or sym not in ticks:
            continue
        price = ticks[sym]

        # fresh H1 close only for h1_close entries
        h1_close: float | None = None
        if str(entry.get("kind")) == "h1_close":
            try:
                df = client.rates(sym, "H1", 2)
                if df is not None and len(df) >= 2:
                    # last completed bar (index len-2; last row is forming)
                    h1_close = float(df["close"].iloc[-2])
            except Exception:
                h1_close = None

        rec = fired.get(eid, {})
        last_price = rec.get("price")
        ttl = float(entry.get("ttl_minutes", 240)) * 60

        if _condition_met(entry, price, h1_close):
            # re-arm logic: fire once, then only again if it left and came back
            if not rec.get("active", False):
                msg = _format_alert(entry, price)
                logger.info("TRIGGER: %s", msg)
                notify("watch_" + eid, msg)
                fired[eid] = {"active": True, "price": price, "ts": now}
                changed = True
                alerts += 1
            else:
                # inside zone already alerted; refresh ts
                fired[eid]["ts"] = now
        else:
            if rec.get("active", False):
                # price left the trigger: re-arm after leaving
                fired[eid] = {"active": False, "price": price, "ts": now}
                changed = True
            # clear stale armed entries beyond ttl
        if rec.get("active") and now - float(rec.get("ts", 0)) > ttl:
            fired.pop(eid, None)
            changed = True

    if changed:
        state["fired"] = fired
        _save_json(STATE_PATH, state)
    return alerts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="single poll cycle")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS)
    args = parser.parse_args()

    _setup_logging()
    logger.info("watch daemon starting (interval=%ss)", args.interval)

    client = get_client()
    if args.once:
        try:
            client.ensure_connected()
            n = poll_once(client)
            logger.info("cycle complete: %d alert(s)", n)
            return 0
        except Exception as exc:
            logger.error("poll failed: %s", exc)
            return 1

    while True:
        try:
            client.ensure_connected()
            poll_once(client)
        except Exception as exc:
            logger.warning("cycle error: %s", exc)
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
