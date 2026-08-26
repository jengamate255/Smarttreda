"""Shared trade event log for the dashboards.

Watchers (Boom/Crash, forex breakout, exit managers) append JSON events to
``logs/trade_events.jsonl``. Each web dashboard exposes them via
``/api/events`` so the UI can toast fills/closes/trails as they happen.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_PATH = os.path.join(ROOT, "logs", "trade_events.jsonl")
MAX_ID_KEEP = 500

_lock = threading.Lock()
_sequence = 0


def emit(account: str, kind: str, symbol: str, message: str) -> None:
    """Append one trade event. Never raises; logging is best-effort."""
    global _sequence
    try:
        now_ms = int(time.time() * 1000)
        with _lock:
            _sequence = max(_sequence + 1, now_ms)
            row = {
                "id": _sequence,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "account": account,
                "kind": kind,
                "symbol": symbol or "",
                "message": message,
            }
            path = EVENTS_PATH
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def read_since(since_id: int = 0, limit: int = 50) -> tuple[list[dict], int]:
    """Return (events with id > since_id, latest id). Never raises."""
    events: list[dict] = []
    latest = since_id
    try:
        with open(EVENTS_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                eid = int(row.get("id", 0) or 0)
                latest = max(latest, eid)
                if eid > since_id:
                    events.append(row)
        return events[-limit:], latest
    except FileNotFoundError:
        return [], since_id
    except OSError:
        return [], since_id