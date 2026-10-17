"""Service watchdog: keeps MT5 services alive by restarting dead ones.

Monitors the pid files written by start_services.ps1 (logs/*.pid), checks that
each process is still alive, and respawns anything that died. Also verifies the
dashboards are still listening on 8050/8051.

Run:  python service_watchdog.py --interval 30
Schedule with Task Scheduler for 24/7 coverage:
    schtasks /create /tn "mt5_watchdog" /tr "python E:\\Project\\Mt5 Ea\\service_watchdog.py --interval 30" /sc minute /mo 1 /ru SYSTEM

Env vars come from .env (same file the services use).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "mt5_mcp" / ".env")

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("service_watchdog")


def _setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        LOG_DIR / "watchdog.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())


def _is_alive(pid: int) -> bool:
    # Windows: use OpenProcess with SYNCHRONIZE to check if process exists
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        # Fallback: try psutil if available
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return False


def _pid_alive(pidfile: str) -> int | None:
    """Return the pid from a pid file if that process is alive, else None."""
    p = ROOT / pidfile
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if _is_alive(pid) else None


def _find_cmd_match(pattern: str) -> list[int]:
    """Find python PIDs whose command line contains ``pattern``.

    ``wmic`` is deprecated and on some Windows builds returns nothing at all,
    which used to make every service look DOWN and spawned a duplicate every
    cycle (see logs: 52 copies of each watcher). Fall back to CIM whenever wmic
    produces no usable result.
    """
    pids = _find_cmd_match_wmic(pattern)
    if pids:
        return pids
    return _find_cmd_match_cim(pattern)


def _find_cmd_match_wmic(pattern: str) -> list[int]:
    """Legacy wmic lookup. Returns [] when wmic is missing or silent."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001 - wmic may be missing on some systems
        return []
    pids: list[int] = []
    for block in out.stdout.split("\n\n"):
        cl = ""
        pid = None
        for line in block.splitlines():
            if line.startswith("CommandLine="):
                cl = line.split("=", 1)[1]
            elif line.startswith("ProcessId="):
                try:
                    pid = int(line.split("=", 1)[1])
                except (ValueError, IndexError):
                    pid = None
        if cl and pid and pattern in cl:
            pids.append(pid)
    return pids


def _find_cmd_match_cim(pattern: str) -> list[int]:
    """PowerShell/CIM lookup used when wmic yields nothing.

    The pattern travels through the environment so no quoting is needed inside
    the PowerShell command string.
    """
    try:
        env = os.environ.copy()
        env["MT5_WD_PATTERN"] = pattern
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -like ('*' + $env:MT5_WD_PATTERN + '*') } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except Exception:  # noqa: BLE001
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pid = int(line)
            if pid not in pids:
                pids.append(pid)
    return pids


def _port_listening(port: int) -> list[int]:
    """Return pids listening on a TCP port, or [] if none."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and "LISTENING" in line and f":{port}" in line:
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
    return pids


def _write_pidfile(path: Path, pid: int) -> None:
    """Record a spawned/respawned service pid so the next cycle sees it alive."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="utf-8")
    except OSError:  # noqa: BLE001 - never let pidfile bookkeeping kill the loop
        logger.warning("could not write pidfile %s", path)


# Spawn bookkeeping: a service may only be respawned once per cooldown window.
# This is the safety net on top of the pidfile + CIM fixes -- even if liveness
# detection were wrong, the watchdog can no longer produce a respawn storm.
SPAWN_COOLDOWN_SECONDS = 120.0
_LAST_SPAWN: dict[str, float] = {}


def _cooldown_ok(name: str, now: float | None = None) -> bool:
    """True when ``name`` has not been respawned inside the cooldown window."""
    now = time.time() if now is None else now
    return (now - _LAST_SPAWN.get(name, 0.0)) >= SPAWN_COOLDOWN_SECONDS


def _mark_spawn(name: str) -> None:
    _LAST_SPAWN[name] = time.time()


def _send_restart_alert(name: str, reason: str = "DOWN") -> None:
    """Send telegram alert when a service restarts unexpectedly."""
    try:
        from mt5_mcp.telegram_alerts import send_telegram
        msg = f"⚠️ <b>Service Restarted</b>\n\n{name} was {reason} and has been respawned by watchdog."
        send_telegram(msg)
    except Exception:  # noqa: BLE001 - never let alert failure kill the watchdog
        logger.warning("failed to send restart alert for %s", name)


def _spawn(cmd: list[str], logfile: str, env: dict[str, str] | None = None):
    """Launch a service detached. Returns the Popen (or None on failure)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Don't redirect stdout/stderr; services write their own logs.
    # Use CREATE_NEW_PROCESS_GROUP so we can kill it later if needed.
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=full_env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to spawn %s", cmd[0])
        return None
    logger.info("spawned %s (pid %d)", cmd[0], proc.pid)
    return proc


def _env_for(prefix: str) -> dict[str, str]:
    """Collect MT5_MCP_* env vars for an account prefix (DERIV_/PROP_)."""
    mapping = {
        "MT5_MCP_MT5_TERMINAL_PATH": f"{prefix}_TERMINAL",
        "MT5_MCP_MT5_LOGIN": f"{prefix}_LOGIN",
        "MT5_MCP_MT5_PASSWORD": f"{prefix}_PASSWORD",
        "MT5_MCP_MT5_SERVER": f"{prefix}_SERVER",
    }
    env: dict[str, str] = {}
    for target, source in mapping.items():
        value = os.getenv(source, "")
        if value:
            env[target] = value
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=30, help="check interval in seconds")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    args = parser.parse_args()

    _setup_logging()

    services = {
        # NOTE: ``mt5_mcp/server.py`` is deliberately NOT supervised here.
        # It is a stdio MCP server that the MCP client spawns itself, and running
        # it as ``python mt5_mcp/server.py`` fails immediately: sys.path[0] is the
        # script's own directory, so ``import mt5_mcp`` raises ModuleNotFoundError.
        # The watchdog used to respawn it every cycle because of that. For a
        # long-lived HTTP server use ``run_mcp_server.bat`` (MCP_TRANSPORT=
        # streamable-http) and add an entry back here carrying that env.
        "trade_watch": {
            "pidfile": "logs/trade_watch.pid",
            "pattern": "run_synthetic_watcher.py",
            "cmd": [sys.executable, "-X", "utf8", str(ROOT / "run_synthetic_watcher.py"),
                    "--symbols",
                    "BOOM1000,BOOM900,BOOM600,BOOM500,BOOM300,BOOM200,BOOM150,BOOM100,BOOM99,BOOM50,"
                    "CRASH1000,CRASH900,CRASH600,CRASH500,CRASH300,CRASH200,CRASH150,CRASH100,CRASH99,CRASH50",
                    "--live", "--interval", "15",
                    "--confidence-floor", "60", "--max-spike-age", "60", "--lookback", "60",
                    "--log-file", str(ROOT / "logs" / "trade_watch.log")],
            "env": _env_for("DERIV"),
            "log": "logs/dbg_tradewatch.log",
        },
        "forex_watch": {
            "pidfile": "logs/forex_breakout.pid",
            "pattern": "forex_breakout_watch.py",
            "cmd": [sys.executable, str(ROOT / "forex_breakout_watch.py"), "--live", "--interval", "60",
                    "--confidence-floor", "65", "--range-bars", "24", "--rr", "1.5",
                    "--risk-percent", "0.5", "--log-file", str(ROOT / "logs" / "forex_breakout.log")],
            "env": _env_for("PROP"),
            "log": "logs/dbg_forex.log",
        },
        "btc_trend_watch": {
            "pidfile": "logs/btc_trend_watch.pid",
            "pattern": "btc_trend_watch.py",
            "cmd": [sys.executable, str(ROOT / "btc_trend_watch.py"), "--live", "--interval", "300",
                    "--ema-fast", "50", "--ema-slow", "200", "--atr-period", "14",
                    "--sl-atr", "2.0", "--trail-atr", "2.5",
                    "--risk-percent", "0.5", "--timeframe", "H4",
                    "--min-bars-between", "20",
                    "--log-file", str(ROOT / "logs" / "btc_trend_live.log")],
            "env": _env_for("PROP"),
            "log": "logs/dbg_btc_trend.log",
        },
        "telegram_bot": {
            "pidfile": "logs/telegram_bot.pid",
            "pattern": "run_telegram_bot.py",
            "cmd": [sys.executable, str(ROOT / "run_telegram_bot.py")],
            "env": {},
            "log": "logs/dbg_bot.log",
        },
        "trading_ai_api": {
            "pidfile": "logs/trading_ai_api.pid",
            "pattern": "trading_ai\\tools\\serve.py",
            "cmd": [sys.executable, "-u", str(ROOT / "trading_ai" / "tools" / "serve.py")],
            "env": {},
            "log": "logs/dbg_trading_ai.log",
        },
        # NOTE: ``ai_vision_daemon`` is not supervised: the script was retired to
        # ``archive/ai_vision_daemon.py``, so the old root path only produced a
        # spawn-and-die loop. Point the cmd at the archive path to re-enable it.
    }

    # One dashboard per account. 8050/8051 were declared separately but all three
    # ports end up serving the same login (no DERIV_*/PROP_* vars in .env), so the
    # watchdog kept resurrecting duplicates of the 8055 instance that
    # start_server.bat launches. Re-add a port here when it has its own account.
    dashboards = {
        8055: {"pattern": "web_dashboard.py --port 8055", "env": _env_for("PROP"), "log": "logs/dbg_prop.log"},
    }

    while True:
        try:
            restarted = False

            for name, cfg in services.items():
                if os.getenv(f"START_{name.upper().replace('-', '_')}", "1") == "0":
                    continue
                alive = False
                pid = _pid_alive(cfg["pidfile"])
                if pid is not None:
                    alive = True
                else:
                    pids = _find_cmd_match(cfg["pattern"])
                    if pids:
                        alive = True
                        _write_pidfile(ROOT / cfg["pidfile"], pids[0])
                if not alive:
                    if not _cooldown_ok(name):
                        logger.warning(
                            "%s looks DOWN but was respawned <%.0fs ago - skipping "
                            "(spawn cooldown)",
                            name, SPAWN_COOLDOWN_SECONDS,
                        )
                        continue
                    logger.warning("%s is DOWN - respawning", name)
                    _send_restart_alert(name, "DOWN")
                    proc = _spawn(cfg["cmd"], cfg["log"], cfg.get("env"))
                    if proc is not None:
                        _write_pidfile(ROOT / cfg["pidfile"], proc.pid)
                    _mark_spawn(name)
                    restarted = True
                    time.sleep(2)

            for port, cfg in dashboards.items():
                listeners = _port_listening(port)
                if listeners:
                    continue
                if not _cooldown_ok(f"dashboard:{port}"):
                    logger.warning(
                        "dashboard :%d looks DOWN but was respawned <%.0fs ago - "
                        "skipping (spawn cooldown)",
                        port, SPAWN_COOLDOWN_SECONDS,
                    )
                    continue
                cmd = [sys.executable, str(ROOT / "web_dashboard.py"), "--port", str(port)]
                # Only forward credentials we actually have: argparse rejects an
                # empty value (``--login ""`` -> invalid int) so the child died on
                # startup and was retried every cycle (401 log lines).
                for flag, key in (
                    ("--terminal", "MT5_MCP_MT5_TERMINAL_PATH"),
                    ("--login", "MT5_MCP_MT5_LOGIN"),
                    ("--password", "MT5_MCP_MT5_PASSWORD"),
                    ("--server", "MT5_MCP_MT5_SERVER"),
                ):
                    value = (cfg["env"].get(key) or "").strip()
                    if value:
                        cmd.extend([flag, value])
                logger.warning("dashboard :%d is DOWN - respawning", port)
                _send_restart_alert(f"dashboard:{port}", "DOWN")
                _spawn(cmd, cfg["log"], cfg["env"])
                _mark_spawn(f"dashboard:{port}")
                restarted = True
                time.sleep(2)

        except KeyboardInterrupt:
            logger.info("watchdog stopped")
            return 0
        except Exception:  # noqa: BLE001 - never let the watchdog die
            logger.exception("watchdog cycle failed")

        if args.once:
            break
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())