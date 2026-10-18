"""Unified startup protocol for the MT5 trading stack.

Single entry point that launches all services as hidden subprocesses,
health-checks each, and sends one Telegram status card.

Usage:
    python startup.py          # launch all + status card
    python startup.py --dry    # check without starting
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

load_dotenv(BASE / ".env")
load_dotenv(BASE / "telegram.env")

PYTHONW = Path(
    os.getenv(
        "MT5_PYTHONW",
        r"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe",
    )
)
PYTHON = Path(
    os.getenv(
        "MT5_PYTHON",
        r"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe",
    )
)

logging.basicConfig(
    format="%(asctime)s %(levelname)-5s %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "startup.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("startup")

SERVICES = [
    {
        "name": "Gateway",
        "script": "mt5_gateway.py",
        "port": 8060,
        "env": {},
    },
    {
        "name": "Dashboard",
        "script": "web_dashboard.py",
        "port": 8050,
        "env": {"WEB_PORT": "8050", "MT5_MCP_ACCOUNT": "PROP"},
    },
    {
        "name": "WatchDaemon",
        "script": "watch_daemon.py",
        "port": None,
        "env": {},
    },
    {
        "name": "TradeDesk",
        "script": "trade_desk.py",
        "port": None,
        "env": {},
    },
    {
        "name": "ConfirmExec",
        "script": "confirm_execute_daemon.py",
        "port": None,
        "env": {},
    },
    {
        "name": "Analysis",
        "script": "analysis_agent.py",
        "port": None,
        "env": {},
    },
    {
        "name": "Watchdog",
        "script": "service_watchdog.py",
        "args": ["--interval", "30"],
        "port": None,
        "env": {},
        "use_python": True,
    },
]

LAUNCH_DELAY = 1.5
HEALTH_WAIT = 8


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _launch(svc: dict) -> subprocess.Popen | None:
    script = BASE / svc["script"]
    if not script.exists():
        log.warning("  %s: %s not found, skipping", svc["name"], svc["script"])
        return None

    exe = PYTHON if svc.get("use_python") else PYTHONW
    if not exe.exists():
        log.error("  %s: %s not found", svc["name"], exe)
        return None

    env = os.environ.copy()
    env.update(svc.get("env", {}))

    cmd = [str(exe), "-u", "-B", str(script)] + svc.get("args", [])

    kwargs: dict = {"env": env, "cwd": str(BASE)}
    if sys.platform == "win32":
        kwargs.update(
            creationflags=subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    return subprocess.Popen(cmd, **kwargs)


def _check_health(svc: dict) -> str:
    port = svc.get("port")
    if port:
        if _port_open(port):
            return f"OK :{port}"
        return f"FAIL :{port}"
    return "running"


def _build_status_card(results: list[dict], elapsed: float) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "<b>MT5 Trading Stack</b>",
        f"<i>{now}</i>",
        "",
    ]

    ok = sum(1 for r in results if r["status"] not in ("FAIL", "SKIP") and not r["status"].startswith("FAIL"))
    total = len(results)

    for r in results:
        healthy = r["status"] not in ("FAIL", "SKIP") and not r["status"].startswith("FAIL")
        icon = "\u2705" if healthy else "\u274c"
        detail = r["detail"] or ""
        if detail:
            detail = f" <i>({detail})</i>"
        lines.append(f"{icon} {r['name']}: {r['status']}{detail}")

    lines.append("")
    lines.append(f"<b>{ok}/{total}</b> services up in {elapsed:.1f}s")

    telegram_managed = os.getenv("START_TELEGRAM_BOT", "0") == "0"
    if telegram_managed:
        lines.append("<i>Telegram: MT5R_Telegram (scheduled task)</i>")

    return "\n".join(lines)


def _send_status(results: list[dict], elapsed: float) -> None:
    try:
        from mt5_mcp.notify import notify

        html = _build_status_card(results, elapsed)
        notify("startup_status", html, force=True)
    except Exception as exc:
        log.warning("Telegram status failed: %s", exc)


def main() -> int:
    dry = "--dry" in sys.argv

    log.info("=" * 50)
    log.info("MT5 Trading Stack Startup")
    log.info("=" * 50)

    if not PYTHONW.exists() and not dry:
        log.error("pythonw not found: %s", PYTHONW)
        return 1

    results: list[dict] = []
    t0 = time.monotonic()

    for svc in SERVICES:
        name = svc["name"]

        if dry:
            port_info = f" :{svc['port']}" if svc.get("port") else ""
            alive = _port_open(svc["port"]) if svc.get("port") else None
            if alive is True:
                status = "ALREADY UP"
            elif alive is False:
                status = "not running"
            else:
                status = "no port check"
            log.info("  %-14s %s%s", name, status, port_info)
            results.append({"name": name, "status": status, "detail": ""})
            continue

        log.info("  Starting %-14s ...", name)
        proc = _launch(svc)
        if proc is None:
            results.append({"name": name, "status": "SKIP", "detail": "script not found"})
            continue

        time.sleep(LAUNCH_DELAY)

        if proc.poll() is not None:
            log.warning("  %s exited immediately (code %s)", name, proc.returncode)
            results.append(
                {"name": name, "status": "FAIL", "detail": f"exit {proc.returncode}"}
            )
        else:
            results.append(
                {"name": name, "status": "started", "detail": f"pid {proc.pid}"}
            )

    if not dry:
        log.info("  Waiting %ds for services to bind...", HEALTH_WAIT)
        time.sleep(HEALTH_WAIT)

        for r, svc in zip(results, SERVICES):
            if r["status"] == "started":
                health = _check_health(svc)
                r["status"] = health
                r["detail"] = ""

    elapsed = time.monotonic() - t0

    log.info("-" * 50)
    for r in results:
        healthy = r["status"] not in ("FAIL", "SKIP") and not r["status"].startswith("FAIL")
        icon = "OK" if healthy else "!!"
        log.info("  [%s] %-14s %s", icon, r["name"], r["status"])

    ok_count = sum(1 for r in results if r["status"] not in ("FAIL", "SKIP") and not r["status"].startswith("FAIL"))
    log.info("-" * 50)
    log.info("  %d/%d up in %.1fs", ok_count, len(results), elapsed)
    log.info("=" * 50)

    if not dry:
        _send_status(results, elapsed)

    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
