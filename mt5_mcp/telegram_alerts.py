"""Telegram alert sender for the MT5 trading bots.

Reads the bot token and chat id from environment variables (or a local
``telegram.env`` next to this file) so secrets stay out of source control.

Env vars:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Public API:
    send_telegram(text)              - plain/HTML message
    send_photo(path_or_bytes, cap)   - chart image with caption
    trade_entry_msg(...) -> str      - rich entry template
    trade_close_msg(...) -> str      - rich close template (R-multiple)
    halt_msg(...), summary_msg(...)
"""

from __future__ import annotations

import io
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("mt5_mcp.telegram_alerts")

_ENV_PATH = Path(__file__).resolve().parent.parent / "telegram.env"
_API = "https://api.telegram.org/bot{token}/{method}"

_session: requests.Session | None = None
_last_send_ts: float = 0.0
_MIN_SEND_INTERVAL = 1.0  # seconds between outbound messages (rate limit)
_RETRIES = 3

# Emoji constants (explicit unicode escapes avoid editor encoding issues)
EMOJI_ROCKET = "\U0001F680"
EMOJI_MONEY = "\U0001F4B0"
EMOJI_CHART_UP = "\U0001F4C8"
EMOJI_CHART_DOWN = "\U0001F4C9"
EMOJI_CHECK = "\u2705"
EMOJI_CROSS = "\u274C"
EMOJI_WARN = "\u26A0\uFE0F"
EMOJI_BELL = "\U0001F514"
EMOJI_STOP = "\U0001F6D1"
EMOJI_ROBOT = "\U0001F916"
EMOJI_FIRE = "\U0001F525"


def _load_config() -> tuple[str, str]:
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                # telegram.env is the source of truth: override any stale
                # token/chat-id inherited from the parent environment.
                os.environ[key.strip()] = value.strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _call(method: str, payload: dict[str, Any], timeout: int = 20) -> dict[str, Any] | None:
    """POST to the Bot API with retry + rate limiting. Returns JSON or None."""
    token, _ = _load_config()
    if not token:
        logger.info("telegram not configured (token missing)")
        return None
    global _last_send_ts
    sess = _get_session()
    for attempt in range(1, _RETRIES + 1):
        wait = _MIN_SEND_INTERVAL - (time.monotonic() - _last_send_ts)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = sess.post(_API.format(token=token, method=method), json=payload, timeout=timeout)
            _last_send_ts = time.monotonic()
            if resp.status_code == 200:
                return resp.json()
            # 429 rate limit: honour retry_after then back off
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("parameters", {}).get("retry_after", 2))
                except Exception:  # noqa: BLE001
                    retry_after = 2.0
                logger.warning("telegram 429, sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            logger.warning(
                "telegram %s failed (%s/%s): %s %s",
                method, attempt, _RETRIES, resp.status_code, resp.text[:200],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram %s error (%s/%s): %s", method, attempt, _RETRIES, exc)
        if attempt < _RETRIES:
            time.sleep(1.5 * attempt)
    return None


def send_telegram(text: str, parse_mode: str = "HTML") -> dict[str, Any] | None:
    """Send a message to the configured chat. Returns the API response.

    Silently returns None when the bot/chat is not configured or the send
    fails - alerts must never crash the trading loop.
    """
    _, chat_id = _load_config()
    if not chat_id:
        logger.info("telegram not configured (chat id missing)")
        return None
    return _call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


def send_photo(image: Path | bytes | io.BytesIO, caption: str = "") -> dict[str, Any] | None:
    """Send a PNG/JPG chart image with an optional caption."""
    _, chat_id = _load_config()
    if not chat_id:
        logger.info("telegram not configured (chat id missing)")
        return None
    token, _ = _load_config()
    buf: io.BytesIO
    if isinstance(image, (bytes, bytearray)):
        buf = io.BytesIO(bytes(image))
    elif isinstance(image, io.BytesIO):
        buf = image
    else:
        buf = io.BytesIO(Path(image).read_bytes())
    buf.seek(0)
    buf.name = "chart.png"

    global _last_send_ts
    sess = _get_session()
    for attempt in range(1, _RETRIES + 1):
        wait = _MIN_SEND_INTERVAL - (time.monotonic() - _last_send_ts)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = sess.post(
                _API.format(token=token, method="sendPhoto"),
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": buf.getvalue()},
                timeout=40,
            )
            _last_send_ts = time.monotonic()
            if resp.status_code == 200:
                return resp.json()
            logger.warning("telegram photo failed (%s/%s): %s", attempt, _RETRIES, resp.text[:150])
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram photo error (%s/%s): %s", attempt, _RETRIES, exc)
        if attempt < _RETRIES:
            time.sleep(1.5 * attempt)
        buf.seek(0)
    return None


# ---------------------------------------------------------------- templates

def _dir_emoji(side: str) -> str:
    s = side.upper()
    if "BUY" in s or "LONG" in s:
        return EMOJI_CHART_UP
    if "SELL" in s or "SHORT" in s:
        return EMOJI_CHART_DOWN
    return EMOJI_ROBOT


def trade_entry_msg(
    symbol: str, side: str, lots: float, entry: float,
    sl: float | None = None, tp: float | None = None,
    confidence: float | None = None, ticket: int | None = None,
    setup: str = "", rr: str = "",
) -> str:
    lines = [
        f"{_dir_emoji(side)} <b>{side.upper()} {symbol}</b>",
        f"Lots: {lots:.2f} @ {entry:.5g}",
    ]
    if sl is not None:
        lines.append(f"SL: {sl:.5g}" + (f" | TP: {tp:.5g}" if tp is not None else ""))
    extras = []
    if confidence is not None:
        extras.append(f"conf {confidence:.0f}%")
    if rr:
        extras.append(f"RR {rr}")
    if setup:
        extras.append(setup)
    if extras:
        lines.append(" | ".join(extras))
    if ticket:
        lines.append(f"<i>ticket #{ticket}</i>")
    return "\n".join(lines)


def trade_close_msg(
    symbol: str, side: str, pnl: float, r_multiple: float | None = None,
    reason: str = "", balance: float | None = None, ticket: int | None = None,
) -> str:
    win = pnl >= 0
    head = f"{EMOJI_MONEY} <b>CLOSED {'+' if win else ''}{pnl:.2f} USD</b>" if win else \
           f"{EMOJI_CROSS} <b>CLOSED {pnl:.2f} USD</b>"
    lines = [head, f"{symbol} {side.upper()}"]
    if r_multiple is not None:
        lines.append(f"R: {r_multiple:+.2f}")
    if reason:
        lines.append(f"exit: {reason}")
    if balance is not None:
        lines.append(f"balance: ${balance:.2f}")
    if ticket:
        lines.append(f"<i>ticket #{ticket}</i>")
    return "\n".join(lines)


def halt_msg(reason: str, detail: str) -> str:
    return f"{EMOJI_STOP} <b>TRADING HALTED</b>\n{reason}\n{detail}"


def signal_msg(symbol: str, direction: str, confidence: float, detail: str = "") -> str:
    return (
        f"{EMOJI_FIRE} <b>SIGNAL {symbol}</b>\n"
        f"{direction.upper()} | conf {confidence:.0f}%\n"
        f"{detail}"
    ).rstrip()


def daily_summary_msg(
    balance: float, equity: float, trades_today: int, wins: int, losses: int,
    pnl_today: float, open_positions: int, notes: str = "",
) -> str:
    wr = (wins / trades_today * 100) if trades_today else 0.0
    icon = EMOJI_CHART_UP if pnl_today >= 0 else EMOJI_CHART_DOWN
    lines = [
        f"{icon} <b>DAILY SUMMARY</b>",
        f"P&L today: {'+' if pnl_today >= 0 else ''}{pnl_today:.2f} USD ({trades_today} trades, {wr:.0f}% WR)",
        f"W/L: {wins}/{losses} | open: {open_positions}",
        f"Equity: ${equity:.2f} (bal ${balance:.2f})",
    ]
    if notes:
        lines.append(notes)
    return "\n".join(lines)
