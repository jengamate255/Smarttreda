"""MT5 Supervisor — single owner of all background services.

Replaces the scattered scheduled tasks + rogue duplicate instances that
caused today's port conflicts (8051 hijacks, IPC storms, cascading
restarts). One process watches five children:

  gateway     mt5_gateway.py     (terminal owner, :8060)
  dashboard   web_dashboard.py   (:8050)
  desk        trade_desk.py      (scan + execution)
  watch       watch_daemon.py    (trigger alerts)
  telegram    telegram_bot.py    (control bot)

Health model per service:
  - port probe for gateway/dashboard (definitive)
  - process-alive + heartbeat file mtime for daemons (stale > 15 min = hung)
  - restart with exponential backoff (10s -> 5 min), reset after 30 min stable

Kill discipline: PID files under logs/pids/ are the source of truth.
Anything listening on our ports WITHOUT a matching PID file gets killed
(rogue detector) — that's how 8051 got hijacked repeatedly.

Run:  pythonw mt5_supervisor.py            (scheduled task "MT5 Supervisor")
      pythonw mt5_supervisor.py --once      (single check cycle, for debug)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS = BASE / "logs"
PIDS = LOGS / "pids"
# Dedicated trading venv — immune to the hermes-agent venv resets that
# wiped installed packages mid-session (2026-09-11 incident).
VENV_PYW = BASE / ".venv" / "Scripts" / "pythonw.exe"
FALLBACK_PYW = r"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
PYW = str(VENV_PYW) if VENV_PYW.exists() else FALLBACK_PYW

PIDS.mkdir(parents=True, exist_ok=True)

SERVICES: dict[str, dict] = {
    "gateway":   {"script": "mt5_gateway.py",  "port": 8060, "daemon": False},
    "dashboard": {"script": "web_dashboard.py", "port": 8050, "daemon": False},
    "desk":      {"script": "trade_desk.py",   "port": None, "daemon": True,
                  "heartbeat": "trade_desk.log"},
    "watch":     {"script": "watch_daemon.py", "port": None, "daemon": True,
                  "heartbeat": "watch_daemon.log"},
    "telegram":  {"script": "telegram_bot.py",  "port": None, "daemon": True,
                  "heartbeat": "telegram_bot.log"},
}

START_ORDER = ["gateway", "dashboard", "desk", "watch", "telegram"]
STALE_AFTER_S = 15 * 60          # daemon heartbeat staleness threshold
BACKOFF = [10, 20, 40, 90, 180, 300]   # restart delay schedule (seconds)
STABLE_RESET_S = 30 * 60         # reset backoff after 30 min healthy


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    print(line, flush=True)
    try:
        with (LOGS / "supervisor.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        # tasklist returns nonzero if pid gone; cheap and permission-safe
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                            capture_output=True, text=True, timeout=10)
        return str(pid) in r.stdout
    except Exception:
        return False


def pid_of_rogue_owner(port: int) -> int | None:
    """PID listening on `port`, ignoring our own children (None if unowned)."""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    for line in r.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5 and f":{port}" in parts[1]:
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


def read_pid(name: str) -> int | None:
    f = PIDS / f"{name}.pid"
    try:
        return int(f.read_text().strip())
    except (OSError, ValueError):
        return None


def write_pid(name: str, pid: int) -> None:
    (PIDS / f"{name}.pid").write_text(str(pid))


def clear_pid(name: str) -> None:
    (PIDS / f"{name}.pid").unlink(missing_ok=True)


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def heartbeat_age_s(name: str) -> float | None:
    """Seconds since a daemon last appended to its log (heartbeat)."""
    spec = SERVICES[name]
    hb = spec.get("heartbeat")
    if not hb:
        return None
    p = LOGS / hb
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return None


def kill_pid(pid: int) -> bool:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=15)
        return not pid_alive(pid)
    except Exception:
        return False


def start_service(name: str) -> int:
    spec = SERVICES[name]
    cmd = [PYW, "-u", "-B", str(BASE / spec["script"])]
    creationflags = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(cmd, cwd=str(BASE), creationflags=creationflags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log(f"START FAILED {name}: {exc}")
        return 0
    write_pid(name, p.pid)
    log(f"started {name} pid={p.pid} ({spec['script']})")
    return p.pid


def service_healthy(name: str) -> tuple[bool, str]:
    """(healthy, reason). Port services: port probe. Daemons: alive+heartbeat."""
    spec = SERVICES[name]
    pid = read_pid(name)
    if not pid_alive(pid):
        return False, "process not running"
    if spec["port"] is not None:
        if not port_open(spec["port"]):
            return False, f"port {spec['port']} not listening"
        return True, f"port {spec['port']} up"
    age = heartbeat_age_s(name)
    if age is None:
        # daemon with no log yet — give it grace on first start
        return True, "alive (no heartbeat file yet)"
    if age > STALE_AFTER_S:
        return False, f"heartbeat stale {age/60:.0f}m"
    return True, f"alive, heartbeat {age:.0f}s ago"


def kill_rogues() -> None:
    """Kill any process listening on our ports that isn't a tracked child."""
    for name, spec in SERVICES.items():
        port = spec["port"]
        if port is None:
            continue
        owner = pid_of_rogue_owner(port)
        if owner is None:
            continue
        mine = read_pid(name)
        if owner == mine:
            continue
        # someone else's process on our port -> kill it
        log(f"ROGUE on :{port} pid={owner} (expected {mine}) — killing")
        if kill_pid(owner):
            log(f"rogue {owner} on :{port} killed")
        else:
            log(f"could not kill rogue {owner} on :{port} (may need elevation)")


def check_cycle(state: dict) -> None:
    kill_rogues()
    for name in START_ORDER:
        healthy, reason = service_healthy(name)
        if healthy:
            state.setdefault("stable_since", {})[name] = time.time()
            continue
        log(f"UNHEALTHY {name}: {reason}")
        # kill any tracked pid that's half-alive but not serving
        pid = read_pid(name)
        if pid and pid_alive(pid) and "port" in reason:
            kill_pid(pid)
        clear_pid(name)
        now = time.time()
        stable = state.get("stable_since", {}).pop(name, now)
        if now - stable > STABLE_RESET_S:
            state["fails"] = state.get("fails", {})
            state["fails"][name] = 0
        fails = state.setdefault("fails", {}).get(name, 0)
        delay = BACKOFF[min(fails, len(BACKOFF) - 1)]
        last = state.setdefault("last_restart", {}).get(name, 0)
        if now - last < delay:
            continue
        state["last_restart"][name] = now
        state["fails"][name] = fails + 1
        start_service(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single check cycle")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    log("supervisor starting")
    state: dict = {"fails": {}, "last_restart": {}, "stable_since": {}}

    # initial clean start for anything missing
    for name in START_ORDER:
        healthy, reason = service_healthy(name)
        if not healthy:
            log(f"initial start {name} (was: {reason})")
            start_service(name)
            time.sleep(2)

    if args.once:
        time.sleep(45)
        for name in START_ORDER:
            h, why = service_healthy(name)
            log(f"  {name}: {'OK' if h else 'FAIL'} ({why})")
        return 0

    while True:
        try:
            check_cycle(state)
        except Exception as exc:  # noqa: BLE001 - watchdog must never die
            log(f"check_cycle error: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
