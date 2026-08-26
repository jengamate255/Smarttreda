"""AI vision daemon: renders the live Boom/Crash chart from MT5 market data
and sends the image to a vision model (Gemini free tier, OpenRouter fallback)
for analysis.

Triggers:
  * every N minutes (market awareness poll)
  * immediately when a new trade is placed (journal.db watch)

The Deriv MT5 terminal runs headless (Session 0), so charts are rendered
from candle data instead of screen capture.

Output:
  * analysis appended to logs/vision_analysis.jsonl
  * desktop popup with the verdict
  * journal events table entry

Runs as its own background process - opencode not required.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

ROOT = Path(r"E:\Project\Mt5 Ea")
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MT5_MCP_MT5_TERMINAL_PATH", r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe")
os.environ.setdefault("MT5_MCP_MT5_LOGIN", "41204968")
os.environ.setdefault("MT5_MCP_MT5_PASSWORD", "")
os.environ.setdefault("MT5_MCP_MT5_SERVER", "Deriv-Demo")

from mt5_mcp.client import get_client
from mt5_mcp.tools.boom_crash_strategy import mt5_symbol
from mt5_mcp.visual_notify import popup

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.6-flash")
OPENROUTER_KEY = os.getenv(
    "OPENROUTER_API_KEY",
    "",
)
OPENROUTER_MODEL = os.getenv("OR_VISION_MODEL", "google/gemma-4-31b-it:free")
INTERVAL_MINUTES = float(os.getenv("OR_POLL_MINUTES", "15"))
DB = ROOT / "mt5_mcp" / "memory" / "journal.db"
OUT = ROOT / "logs" / "vision_analysis.jsonl"

logger = logging.getLogger("ai_vision_daemon")
_last_call_at = 0.0
_MIN_CALL_GAP = 45.0


def _rate_limited() -> bool:
    global _last_call_at  # noqa: PLW0603
    now = time.monotonic()
    if now - _last_call_at < _MIN_CALL_GAP:
        return True
    _last_call_at = now
    return False


# --------------------------------------------------------------------------
# chart rendering from live data
# --------------------------------------------------------------------------
def render_chart(symbol: str, lookback: int = 60) -> bytes | None:
    client = get_client()
    client.ensure_connected()
    df = client.rates(mt5_symbol(symbol), "M1", lookback)
    if df is None or len(df) < 20:
        logger.warning("not enough candles for %s: %s", symbol, len(df or []))
        return None
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    df = df[["time", "Open", "High", "Low", "Close"]].set_index("time")
    df.index = pd.to_datetime(df.index, utc=True)

    buf = io.BytesIO()
    figsize = (12, 7)
    try:
        mpf.plot(
            df,
            type="candle",
            style="charles",
            volume=False,
            figsize=figsize,
            title=f"{symbol} M1 (last {lookback} bars)",
            savefig=dict(fname=buf, dpi=110, bbox_inches="tight"),
        )
        plt.close("all")
    except Exception as exc:
        logger.error("chart render failed for %s: %s", symbol, exc)
        return None
    buf.seek(0)
    return buf.getvalue()


# --------------------------------------------------------------------------
# journal helpers
# --------------------------------------------------------------------------
def _latest_trade_id() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT MAX(id) FROM trades").fetchone()
        return int(row[0] or 0)
    finally:
        con.close()


def _last_trade() -> dict | None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT id, ticket, symbol, side, entry, sl, tp, lots, open_time, reason "
            "FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# --------------------------------------------------------------------------
# vision providers
# --------------------------------------------------------------------------
def _gemini_analyze(img_bytes: bytes, prompt: str) -> dict:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_bytes).decode()}},
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 800},
        },
        timeout=90,
    )
    if r.status_code != 200:
        return {"error": f"gemini http {r.status_code}: {r.text[:200]}"}
    data = r.json()
    try:
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        return {"content": content.strip(), "model": GEMINI_MODEL}
    except (KeyError, IndexError):
        return {"error": f"gemini parse: {json.dumps(data)[:300]}"}


def _openrouter_analyze(img_bytes: bytes, prompt: str) -> dict:
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8050",
            "X-Title": "MT5 AI Vision Daemon",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"},
        },
                    ],
                }
            ],
            "max_tokens": 800,
        },
        timeout=90,
    )
    if r.status_code != 200:
        return {"error": f"openrouter http {r.status_code}: {r.text[:200]}"}
    data = r.json()
    content = data["choices"][0]["message"]["content"] or ""
    return {"content": content.strip(), "model": data.get("model", OPENROUTER_MODEL)}


PROVIDERS = [
    ("gemini", _gemini_analyze),
    ("openrouter", _openrouter_analyze),
]


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    if start < 0:
        return {"verdict": "UNCLEAR", "direction": "none", "risk": 5, "summary": text[:300]}
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break
    return {"verdict": "UNCLEAR", "direction": "none", "risk": 5, "summary": text[:300]}


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def _run_cycle(kind: str, symbol: str, context: str) -> None:
    if _rate_limited():
        logger.info("skipped %s: rate-limit gap", kind)
        return
    img_bytes = render_chart(symbol)
    if img_bytes is None:
        return
    logger.info("rendered chart for %s (%d bytes) kind=%s", symbol, len(img_bytes), kind)

    prompt = (
        "You are a technical chart analyst for Deriv synthetic indices "
        "(Boom/Crash spike-drift strategy). Look at this candlestick chart.\n\n"
        f"CONTEXT: {context}\n\n"
        "Respond ONLY with compact JSON: {\"verdict\":\"VALID|INVALID|UNCLEAR\","
        "\"direction\":\"long|short|none\",\"supply_demand\":\"brief zone notes\","
        "\"risk\":1..10,\"summary\":\"one sentence for a non-expert\"}"
    )

    last_error = None
    for provider_name, fn in PROVIDERS:
        try:
            result = fn(img_bytes, prompt)
            if "error" in result:
                last_error = result["error"]
                logger.warning("provider %s failed: %s", provider_name, last_error)
                continue
            parsed = _extract_json(result["content"])
            parsed["provider"] = provider_name
            parsed["kind"] = kind
            parsed["symbol"] = symbol
            parsed["context"] = context
            parsed["model"] = result.get("model")
            parsed["timestamp"] = datetime.now(timezone.utc).isoformat()
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(parsed) + "\n")
            logger.info(
                "verdict=%s risk=%s dir=%s (%s)",
                parsed.get("verdict"),
                parsed.get("risk"),
                parsed.get("direction"),
                provider_name,
            )
            emoji = {"VALID": "OK", "INVALID": "NO", "UNCLEAR": "?"}.get(parsed.get("verdict"), "?")
            popup(
                f"[AI VISION {kind.upper()}] {emoji} {parsed.get('verdict')}",
                f"{symbol} risk {parsed.get('risk')}/10 dir={parsed.get('direction')}\n"
                f"{parsed.get('summary', '')[:160]}",
                seconds=10,
            )
            return
        except Exception as exc:
            last_error = str(exc)
            logger.error("provider %s raised: %s", provider_name, exc)
    if last_error:
        logger.error("all providers failed for %s: %s", kind, last_error)


def trade_watch_loop() -> None:
    last_id = _latest_trade_id()
    logger.info("trade-watch started, latest trade id=%s", last_id)
    while True:
        try:
            latest = _latest_trade_id()
            if latest > last_id:
                trade = _last_trade()
                last_id = latest
                if trade:
                    context = (
                        f"A new trade was just placed: {trade['side']} {trade['symbol']} "
                        f"entry={trade['entry']} SL={trade['sl']} TP={trade['tp']} "
                        f"lots={trade['lots']} ticket={trade['ticket']}."
                        "Is this setup safe to hold? Flag anything that looks wrong on the chart."
                    )
                    _run_cycle("trade", trade["symbol"], context)
        except Exception as exc:
            logger.warning("trade-watch cycle error: %s", exc)
        time.sleep(5)


def poll_loop() -> None:
    logger.info("poll loop started, interval=%.0f min", INTERVAL_MINUTES)
    symbols = ["BOOM900", "CRASH600"]
    idx = 0
    while True:
        try:
            symbol = symbols[idx % len(symbols)]
            _run_cycle(
                "poll",
                symbol,
                "Routine market awareness check. Is there any high-probability "
                "spike-drift setup visible right now, and where are the key supply/demand zones?",
            )
            idx += 1
        except Exception as exc:
            logger.warning("poll cycle error: %s", exc)
        time.sleep(INTERVAL_MINUTES * 60)


def main() -> int:
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(ROOT / "logs" / "ai_vision_daemon.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "AI vision daemon starting gemini=%s or=%s interval=%.0fmin",
        GEMINI_MODEL,
        OPENROUTER_MODEL,
        INTERVAL_MINUTES,
    )
    threading.Thread(target=trade_watch_loop, daemon=True).start()
    poll_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())