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
    """Find python PIDs whose command line matches a regex pattern."""
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


def _spawn(cmd: list[str], logfile: str, env: dict[str, str] | None = None) -> None:
    """Launch a service detached. Services handle their own log files via --log-file."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Don't redirect stdout/stderr; services write their own logs.
    # Use CREATE_NEW_PROCESS_GROUP so we can kill it later if needed.
    subprocess.Popen(
        cmd, cwd=ROOT, env=full_env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    logger.info("spawned %s", cmd[0])


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
        "mcp_server": {
            "pidfile": "logs/mcp_server.pid",
            "pattern": "mt5_mcp/server.py",
            "cmd": [sys.executable, str(ROOT / "mt5_mcp" / "server.py")],
            "env": _env_for("PROP"),
            "log": "logs/dbg_mcp.log",
        },
        "trade_watch": {
            "pidfile": "logs/trade_watch.pid",
            "pattern": "run_synthetic_watcher.py",
            "cmd": [sys.executable, str(ROOT / "run_synthetic_watcher.py"), "--live", "--interval", "15",
                    "--confidence-floor", "90", "--max-spike-age", "15", "--lookback", "60",
                    "--log-file", str(ROOT / "logs" / "trade_watch_live.log")],
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
            "pattern": "telegram_bot.py",
            "cmd": [sys.executable, str(ROOT / "telegram_bot.py")],
            "env": {},
            "log": "logs/dbg_bot.log",
        },
        "ai_vision": {
            "pidfile": "logs/ai_vision_daemon.pid",
            "pattern": "ai_vision_daemon.py",
            "cmd": [sys.executable, str(ROOT / "ai_vision_daemon.py")],
            "env": _env_for("DERIV"),
            "log": "logs/dbg_ai_vision.log",
        },
    }

    dashboards = {
        8050: {"pattern": "web_dashboard.py --port 8050", "env": _env_for("DERIV"), "log": "logs/dbg_deriv.log"},
        8051: {"pattern": "web_dashboard.py --port 8051", "env": _env_for("PROP"), "log": "logs/dbg_prop.log"},
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
                        with open(ROOT / cfg["pidfile"], "w", encoding="utf-8") as fh:
                            fh.write(str(pids[0]))
                if not alive:
                    logger.warning("%s is DOWN - respawning", name)
                    _spawn(cfg["cmd"], cfg["log"], cfg.get("env"))
                    restarted = True
                    time.sleep(2)

            for port, cfg in dashboards.items():
                listeners = _port_listening(port)
                if listeners:
                    continue
                logger.warning("dashboard :%d is DOWN - respawning", port)
                cmd = [sys.executable, str(ROOT / "web_dashboard.py"), "--port", str(port),
                       "--terminal", cfg["env"].get("MT5_MCP_MT5_TERMINAL_PATH", ""),
                       "--login", cfg["env"].get("MT5_MCP_MT5_LOGIN", ""),
                       "--password", cfg["env"].get("MT5_MCP_MT5_PASSWORD", ""),
                       "--server", cfg["env"].get("MT5_MCP_MT5_SERVER", "")]
                _spawn(cmd, cfg["log"], cfg["env"])
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