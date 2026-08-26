"""Throttled notifications for auto-trader events.

Supports Telegram via the Bot API and email via SMTP. Both are optional
and configured purely through environment variables:

* ``MT5_NOTIFY_TELEGRAM_TOKEN`` / ``MT5_NOTIFY_TELEGRAM_CHAT_ID``
* ``MT5_NOTIFY_SMTP_HOST`` / ``MT5_NOTIFY_SMTP_PORT`` / ``MT5_NOTIFY_SMTP_USER``
  / ``MT5_NOTIFY_SMTP_PASS`` / ``MT5_NOTIFY_EMAIL_FROM`` / ``MT5_NOTIFY_EMAIL_TO``
* ``MT5_NOTIFY_MIN_INTERVAL`` (seconds per event type, default 900)

``notify()`` is a no-op when nothing is configured. Delivery state is kept
in ``mt5_mcp/memory/notify_state.json`` so a 5-minute scheduled cycle can
never spam the same channel.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
STATE_PATH = MODULE_DIR / "memory" / "notify_state.json"
STATE_DIR = MODULE_DIR / "memory"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
DEFAULT_MIN_INTERVAL = 900.0
MAX_EVENT_TYPES = 50

logger = logging.getLogger("mt5_autotrader.notify")


def _load_sent() -> dict[str, dict[str, float]]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {}


def _save_sent(sent: dict[str, dict[str, float]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(sent), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _send_telegram(text: str) -> bool:
    token = os.getenv("MT5_NOTIFY_TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("MT5_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - Telegram Bot API
        TELEGRAM_API.format(token=token),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return resp.status == 200


def _send_email(subject: str, body: str) -> bool:
    host = os.getenv("MT5_NOTIFY_SMTP_HOST", "").strip()
    user = os.getenv("MT5_NOTIFY_SMTP_USER", "").strip()
    to = os.getenv("MT5_NOTIFY_EMAIL_TO", "").strip()
    if not host or not user or not to:
        return False
    port = int(os.getenv("MT5_NOTIFY_SMTP_PORT", "587") or 587)
    password = os.getenv("MT5_NOTIFY_SMTP_PASS", "").strip()
    sender = os.getenv("MT5_NOTIFY_EMAIL_FROM", user).strip()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(sender, [to], msg.as_string())
    return True


def notify(event_type: str, message: str, force: bool = False) -> bool:
    """Send a throttled notification for ``event_type``.

    Returns True when at least one channel accepted the message. When no
    channel is configured this is a cheap no-op that always returns False.
    """
    if not (os.getenv("MT5_NOTIFY_TELEGRAM_TOKEN") or os.getenv("MT5_NOTIFY_SMTP_HOST")):
        return False
    now = time.time()
    sent = _load_sent()
    entry = sent.get(event_type, {})
    last = float(entry.get("last", 0.0) or 0.0)
    min_interval = float(os.getenv("MT5_NOTIFY_MIN_INTERVAL", str(DEFAULT_MIN_INTERVAL)))
    if not force and now - last < min_interval:
        return False

    subject = f"[MT5 AutoTrader] {event_type}"
    delivered = False
    try:
        if _send_telegram(f"{subject}: {message}"):
            delivered = True
    except Exception as exc:  # noqa: BLE001 - notifications must never crash the runner
        logger.warning("telegram notify failed: %s", exc)
    try:
        if _send_email(subject, message):
            delivered = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("email notify failed: %s", exc)

    if delivered:
        sent[event_type] = {"last": now}
        if len(sent) > MAX_EVENT_TYPES:
            oldest = sorted(sent, key=lambda k: sent[k].get("last", 0.0))[: len(sent) - MAX_EVENT_TYPES]
            for key in oldest:
                sent.pop(key, None)
        _save_sent(sent)
    return delivered
