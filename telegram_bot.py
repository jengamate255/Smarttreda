"""Interactive Telegram control bot for the dual-terminal MT5 system.

Long-polls Telegram getUpdates and serves commands from the authorised
chat only. Data comes from the dashboard HTTP APIs where possible and
falls back to direct MT5 clients routed per symbol (Prop vs Deriv), so
the bot never fights the watchers/dashboards for connections.

Commands:
    /help                 - this list
    /status               - accounts, services, watcher state
    /positions            - open positions on both terminals
    /price SYMBOL         - live quote
    /chart SYMBOL [TF]    - annotated candlestick chart photo
    /scan                 - Boom/Crash spike scan (Deriv)
    /summary [DAYS]       - journal performance stats
    /at                   - auto trader status (JP225/EURUSD runner)
    /atpause              - engage auto trader kill switch
    /atresume             - release auto trader kill switch
    /research             - research lab verdicts (RL/JP225/Deriv)
    /heatmap              - session P&L money windows

Run:  python telegram_bot.py          (foreground)
      python telegram_bot.py --loop   (with auto-restart wrapper)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt5_mcp.telegram_alerts import _load_config, EMOJI_ROBOT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("telegram_bot")

_API = "https://api.telegram.org/bot{token}/{method}"
AUTH_TOKEN = "REDACTED_DASHBOARD_TOKEN"
DASHBOARDS = {
    "PROP": ("http://127.0.0.1:8051", "Prop"),
    "DERIV": ("http://127.0.0.1:8050", "Deriv"),
}
DERIV_HINTS = ("boom", "crash", "volatility", "step", "jump", "dx")


def _dash_get(terminal: str, path: str, timeout: float = 8.0) -> dict | None:
    base, _ = DASHBOARDS[terminal]
    url = f"{base}{path}"
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{url}{sep}token={AUTH_TOKEN}")
    req.add_header("X-Auth-Token", AUTH_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        log.debug("dash %s %s failed: %s", terminal, path, exc)
        return None


def route_for(symbol: str) -> str:
    s = symbol.lower()
    return "DERIV" if any(h in s for h in DERIV_HINTS) else "PROP"


def resolve_symbol(symbol: str) -> str:
    """Map API-style names (BOOM500) to MT5 names (Boom 500 Index)."""
    if route_for(symbol) == "DERIV":
        from mt5_mcp.tools.boom_crash_strategy import mt5_symbol

        return mt5_symbol(symbol)
    return symbol


def _client(terminal: str):
    from mt5_mcp.client import get_client

    os.environ["MT5_MCP_ACCOUNT"] = terminal
    return get_client()


# ------------------------------------------------------------------ helpers

def fmt_money(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.2f}"


def cmd_help() -> str:
    return (
        f"{EMOJI_ROBOT} <b>MT5 Assistant</b>\n"
        "/status - accounts &amp; services\n"
        "/positions - open positions (both)\n"
        "/price SYMBOL - live quote\n"
        "/chart SYMBOL [M1|M5|M15|H1|H4|D1] - chart photo\n"
        "/scan - Boom/Crash spike scan\n"
        "/summary [days] - performance stats\n"
        "/at - auto trader status\n"
        "/atpause - pause auto trader (kill switch)\n"
        "/atresume - resume auto trader\n"
        "/research - research lab verdicts\n"
        "/heatmap - session P&L money windows\n"
        "/close TICKET - close a position\n"
        "/halt - stop spike watcher\n"
        "/resume - restart watcher\n"
        "/help - commands"
    )


def cmd_status() -> str:
    lines = [f"{EMOJI_ROBOT} <b>SYSTEM STATUS</b>"]
    from mt5_mcp.client import get_client

    for term in ("PROP", "DERIV"):
        label = DASHBOARDS[term][1]
        try:
            c = _client(term)
            c.ensure_connected()
            acct = c.account_info()
            lines.append(
                f"{label}: ${acct.get('balance', 0):.2f} "
                f"(eq ${acct.get('equity', 0):.2f})"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{label}: OFFLINE ({str(exc)[:40]})")
    # watcher heartbeat from log mtime
    wl = Path(__file__).parent / "logs" / "trade_watch_live.log"
    if wl.exists():
        age = time.time() - wl.stat().st_mtime
        state = "alive" if age < 120 else f"quiet {int(age//60)}m"
        lines.append(f"Watcher: {state}")
    return "\n".join(lines)


def cmd_positions() -> str:
    lines = [f"{EMOJI_ROBOT} <b>OPEN POSITIONS</b>"]
    total = 0.0
    any_found = False
    for term in ("PROP", "DERIV"):
        label = DASHBOARDS[term][1]
        try:
            c = _client(term)
            c.ensure_connected()
            positions = c.positions() or []
            if not positions:
                lines.append(f"{label}: none")
                continue
            any_found = True
            for p in positions[:8]:
                pnl = float(p.get("profit", 0.0))
                total += pnl
                icon = "+" if pnl >= 0 else ""
                lines.append(
                    f"{label} {p.get('symbol')} {p.get('type','')} {p.get('volume')} "
                    f"@ {p.get('price_open')} -> {icon}{pnl:.2f}"
                )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{label}: offline")
    lines.insert(1, f"Total floating: {fmt_money(total)} USD")
    if not any_found:
        pass
    return "\n".join(lines)


def cmd_price(symbol: str) -> str:
    if not symbol:
        return "Usage: /price EURUSD"
    try:
        term = route_for(symbol)
        c = _client(term)
        name = resolve_symbol(symbol)
        tick = c.tick(name)
        bid, ask = float(tick.get("bid", 0)), float(tick.get("ask", 0))
        spread = tick.get("spread", ask - bid)
        return (
            f"<b>{name}</b> [{DASHBOARDS[term][1]}]\n"
            f"bid {bid:.5g} / ask {ask:.5g}\nspread {spread}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"No quote for {symbol}: {str(exc)[:60]}"


def _render_chart_png(df, title: str) -> bytes:
    """Minimal candlestick + EMA50/200 render -> PNG bytes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    closes = df["close"]
    ema50 = closes.ewm(span=50, adjust=False).mean()
    ema200 = closes.ewm(span=min(200, max(len(closes) - 1, 2)), adjust=False).mean()

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=110)
    xs = range(len(df))
    w = 0.6
    for i in xs:
        o, h, l, c = df["open"][i], df["high"][i], df["low"][i], df["close"][i]
        color = "#26a69a" if c >= o else "#ef5350"
        ax.plot([i, i], [l, h], color=color, linewidth=0.7)
        ax.plot([i - w / 2, i + w / 2], [o, o], color=color, linewidth=1.4)
        ax.plot([i - w / 2, i + w / 2], [c, c], color=color, linewidth=1.4)
    ax.plot(xs, ema50, color="#f39c12", linewidth=1.1, label="EMA50")
    ax.plot(xs, ema200, color="#2962ff", linewidth=1.1, label="EMA200")
    ax.set_title(title, fontsize=11)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def cmd_chart(symbol: str, timeframe: str = "M1") -> tuple[str, bytes | None]:
    if not symbol:
        return "Usage: /chart BOOM500 H1", None
    tf = timeframe.upper() if timeframe else "M1"
    if tf not in {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}:
        tf = "M1"
    tf_seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[tf]
    count = 220
    try:
        term = route_for(symbol)
        c = _client(term)
        name = resolve_symbol(symbol)
        frame = c.rates(name, tf, count)
        if frame is None or len(frame) == 0:
            return f"No data for {symbol} {tf}", None
        import pandas as pd

        df = pd.DataFrame(frame).tail(count).reset_index(drop=True)
        png = _render_chart_png(df, f"{name} {tf}")
        last_close = float(df['close'].iloc[-1])
        caption = f"<b>{name}</b> {tf}\nlast: {last_close:.5g}"
        return caption, png
    except Exception as exc:  # noqa: BLE001
        return f"Chart failed: {str(exc)[:70]}", None


def cmd_scan() -> str:
    try:
        os.environ["MT5_MCP_ACCOUNT"] = "DERIV"
        c = _client("DERIV")
        c.ensure_connected()
        symbols = ["Boom 1000 Index", "Crash 1000 Index", "Boom 500 Index",
                   "Crash 500 Index", "Boom 300 Index", "Crash 300 Index"]
        lines = [f"{EMOJI_ROBOT} <b>SPIKE SCAN</b>"]
        found = 0
        from mt5_mcp.tools.boom_crash_strategy import detect_spike

        for s in symbols:
            try:
                sig = detect_spike(s.replace(" Index", ""), lookback_minutes=60,
                                   min_spike_ticks=10)
            except Exception:  # noqa: BLE001
                continue
            if sig:
                found += 1
                lines.append(
                    f"{s}: {sig.direction.upper()} conf={sig.confidence:.0f}% "
                    f"entry~{sig.entry_price:.2f}"
                )
        if not found:
            lines.append("no qualifying spikes right now")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Scan failed: {str(exc)[:80]}"


def cmd_summary(days: int = 7) -> str:
    try:
        from mt5_mcp.memory.trade_journal import get_journal

        j = get_journal()
        con = getattr(j, "_connect", None)
        import sqlite3
        from pathlib import Path as _P

        db_path = _P(j._path)
        con2 = sqlite3.connect(str(db_path))
        con2.row_factory = sqlite3.Row
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = con2.execute(
            "SELECT outcome, COUNT(*) n, SUM(COALESCE(pnl,0)) pnl FROM trades "
            "WHERE COALESCE(close_time, created_at,'') >= ? GROUP BY outcome",
            (since,),
        ).fetchall()
        total = sum(r["n"] for r in rows)
        wins = next((r["n"] for r in rows if r["outcome"] == "win"), 0)
        losses = next((r["n"] for r in rows if r["outcome"] == "loss"), 0)
        pnl = sum(r["pnl"] or 0 for r in rows)
        con2.close()
        wr = wins / total * 100 if total else 0
        icon = "\U0001F4C8" if pnl >= 0 else "\U0001F4C9"
        return (
            f"{icon} <b>{days}-DAY SUMMARY</b>\n"
            f"trades: {total} | WR {wr:.0f}%\n"
            f"net: {fmt_money(pnl)} USD\n"
            f"w/l: {wins}/{losses}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"Summary failed: {str(exc)[:80]}"


def cmd_at() -> str:
    """Auto trader status: task, kill switch, cycles, per-symbol plans, validation."""
    data = _dash_get("PROP", "/api/autotrader/status")
    if not data or data.get("error"):
        return f"Auto trader API unavailable: {str(data.get('error', 'offline'))[:60] if data else 'offline'}"
    running = data.get("task_state") in ("Running", "Ready")
    killed = bool(data.get("kill_switch"))
    state = "PAUSED (kill switch)" if killed else ("ACTIVE" if running else data.get("task_state", "?"))

    lines = [f"🤖 <b>AUTO TRADER</b> — {state}"]
    lines.append(f"cycles: {data.get('cycles', 0)} | task: {data.get('task_state', '?')}")

    plans = data.get("plans") or {}
    for sym, p in plans.items():
        conf = p.get("confidence")
        conf_s = f"{conf:.1f}%" if isinstance(conf, (int, float)) else "--"
        ok = isinstance(conf, (int, float)) and conf >= 90
        icon = "🟢" if ok else "⚪"
        reason = (p.get("reason") or "--")[:50]
        lines.append(f"{icon} {sym}: {p.get('action', '?')} conf {conf_s}\n    ↳ {reason}")

    v = data.get("last_validation") or {}
    if v.get("available"):
        age = v.get("age_days")
        action = str(v.get("action", "--")).split(" - ")[0]
        v_icon = "⚠️" if v.get("overdue") else ("✅" if action.startswith("CONTINUE") else "🛑")
        lines.append(f"{v_icon} validation: {action} ({age}d ago)")
    else:
        lines.append("⚠️ validation: never run")
    return "\n".join(lines)


def cmd_atpause() -> str:
    """Engage the auto trader kill switch (pauses at next cycle)."""
    import urllib.request

    base = DASHBOARDS["PROP"][0]
    req = urllib.request.Request(
        f"{base}/api/autotrader/kill?token={AUTH_TOKEN}",
        method="POST",
        headers={"X-Auth-Token": AUTH_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
        if ok:
            return ("⏸ <b>AUTO TRADER PAUSED</b>\n"
                    "Kill switch engaged — pauses at the next cycle.\n"
                    "Open positions are NOT closed. /atresume to restart.")
        return f"Pause failed: HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return f"Pause failed: {str(exc)[:70]}"


def cmd_atresume() -> str:
    """Release the auto trader kill switch."""
    import urllib.request

    base = DASHBOARDS["PROP"][0]
    req = urllib.request.Request(
        f"{base}/api/autotrader/kill?token={AUTH_TOKEN}",
        method="DELETE",
        headers={"X-Auth-Token": AUTH_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
        if ok:
            return "▶️ <b>AUTO TRADER RESUMED</b>\nKill switch released — trading continues."
        return f"Resume failed: HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return f"Resume failed: {str(exc)[:70]}"


def cmd_research() -> str:
    """Research lab verdicts: RL, JP225 price-action, Deriv spike-drift."""
    data = _dash_get("PROP", "/api/dojo/research")
    if not data or data.get("error"):
        return "Research API unavailable (dashboards offline?)"

    lines = ["🔬 <b>RESEARCH LAB</b>"]
    rl = data.get("rl_verdict") or {}
    lines.append(f"🧠 RL: {rl.get('summary', '?')}")
    base = rl.get("honest_baseline") or []
    if base:
        nets = ", ".join(f"{b['symbol']} {b['pf']:.2f}" for b in base)
        lines.append(f"    honest PF: {nets}")
    pa = data.get("price_action_verdict") or {}
    jp = pa.get("jp225") or {}
    lines.append(f"🎯 JP225: {pa.get('summary', '?')}")
    lines.append(f"    {jp.get('stats', '')}")
    dv = data.get("deriv_verdict") or {}
    lines.append(f"⚡ Deriv: {dv.get('summary', '?')}")
    good = ", ".join(dv.get("sessions_good") or [])
    bad = ", ".join(dv.get("sessions_bad") or [])
    lines.append(f"    good: {good} | avoid: {bad}")
    return "\n".join(lines)


def cmd_heatmap() -> str:
    """Session P&L money windows: best and worst UTC hours."""
    data = _dash_get("PROP", "/api/session-heatmap?days=120")
    if not data or data.get("error"):
        return "Heat-map API unavailable (dashboards offline?)"
    hours = data.get("hours") or {}
    if not hours:
        return "No session data in the journal yet"
    rows = sorted(
        ((int(k), v) for k, v in hours.items()),
        key=lambda kv: -(kv[1].get("net") or 0),
    )
    top = rows[:3]
    worst = rows[-3:][::-1]

    def fmt_row(hv):
        h, v = hv
        net = v.get("net") or 0
        sign = "+" if net >= 0 else ""
        return f"{h:02d}:00 — {sign}{net:.2f} ({v.get('trades', 0)}t, WR {v.get('win_rate', 0):.0f}%)"

    lines = ["🗺 <b>SESSION P&L (UTC)</b>"]
    lines.append("Money windows:")
    lines.extend(f"  🟢 {fmt_row(r)}" for r in top)
    lines.append("Dead zones:")
    lines.extend(f"  🔴 {fmt_row(r)}" for r in worst)
    return "\n".join(lines)


# ------------------------------------------------------------------ plumbing

def _watcher_pids():
    """PIDs of running synthetic watcher processes."""
    pids = []
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            if "run_synthetic_watcher" in line:
                tail = line.rstrip().split(",")[-1]
                if tail.isdigit():
                    pids.append(int(tail))
    except Exception as exc:  # noqa: BLE001
        log.warning("watcher pid scan failed: %s", exc)
    return pids


def cmd_close(ticket: int) -> str:
    """Close a position by ticket (searches both terminals)."""
    if not ticket:
        return "Usage: /close TICKET"
    from mt5_mcp.tools.trades import close_position

    for term in ("PROP", "DERIV"):
        try:
            c = _client(term)
            c.ensure_connected()
            for pos in c.positions() or []:
                if int(pos.get("ticket", 0)) == ticket:
                    result = close_position(ticket)
                    if getattr(result, "accepted", False):
                        return (
                            f"\u2705 Closed #{ticket} on {DASHBOARDS[term][1]} "
                            f"({pos.get('symbol')})"
                        )
                    return f"Close rejected: {getattr(result, 'error', 'unknown')}"
        except Exception as exc:  # noqa: BLE001
            log.warning("close %s on %s: %s", ticket, term, exc)
    return f"Ticket #{ticket} not found on either terminal"


def cmd_halt() -> str:
    """Kill the spike watcher process (trading pause)."""
    pids = _watcher_pids()
    if not pids:
        return "Watcher is not running."
    import psutil  # optional dep, falls back below

    killed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
            killed.append(pid)
        except Exception as exc:  # noqa: BLE001
            log.warning("halt kill %s: %s", pid, exc)
    del psutil
    return (
        f"\U0001F6D1 <b>HALTED</b> - watcher stopped (pid {', '.join(map(str, killed))}).\n"
        "Send /resume to restart spike monitoring."
    )


def cmd_resume() -> str:
    """Restart the spike watcher after a halt."""
    if _watcher_pids():
        return "Watcher already running."
    py = sys.executable
    wd = Path(__file__).resolve().parent
    subprocess.Popen(
        [py, "-X", "utf8", "-u", "run_synthetic_watcher.py", "--live",
         "--symbols", "BOOM500,BOOM600,BOOM900", "--interval", "15",
         "--confidence-floor", "90", "--max-spike-age", "15",
         "--lookback", "60", "--log-file", "logs/trade_watch_live.log"],
        cwd=str(wd), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(2)
    return "\u2705 Watcher restarting with BOOM500 config."


def _seconds_until_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0)
    return (nxt - now).total_seconds()


def midnight_summary_loop(token: str, chat_id: int) -> None:
    """Daily P&L summary pushed at ~00:00:05 UTC (item #22)."""
    while True:
        time.sleep(max(30.0, _seconds_until_utc_midnight()))
        try:
            text = cmd_summary(1).replace("<b>", "").replace("</b>", "")
            api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})
        except Exception as exc:  # noqa: BLE001
            log.warning("midnight summary failed: %s", exc)



def handle_message(text: str) -> tuple[str, bytes | None]:
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    if cmd == "/start" or cmd == "/help":
        return cmd_help(), None
    if cmd == "/status":
        return cmd_status(), None
    if cmd == "/positions":
        return cmd_positions(), None
    if cmd == "/price":
        return cmd_price(args[0] if args else ""), None
    if cmd == "/chart":
        return cmd_chart(args[0] if args else "", args[1] if len(args) > 1 else "M1")
    if cmd == "/scan":
        return cmd_scan(), None
    if cmd == "/summary":
        d = int(args[0]) if args and args[0].isdigit() else 7
        return cmd_summary(min(d, 90)), None
    if cmd == "/at":
        return cmd_at(), None
    if cmd == "/atpause":
        return cmd_atpause(), None
    if cmd == "/atresume":
        return cmd_atresume(), None
    if cmd == "/research":
        return cmd_research(), None
    if cmd == "/heatmap":
        return cmd_heatmap(), None
    if cmd == "/close":
        try:
            return cmd_close(int(args[0])), None
        except (ValueError, IndexError):
            return "Usage: /close TICKET", None
    if cmd == "/halt":
        return cmd_halt(), None
    if cmd == "/resume":
        return cmd_resume(), None
    return "Unknown command. Try /help", None


def api_call(token: str, method: str, payload: dict | None = None, timeout: int = 35):
    import requests

    try:
        resp = requests.post(_API.format(token=token, method=method),
                             json=payload or {}, timeout=timeout)
        data = resp.json()
        if data.get("ok"):
            return data["result"]
        log.warning("%s failed: %s", method, data)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s error: %s", method, exc)
    return None


# ------------------------------------------------------------------ watchdog

WATCHER_LOG = Path(__file__).resolve().parent / "logs" / "trade_watch_live.log"
WATCHDOG_INTERVAL = 60  # seconds
WATCHDOG_STALE = 180    # log older than this => watcher likely dead


def _watcher_alive() -> bool:
    """Watcher is alive if its process exists AND the log ticked recently."""
    proc_alive = False
    try:
        import psutil  # optional
        for p in psutil.process_iter(["cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "run_synthetic_watcher.py" in cl:
                proc_alive = True
                break
    except ImportError:
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            proc_alive = "run_synthetic_watcher" in out
        except Exception:  # noqa: BLE001
            proc_alive = True  # can't tell -> don't false-alarm on this signal
    log_fresh = False
    if WATCHER_LOG.exists():
        log_fresh = (time.time() - WATCHER_LOG.stat().st_mtime) < WATCHDOG_STALE
    # Quiet logging when no signals is normal -> require BOTH signals dead.
    return proc_alive or log_fresh


def watchdog_loop(token: str, chat_id: int) -> None:
    """Alert once when the synthetic watcher goes down; again if it stays down hourly."""
    alerted_at = 0.0
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        try:
            alive = _watcher_alive()
        except Exception as exc:  # noqa: BLE001
            log.warning("watchdog check error: %s", exc)
            continue
        now = time.time()
        if not alive and now - alerted_at > 3600:
            api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "\U0001F6A8 <b>WATCHER DOWN</b>\nSpike watcher process not found "
                        f"and log stale &gt;{180}s.\nCheck terminal + "
                        "<code>start_services.ps1</code>",
            })
            alerted_at = now
            log.warning("watchdog: WATCHER DOWN alert sent")
        elif alive and alerted_at:
            api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "\u2705 <b>Watcher recovered</b> - spike monitoring back online",
            })
            alerted_at = 0.0
            log.info("watchdog: watcher recovered")


AUTOTRADER_POLL = 30  # seconds between checks

def autotrader_alert_loop(token: str, chat_id: int) -> None:
    """Alert on auto-trader trade fills, kill-switch events, and validation warnings."""
    last_cycles = 0
    last_killed = None
    last_validation_action = None
    alerted_at = 0.0
    while True:
        time.sleep(AUTOTRADER_POLL)
        try:
            data = _dash_get("PROP", "/api/autotrader/status")
            if not data or data.get("error"):
                continue
        except Exception as exc:  # noqa: BLE001
            log.debug("autotrader alert poll failed: %s", exc)
            continue

        now = time.time()
        cycles = data.get("cycles", 0)
        killed = bool(data.get("kill_switch"))
        task_state = data.get("task_state", "")

        # --- new cycle (trade fill) ---
        if cycles > last_cycles and last_cycles > 0:
            delta = cycles - last_cycles
            plans = data.get("plans") or {}
            fills = []
            for sym, p in plans.items():
                conf = p.get("confidence")
                conf_s = f"{conf:.1f}%" if isinstance(conf, (int, float)) else "--"
                fills.append(f"  {sym} {p.get('action', '?')} conf={conf_s}")
            text = (
                f"🤖 <b>AUTO TRADER CYCLE</b> (×{delta})\n"
                + "\n".join(fills[:4])
            )
            api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})
            log.info("autotrader alert: %d new cycle(s)", delta)

        last_cycles = cycles

        # --- kill-switch state change ---
        if last_killed is not None and killed != last_killed:
            if killed:
                api_call(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "⏸ <b>AUTO TRADER PAUSED</b> (kill switch engaged)\n"
                            "Open positions kept open. /atresume to restart.",
                })
                log.info("autotrader alert: kill switch engaged")
            else:
                api_call(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "▶️ <b>AUTO TRADER RESUMED</b>\nKill switch released.",
                })
                log.info("autotrader alert: kill switch released")
        last_killed = killed

        # --- validation overdue warning (once per day) ---
        v = data.get("last_validation") or {}
        v_action = v.get("action", "")
        if v.get("overdue") and v_action != last_validation_action:
            if now - alerted_at > 86400:
                api_call(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        f"⚠️ <b>VALIDATION OVERDUE</b>\n"
                        f"Last validation: {v.get('age_days', '?')}d ago\n"
                        f"Action: {v_action}\n"
                        f"Run <code>validate_jp225_monthly.py</code> or check Auto Trader panel."
                    ),
                })
                alerted_at = now
        last_validation_action = v_action


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram control bot")
    ap.add_argument("--once-poll", action="store_true", help="poll once then exit (debug)")
    args = ap.parse_args()

    token, chat_id = _load_config()
    if not token or not chat_id:
        print("telegram not configured: set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return 1

    send_to = int(chat_id)
    api_call(token, "sendMessage",
             {"chat_id": send_to, "text": f"{EMOJI_ROBOT} MT5 assistant online. /help"})
    # Watchdog: alert Telegram if the spike watcher dies (item #3).
    threading.Thread(target=watchdog_loop, args=(token, send_to), daemon=True).start()
    threading.Thread(target=midnight_summary_loop, args=(token, send_to), daemon=True).start()
    threading.Thread(target=autotrader_alert_loop, args=(token, send_to), daemon=True).start()
    offset = 0
    while True:
        updates = api_call(token, "getUpdates",
                           {"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
                           timeout=40)
        for upd in updates or []:
            offset = max(offset, upd["update_id"] + 1)
            msg = upd.get("message") or {}
            chat = msg.get("chat", {})
            if chat.get("id") != send_to:
                continue  # unauthorised chatter ignored
            text = msg.get("text", "")
            if not text.startswith("/"):
                continue
            log.info("command: %s", text)
            try:
                reply, png = handle_message(text)
            except Exception as exc:  # noqa: BLE001
                reply, png = f"error: {str(exc)[:100]}", None
            if png is not None:
                import requests

                try:
                    requests.post(_API.format(token=token, method="sendPhoto"),
                                  data={"chat_id": send_to, "caption": reply,
                                        "parse_mode": "HTML"},
                                  files={"photo": png}, timeout=45)
                except Exception as exc:  # noqa: BLE001
                    log.warning("sendPhoto error: %s", exc)
                    api_call(token, "sendMessage", {"chat_id": send_to, "text": reply})
            else:
                api_call(token, "sendMessage", {"chat_id": send_to, "text": reply})
        if args.once_poll:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
