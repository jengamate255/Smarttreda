"""Telegram command bot for the MT5 trading system.

Long-polls the @storm255bot bot and answers questions about setups,
trades, positions, account status, signals, and chart images. Reads config
from telegram.env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS).

Supported commands (all case-insensitive, with or without /prefix):
    help                      - list all available commands
    start                     - same as help
    setups                    - today's trades by setup + current qualifying signals
    setups today              - same as setups
    trades today / today      - today's trades + live open positions
    recent / journal          - last 10 journal trades
    positions / open trades   - open positions on both Deriv + Prop accounts
    account / equity / balance - account summary (both Deriv + Prop)
    vision                    - latest AI chart analysis
    bots / watchers           - which trading processes are running
    status                    - bots + watchers running status
    signals                   - current qualifying signals from all scanners
    performance               - today's trade performance metrics
    /chart                    - send M5 chart snapshot with pattern + confluence
                                     (also: autochartist, patterns, snapshot)
                                     shows full structure, entry/wait with confluence

Only responds to authorized chat IDs configured in TELEGRAM_CHAT_IDS
(env var: comma-separated list of chat IDs). Runs as a background daemon -
no opencode required.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import io
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.telegram_alerts import _load_config

try:
    from mt5_mcp.pattern_confluence import (
        build_signal_caption,
        detect_patterns,
        generate_chart_with_patterns,
    )
    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
API = "https://api.telegram.org/bot{token}"

# Rate limiting: minimum seconds between messages to same chat
RATE_LIMIT_SECONDS = 2

# Authorized chat IDs are resolved lazily on first use (after
# telegram.env / env vars are loaded in main()) instead of at import time.
_authorized_chat_ids: set[int] | None = None


def _auth_chat_ids() -> set[int]:
    """Authorized chat IDs (comma-separated from env, or single value)."""
    global _authorized_chat_ids
    if _authorized_chat_ids is None:
        _load_config()
        _raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        _authorized_chat_ids = (
            {int(cid.strip()) for cid in _raw.split(",") if cid.strip()} if _raw else set()
        )
    return _authorized_chat_ids


AUTHORIZED_CHAT_IDS = _auth_chat_ids()

# Per-chat last-sent tracker (in-memory, resets on restart)
last_sent_time: dict[int, float] = {}


# --------------------------------------------------------------------------
# journal queries
# --------------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    DB = ROOT / "mt5_mcp" / "memory" / "journal.db"
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def trades_today() -> list[dict]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _conn() as con:
        rows = con.execute(
            "SELECT id, ticket, symbol, side, entry, exit_price, sl, tp, lots, "
            "open_time, close_time, pnl, outcome, setup, confidence, rr "
            "FROM trades "
            "WHERE (open_time IS NOT NULL AND open_time LIKE ?) "
            "   OR (open_time IS NULL AND created_at LIKE ?) "
            "ORDER BY COALESCE(open_time, created_at) DESC",
            (f"{day}%", f"{day}%"),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_trades(limit: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, ticket, symbol, side, entry, exit_price, sl, tp, lots, "
            "open_time, close_time, pnl, outcome, setup, confidence, rr "
            "FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def last_vision_analysis() -> dict | None:
    path = ROOT / "logs" / "vision_analysis.jsonl"
    if not path.exists():
        return None
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# live terminal queries (subprocess - avoids holding MT5 in bot process)
# --------------------------------------------------------------------------
def _live_query(code: str, env_override: dict | None = None) -> dict:
    env = os.environ.copy()
    env.update(env_override or {})
    env["MT5_MCP_MT5_TERMINAL_PATH"] = r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
            env=env,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip()[:200]}
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        return data
    except Exception as exc:
        return {"error": str(exc)}


def _live_query_prop(code: str) -> dict:
    env = os.environ.copy()
    env["MT5_MCP_MT5_TERMINAL_PATH"] = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
    env["MT5_MCP_MT5_LOGIN"] = "11911448"
    env["MT5_MCP_MT5_PASSWORD"] = ""
    env["MT5_MCP_MT5_SERVER"] = "FundingPips2-SIM"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
            env=env,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip()[:200]}
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        return data
    except Exception as exc:
        return {"error": str(exc)}


LIVE_POSITIONS = r"""
import json, sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')
from mt5_mcp.client import get_client, reset_client
try:
    reset_client(); client = get_client(); client.ensure_connected()
    acc = client.account_info()
    positions = client.positions() or []
    print(json.dumps({
        "login": acc.get("login"), "server": acc.get("server"),
        "balance": acc.get("balance"), "equity": acc.get("equity"),
        "floating": acc.get("profit"),
        "positions": [
            {"ticket": p.get("ticket"), "symbol": p.get("symbol"),
             "side": "BUY" if p.get("type") == 0 else "SELL",
             "lots": p.get("volume"),
             "entry": p.get("price_open"), "sl": p.get("sl"), "tp": p.get("tp"),
             "profit": p.get("profit")}
            for p in positions
        ],
    ))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
"""

LIVE_SETUPS = r"""
import json, sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')
from mt5_mcp.client import get_client, reset_client
from mt5_mcp.tools.boom_crash_strategy import scan_boom_crash_opportunities
try:
    reset_client(); client = get_client(); client.ensure_connected()
    sigs = scan_boom_crash_opportunities(min_confidence=50.0, lookback_minutes=60)
    print(json.dumps([
        {"symbol": s.symbol, "side": "BUY" if s.drift_direction == "up" else "SELL",
         "entry": s.entry_price, "sl": s.sl_price,
         "confidence": round(s.confidence, 1), "age": s.spike_age_candles,
         "atr": round(float(s.atr), 5)}
        for s in sigs
    ]))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
"""


# --------------------------------------------------------------------------
# answer builders
# --------------------------------------------------------------------------
def _build_help() -> str:
    """Dynamic help generated from actual available commands."""
    cmds = {
        "help": "Show this help message",
        "start": "Same as help",
        "setups": "Today's setups + current qualifying signals",
        "setups today": "Same as setups",
        "trades today": "Today's trades + live open positions",
        "today": "Today's trades + live open positions",
        "recent": "Last 10 journal trades",
        "journal": "Last 10 journal trades",
        "positions": "Open positions on both Deriv + Prop accounts",
        "account": "Account summary (balance/equity/floating)",
        "balance": "Account summary (balance/equity/floating)",
        "vision": "Latest AI chart analysis",
        "bots": "Watcher processes running status",
        "status": "Bots + watchers running status",
        "signals": "Current qualifying signals from all scanners",
        "performance": "Today's trade performance metrics",
        "/chart": "Send M5 candlestick chart with S/D zones",
    }
    lines = ["<b>🤖 MT5 Trading Assistant</b>"]
    lines.append("Available commands:")
    for cmd, desc in cmds.items():
        lines.append(f"  <code>/{cmd}</code> - {desc}")
    lines.append("\nAll commands work with or without leading <code>/</code>.")
    lines.append("Rate limited: 1 message every 2 seconds per chat.")
    return "\n".join(lines)


def answer_help() -> str:
    return _build_help()


def answer_setups() -> str:
    lines = ["<b>📋 Setups today</b>"]
    trades = trades_today()
    if trades:
        lines.append(f"Trades today: <b>{len(trades)}</b>")
        for t in trades:
            pnl = f" PnL {t['pnl']}" if t["pnl"] is not None else ""
            outcome = f" ({t['outcome']})" if t["outcome"] else ""
            lines.append(
                f"• {t['side']} {t['symbol']} lot={t['lots']} @ {t['entry']}"
                f" setup={t['setup']}{pnl}{outcome}"
            )
    else:
        lines.append("No trades placed today yet.")
    lines.append("\n<b>📡 Current qualifying signals (Boom/Crash)</b>")
    live = _live_query(LIVE_SETUPS)
    if "error" in live:
        lines.append(f"scan unavailable: {live['error'][:80]}")
    elif not live:
        lines.append("none right now - waiting for fresh spikes")
    else:
        for s in live:
            lines.append(
                f"• {s['side']} {s['symbol']} entry={s['entry']} sl={s['sl']}"
                f" conf={s['confidence']}% age={s['age']}"
            )
    return "\n".join(lines)


def answer_trades_today() -> str:
    lines = []
    trades = trades_today()
    if trades:
        lines.append(f"<b>📊 Trades today ({len(trades)})</b>")
        for t in trades:
            pnl = f" → <b>{t['pnl']}</b>" if t["pnl"] is not None else ""
            lines.append(
                f"• #{t['id']} {t['side']} {t['symbol']} lot={t['lots']} "
                f"@ {t['entry']} ({t['setup']}){pnl}"
            )
    else:
        lines.append("<b>📊 Trades today</b>")
        lines.append("No trades recorded today in the journal.")
    lines.append("\n<b>📌 Currently open (live)</b>")
    open_any = False
    for label, q in (
        ("Deriv", _live_query(LIVE_POSITIONS)),
        ("Prop", _live_query_prop(LIVE_POSITIONS)),
    ):
        if "error" in q:
            lines.append(f"{label}: unavailable ({q['error'][:60]})")
            continue
        if not q["positions"]:
            continue
        open_any = True
        lines.append(f"{label} ({len(q['positions'])}):")
        for p in q["positions"]:
            lines.append(
                f"• #{p['ticket']} {p['side']} {p['symbol']} lot={p['lots']} "
                f"@ {p['entry']} profit=<b>{p['profit']}</b>"
            )
    if not open_any:
        lines.append("No open positions on either account.")
    return "\n".join(lines)


def answer_recent() -> str:
    trades = recent_trades(10)
    if not trades:
        return "Journal is empty."
    lines = ["<b>📖 Last 10 journal trades</b>"]
    for t in trades:
        pnl = f" → <b>{t['pnl']}</b>" if t["pnl"] is not None else ""
        lines.append(
            f"• #{t['id']} {t['side']} {t['symbol']} lot={t['lots']} "
            f"@ {t['entry']} ({t['setup']}){pnl}"
        )
    return "\n".join(lines)


def answer_positions() -> str:
    lines = []
    for label, q in (
        ("Deriv (Boom/Crash)", _live_query(LIVE_POSITIONS)),
        ("Prop (FundingPips)", _live_query_prop(LIVE_POSITIONS)),
    ):
        if "error" in q:
            lines.append(f"<b>{label}</b>: query failed ({q['error'][:80]})")
            continue
        lines.append(f"<b>💼 {label} - {q['server']} (login {q['login']})</b>")
        lines.append(f"Balance: {q['balance']} | Equity: {q['equity']}")
        lines.append(f"Floating: <b>{q['floating']}</b>")
        if not q["positions"]:
            lines.append("No open positions.")
        else:
            for p in q["positions"]:
                lines.append(
                    f"• #{p['ticket']} {p['side']} {p['symbol']} lot={p['lots']} "
                    f"@ {p['entry']} SL={p['sl']} TP={p['tp']} profit={p['profit']}"
                )
        lines.append("")
    return "\n".join(lines).rstrip()


def answer_account() -> str:
    lines = []
    for label, q in (
        ("Deriv (Boom/Crash)", _live_query(LIVE_POSITIONS)),
        ("Prop (FundingPips)", _live_query_prop(LIVE_POSITIONS)),
    ):
        if "error" in q:
            lines.append(f"<b>{label}</b>: query failed ({q['error'][:80]})")
            continue
        lines.append(
            f"<b>💰 {label} - {q['server']} (login {q['login']})</b>\n"
            f"Balance: <b>{q['balance']}</b>\n"
            f"Equity: <b>{q['equity']}</b>\n"
            f"Floating: {q['floating']}\n"
            f"Open positions: {len(q['positions'])}"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def answer_vision() -> str:
    a = last_vision_analysis()
    if not a:
        return "No AI vision analysis yet."
    return (
        f"<b>👁 Last AI analysis ({a.get('timestamp', '')[:19]} UTC)</b>\n"
        f"Verdict: <b>{a.get('verdict')}</b> | risk {a.get('risk')}/10 | dir={a.get('direction')}\n"
        f"Symbol: {a.get('symbol')} | provider: {a.get('provider')} / {a.get('model')}\n"
        f"Zones: {a.get('supply_demand', 'n/a')}\n"
        f"Summary: {a.get('summary', '')[:250]}"
    )


def answer_bots() -> str:
    import psutil

    lines = ["<b>🤖 Watcher processes</b>"]
    targets = {
        "trade_watch.py (Boom/Crash)": "trade_watch.py",
        "forex_breakout_watch.py (forex)": "forex_breakout_watch.py",
        "ai_vision_daemon.py (vision)": "ai_vision_daemon.py",
    }
    for label, match in targets.items():
        found = [
            p.info["pid"]
            for p in psutil.process_iter(["pid", "cmdline"])
            if match in " ".join(p.info["cmdline"] or [])
        ]
        lines.append(f"• {label}: {'RUNNING ' + str(found[0]) if found else 'STOPPED'}")
    return "\n".join(lines)


def answer_status() -> str:
    """Status of bots and watchers."""
    import psutil

    lines = ["<b>🤖 System Status</b>"]
    t_ok = AUTHORIZED_CHAT_IDS and True
    lines.append(f"• Telegram bot: {'RUNNING' if t_ok else 'STOPPED'}")
    for label, match in {
        "trade_watch.py (Boom/Crash)": "trade_watch.py",
        "forex_breakout_watch.py (forex)": "forex_breakout_watch.py",
        "ai_vision_daemon.py (vision)": "ai_vision_daemon.py",
    }.items():
        found = [
            p.info["pid"]
            for p in psutil.process_iter(["pid", "cmdline"])
            if match in " ".join(p.info["cmdline"] or [])
        ]
        lines.append(f"• {label}: {'RUNNING ' + str(found[0]) if found else 'STOPPED'}")
    lines.append("")
    lines.append(f"• Authorized chats: {len(AUTHORIZED_CHAT_IDS)}")
    return "\n".join(lines)


def answer_signals() -> str:
    """Show current qualifying signals from all scanners."""
    lines = ["<b>📡 Current Qualifying Signals</b>"]
    live = _live_query(LIVE_SETUPS)
    if "error" in live:
        lines.append(f"• Boom/Crash: unavailable ({live['error'][:60]})")
    elif not live:
        lines.append("• Boom/Crash: none right now - waiting for fresh spikes")
    else:
        lines.append(f"• Boom/Crash: {len(live)} signal(s)")
        for s in live:
            lines.append(
                f"  - {s['side']} {s['symbol']} entry={s['entry']} sl={s['sl']}"
                f" conf={s['confidence']}% age={s['age']} ATR={s['atr']:.5f}"
            )
    lines.append("")
    lines.append("<b>📈 Gold & NDX Scalper</b>")
    lines.append("• Trend filter: H1 EMA 200 (bull/bear/unclear)")
    lines.append("• M5 pullback to S/D zones + momentum confirmation")
    lines.append("• 1% risk per trade, 1:1 min RR")
    lines.append("• Max 2 trades/day, 5% daily loss, 8% overall drawdown")
    return "\n".join(lines)


def answer_performance() -> str:
    """Today's trade performance metrics."""
    trades = trades_today()
    winning = [t for t in trades if t.get("pnl") is not None and t["pnl"] > 0]
    losing = [t for t in trades if t.get("pnl") is not None and t["pnl"] < 0]
    total = len([t for t in trades if t.get("pnl") is not None])

    gross_win = sum(t["pnl"] for t in winning) if winning else 0
    gross_loss = abs(sum(t["pnl"] for t in losing)) if losing else 0
    win_rate = len(winning) / total * 100 if total else 0
    profit_factor = gross_win / gross_loss if gross_loss else float("inf")

    deriv_q = _live_query(LIVE_POSITIONS)
    prop_q = _live_query_prop(LIVE_POSITIONS)
    deriv_eq = deriv_q.get("equity", 0) if "error" not in deriv_q else 0
    prop_eq = prop_q.get("equity", 0) if "error" not in prop_q else 0
    total_eq = deriv_eq + prop_eq

    lines = [
        "<b>📊 Performance Today</b>",
        f"Trades recorded: {total}",
        f"Win rate: {win_rate:.1f}% ({len(winning)}/{len(losing)})",
        f"Gross win: ${gross_win:.2f}",
        f"Gross loss: ${gross_loss:.2f}",
        f"Profit factor: {profit_factor:.2f}",
        f"Total equity: ${total_eq:.2f}",
        f"Deriv equity: ${deriv_eq:.2f}",
        f"Prop equity: ${prop_eq:.2f}",
        f"Max drawdown today: 5% limit",
        f"Trades today limit: 2",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# message classification
# --------------------------------------------------------------------------
def _classify(text: str) -> str:
    """Classify incoming message into a command category."""
    t = text.lower().strip()
    # Strip /prefix
    if t.startswith("/"):
        t = t[1:]

    # Exact command matches first
    exact_commands = {
        "help": "help",
        "start": "help",
        "setups": "setups",
        "setups today": "setups",
        "trades today": "trades_today",
        "today": "trades_today",
        "recent": "recent",
        "journal": "recent",
        "positions": "positions",
        "account": "account",
        "balance": "account",
        "vision": "vision",
        "bots": "bots",
        "status": "status",
        "signals": "signals",
        "performance": "performance",
        "/chart": "chart",
        "chart": "chart",
        "charts": "chart",
        "autochartist": "chart",
        "/autochartist": "chart",
        "patterns": "chart",
        "/patterns": "chart",
        "snapshot": "chart",
        "/snapshot": "chart",
    }
    if t in exact_commands:
        return exact_commands[t]

    # Keyword-based fallbacks
    if any(k in t for k in ("setup", "signals", "opportunities")):
        return "setups"
    if any(k in t for k in ("trade", "today")):
        return "trades_today"
    if any(k in t for k in ("recent", "journal", "history")):
        return "recent"
    if any(k in t for k in ("position", "open", "floating")):
        return "positions"
    if any(k in t for k in ("account", "equity", "balance", "money")):
        return "account"
    if any(k in t for k in ("vision", "analysis", "ai")):
        return "vision"
    if any(k in t for k in ("bot", "watcher", "running", "process")):
        return "bots"
    return "unknown"


def _rate_limit_ok(chat_id: int) -> bool:
    """Check if enough time has since the last message to this chat."""
    now = time.time()
    last = last_sent_time.get(chat_id, 0)
    return now - last >= RATE_LIMIT_SECONDS


def _set_rate_time(chat_id: int) -> None:
    last_sent_time[chat_id] = time.time()


def handle_message(text: str, chat_id: int) -> str | None:
    """Process a message and return reply, or None if should not respond."""
    # Rate limiting check
    if not _rate_limit_ok(chat_id):
        return None  # Too soon, don't respond

    # Authorized chat check
    if AUTHORIZED_CHAT_IDS and str(chat_id) not in {str(cid) for cid in AUTHORIZED_CHAT_IDS}:
        return None  # Silently ignore non-authorized chats

    kind = _classify(text)

    handlers = {
        "help": answer_help,
        "start": answer_help,
        "setups": answer_setups,
        "signals": answer_signals,
        "performance": answer_performance,
        "status": answer_status,
        "trades_today": answer_trades_today,
        "recent": answer_recent,
        "positions": answer_positions,
        "account": answer_account,
        "vision": answer_vision,
        "bots": answer_bots,
        "chart": lambda: _generate_and_send_chart(chat_id),
    }

    if kind == "unknown":
        return None  # silently ignore unrecognized messages

    try:
        reply = handlers[kind]()
        _set_rate_time(chat_id)
        return reply
    except Exception as exc:
        logging.getLogger("telegram_bot").exception("handler %s failed", kind)
        return f"⚠️ Error: {str(exc)[:120]}"


# --------------------------------------------------------------------------
# chart generation and sending
# --------------------------------------------------------------------------
def _fetch_m5_data(symbol: str, count: int = 200) -> pd.DataFrame | None:
    """Fetch M5 rates from prop account via subprocess."""
    code = f'''
import pandas as pd
import sys
sys.path.insert(0, r'E:\\Project\\Mt5 Ea')
from mt5_mcp.client import get_client, reset_client
reset_client(); c=get_client(); c.ensure_connected()
f=c.rates('{symbol}', 'M5', {count})
if f is not None and len(f) > 0:
    df=pd.DataFrame(f)
    df['close']=df['close'].astype(float)
    df['high']=df['high'].astype(float)
    df['low']=df['low'].astype(float)
    df['open']=df['open'].astype(float)
    df['time']=pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.tail({count})
    print(df.to_json(orient='records'))
'''
    result = _live_query(code)
    if isinstance(result, dict) and "error" in result:
        return None
    data = result if isinstance(result, list) else []
    if not data:
        return None
    df = pd.DataFrame(data)
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['open'] = df['open'].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.tail(count)
    return df


def _generate_chart_image(symbol: str, df: pd.DataFrame) -> bytes | None:
    """Generate a candlestick chart with trend overlay and S/D zone annotations."""
    try:
        fig, ax = plt.subplots(1, 1, figsize=(10, 6))

        # Prepare data
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        closes = df["close"].tolist()

        # Color up/down bars
        colors = ["green" if closes[i] >= opens[i] else "red" for i in range(len(df))]

        # Plot each bar
        for i in range(len(df)):
            ax.bar(i, closes[i] - opens[i], bottom=opens[i], width=0.6, color=colors[i])
            ax.plot([i, i], [highs[i], lows[i]], color="black", linewidth=1)

        # Add EMA 20 trend line
        close_series = pd.Series(closes)
        ema20 = close_series.ewm(span=20, adjust=False).mean()
        ax.plot(range(len(df)), ema20, "yellow", linewidth=2, label="EMA 20")

        # Add recent high/low annotations
        recent_high = df["high"].max()
        recent_low = df["low"].min()
        ax.axhline(recent_high, color="magenta", linestyle="--", alpha=0.5, label="Recent High")
        ax.axhline(recent_low, color="cyan", linestyle="--", alpha=0.5, label="Recent Low")

        # Formatting
        ax.set_title(f"{symbol} M5 Chart - EMA 20 Trend")
        ax.set_ylabel("Price")
        ax.legend()
        plt.setp(plt.gca().get_xticklabels(), rotation=45, ha="right")

        # Save to buffer
        buf = io.BytesIO()
        plt.tight_layout()
        fig.savefig(buf, format="png", dpi=100)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()

    except Exception as exc:
        logging.getLogger("telegram_bot").error("Chart generation error: %s", exc)
        return None


def _generate_and_send_chart(chat_id: int) -> str | None:
    """Generate M5 chart image and send via Telegram."""
    try:
        # Fetch M5 data
        df = _fetch_m5_data("XAUUSD", 200)
        if df is None or df.empty:
            return "⚠️ Could not fetch M5 data for chart generation."

        # Generate annotated chart with pattern + confluence (falls back to plain chart)
        caption = "📊 M5 XAUUSD Chart with EMA 20 & Zones"
        img_bytes = None
        if HAS_PATTERNS:
            try:
                from mt5_mcp.pattern_confluence import detect_patterns as _dp
                _pats = _dp(df)
                img_bytes, _conf = generate_chart_with_patterns("XAUUSD", df)
                if img_bytes is not None:
                    caption = build_signal_caption("XAUUSD", _conf, _pats)
            except Exception as _e:
                logging.getLogger("telegram_bot").warning("pattern chart failed, falling back: %s", _e)
        if img_bytes is None:
            img_bytes = _generate_chart_image("XAUUSD", df)
        if img_bytes is None:
            return "⚠️ Chart generation failed."

        # Send via Telegram
        token, _ = _load_config()
        base = f"https://api.telegram.org/bot{token}"
        resp = requests.post(
            f"{base}/sendPhoto",
            files={"photo": ("chart.png", img_bytes, "image/png")},
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            timeout=30,
        )
        if resp.status_code != 200:
            return f"⚠️ Failed to send chart: {resp.text[:100]}"
        return "✅ Chart sent successfully!"
    except Exception as exc:
        logging.getLogger("telegram_bot").error("Chart send error: %s", exc)
        return f"⚠️ Chart error: {str(exc)[:120]}"


# --------------------------------------------------------------------------
# bot loop
# --------------------------------------------------------------------------
def _acquire_singleton() -> Path | None:
    """Prevent duplicate bot processes (two long-pollers on one token cause
    missed/conflicting updates). Returns the pid file handle, or None when
    another instance is already running."""
    pid_file = ROOT / "logs" / "telegram_bot.pid"
    try:
        os.makedirs(pid_file.parent, exist_ok=True)
        if pid_file.exists():
            pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
            if pid > 0:
                import psutil

                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    if proc.is_running() and "telegram_bot.py" in " ".join(proc.cmdline() or []):
                        return None
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        return pid_file
    except Exception:
        return None


def main() -> int:
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(ROOT / "logs" / "telegram_bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    pid_file = _acquire_singleton()
    if pid_file is None:
        logging.getLogger("telegram_bot").warning(
            "another telegram bot instance is already running; exiting"
        )
        return 1
    token, chat_id = _load_config()
    if not token:
        logging.getLogger("telegram_bot").error("TELEGRAM_BOT_TOKEN not configured; add telegram.env")
        if pid_file:
            pid_file.unlink(missing_ok=True)
        return 1

    base = API.format(token=token)
    offset = 0

    logging.getLogger("telegram_bot").info("telegram bot started (authorized chats=%s)", len(AUTHORIZED_CHAT_IDS))

    while True:
        try:
            resp = requests.get(
                f"{base}/getUpdates",
                params={"timeout": 30, "offset": offset},
                timeout=45,
            )
            if resp.status_code != 200:
                logging.getLogger("telegram_bot").warning("getUpdates http %s", resp.status_code)
                time.sleep(5)
                continue
            for upd in resp.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                text = msg.get("text", "").strip()
                if not text:
                    continue
                sender = msg["chat"]["id"]
                if AUTHORIZED_CHAT_IDS and str(sender) not in {str(cid) for cid in AUTHORIZED_CHAT_IDS}:
                    logging.getLogger("telegram_bot").info("ignoring message from foreign chat %s", sender)
                    continue
                logging.getLogger("telegram_bot").info("message: %s from chat %s", text[:80], sender)
                reply = handle_message(text, sender)
                if reply:
                    try:
                        requests.post(
                            f"{base}/sendMessage",
                            json={"chat_id": sender, "text": reply, "parse_mode": "HTML"},
                            timeout=20,
                        )
                    except Exception as e:
                        logging.getLogger("telegram_bot").warning("failed to send message: %s", e)
        except Exception as exc:
            logging.getLogger("telegram_bot").warning("poll error: %s", exc)
            time.sleep(5)
        time.sleep(RATE_LIMIT_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())