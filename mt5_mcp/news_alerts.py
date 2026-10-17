"""News calendar Telegram alerts.

Checks for upcoming high-impact economic events and sends Telegram alerts
30-60 minutes before they occur. Tracks which events have already been
alerted to avoid duplicates.

Uses the existing ForexFactory news provider via mt5_mcp.tools.news.

Run via schtasks every 15 minutes:
    schtasks /Create /TN "MT5R_NewsAlerts" ^
        /TR "C:\\Users\\Dave\\run_news_alerts.bat" ^
        /SC MINUTE /MO 15 /IT /F
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("mt5_mcp.news_alerts")

MODULE_DIR = Path(__file__).resolve().parent
STATE_PATH = MODULE_DIR / "memory" / "news_alerts_state.json"

# Alert windows (minutes before event)
ALERT_WINDOW_MIN = 60  # first alert: 60 min before
ALERT_WINDOW_CLOSE = 30  # second alert: 30 min before (optional)

# State TTL: forget alerted events after 24h
STATE_TTL_SEC = 86400.0


def _load_state() -> dict:
    """Load alerted-events state."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"alerted": {}}


def _save_state(state: dict) -> None:
    """Save alerted-events state (atomic write)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _event_key(event) -> str:
    """Unique key for a news event (currency + time + title)."""
    ts = event.time.strftime("%Y-%m-%dT%H:%M") if hasattr(event.time, "strftime") else str(event.time)
    return f"{event.currency}_{ts}_{event.title[:40]}"


def _prune_state(state: dict) -> dict:
    """Remove alerted entries older than STATE_TTL_SEC."""
    now = time.time()
    alerted = state.get("alerted", {})
    pruned = {
        k: v for k, v in alerted.items()
        if now - float(v.get("ts", 0)) < STATE_TTL_SEC
    }
    state["alerted"] = pruned
    return state


def _fmt_event_html(event, minutes_until: float) -> str:
    """Format a news event as a Telegram HTML card."""
    impact_emoji = {"high": "\U0001F534", "medium": "\U0001F7E1", "low": "\U0001F7E2"}.get(
        getattr(event, "impact", "low"), "\u2B55"
    )
    time_str = event.time.strftime("%H:%M UTC") if hasattr(event.time, "strftime") else str(event.time)
    mins = int(minutes_until)

    lines = [
        f"{impact_emoji} <b>NEWS ALERT</b> \u2014 in {mins} min",
        f"<b>{_esc(event.currency)}</b> \u2014 {_esc(event.title)}",
        f"Time: <code>{time_str}</code>",
    ]
    if getattr(event, "forecast", ""):
        lines.append(f"Forecast: <code>{_esc(event.forecast)}</code>")
    if getattr(event, "previous", ""):
        lines.append(f"Previous: <code>{_esc(event.previous)}</code>")
    lines.append(f"Impact: <b>{_esc(getattr(event, 'impact', 'unknown').upper())}</b>")
    return "\n".join(lines)


def _esc(value: object) -> str:
    """Escape text for Telegram HTML."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def check_and_alert() -> dict:
    """Check for upcoming high-impact events and send alerts.

    Returns a dict summarizing what happened:
    - events_checked: total events in the window
    - events_alerted: new alerts sent this run
    - events_skipped: already-alerted events
    """
    from mt5_mcp.tools.news import get_provider

    state = _load_state()
    state = _prune_state(state)
    alerted = state.setdefault("alerted", {})

    now = datetime.now(timezone.utc)
    provider = get_provider()

    # Get events in the next 2 hours (covers both 60min and 30min windows)
    try:
        events = provider.upcoming(hours=2)
    except Exception as exc:
        logger.warning("Failed to fetch news events: %s", exc)
        _save_state(state)
        return {"status": "error", "reason": str(exc), "events_checked": 0, "events_alerted": 0}

    # Filter to high-impact only
    high_impact = [e for e in events if getattr(e, "impact", "low") == "high"]

    new_alerts = 0
    skipped = 0

    for event in high_impact:
        key = _event_key(event)
        minutes_until = (event.time - now).total_seconds() / 60.0

        # Skip events already past or too far in the future
        if minutes_until < 0 or minutes_until > ALERT_WINDOW_MIN:
            continue

        # Skip if already alerted
        if key in alerted:
            skipped += 1
            continue

        # Send alert
        html = _fmt_event_html(event, minutes_until)
        try:
            from mt5_mcp.notify import notify
            notify(
                f"news_{key}",
                f"{event.currency} {event.title} in {int(minutes_until)} min",
                html=html,
                force=True,
            )
            alerted[key] = {"ts": time.time(), "minutes_before": round(minutes_until, 1)}
            new_alerts += 1
            logger.info("Alerted: %s in %d min", key, int(minutes_until))
        except Exception as exc:
            logger.warning("Failed to send alert for %s: %s", key, exc)

    _save_state(state)

    return {
        "status": "ok",
        "events_checked": len(high_impact),
        "events_alerted": new_alerts,
        "events_skipped": skipped,
        "timestamp": now.isoformat(),
    }


def main() -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="News calendar Telegram alerts")
    parser.add_argument("--check", action="store_true", help="check and alert (default)")
    parser.add_argument("--status", action="store_true", help="show alert state")
    args = parser.parse_args()

    if args.status:
        state = _load_state()
        alerted = state.get("alerted", {})
        print(f"Alerted events: {len(alerted)}")
        for key, info in sorted(alerted.items(), key=lambda x: x[1].get("ts", 0)):
            age_min = (time.time() - info.get("ts", 0)) / 60
            print(f"  {key} ({age_min:.0f} min ago)")
        return 0

    result = check_and_alert()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
