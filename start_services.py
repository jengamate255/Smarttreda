"""Start MT5 services with proper logging and health checks.

Usage:
    python start_services.py              # start dashboard (port 8055)
    python start_services.py --terminal deriv  # start Deriv dashboard (port 8052)
    python start_services.py --restart       # kill + restart everything
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ─── Configuration ────────────────────────────────────────────────────
PROP = {
    "port": 8055,
    "terminal": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    "login": 11911448,
    "password": "",
    "server": "FundingPips2-SIM",
}
DERIV = {
    "port": 8052,
    "terminal": r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe",
    "login": 21727841,
    "password": "REDACTED_MT5_PASSWORD$",
    "server": "DerivVU-Server",
}


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, level="INFO"):
    print(f"[{ts()}] [{level:>5}] {msg}", flush=True)


def find_processes(pattern):
    """Find python.exe PIDs matching a command-line pattern."""
    import re
    out = subprocess.check_output(
        ["wmic", "process", "where", "Name='python.exe'", "get",
         "ProcessId,CommandLine", "/format:list"],
        text=True, stderr=subprocess.DEVNULL
    )
    pids = []
    current_cmd = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CommandLine="):
            current_cmd = line[len("CommandLine="):]
        elif line.startswith("ProcessId="):
            pid = int(line[len("ProcessId="):])
            if re.search(pattern, current_cmd, re.IGNORECASE):
                pids.append(pid)
    return pids


def kill_old_services():
    """Kill existing dashboard processes on our ports."""
    for port in [8055, 8052]:
        pids = find_processes(f"port.*{port}|{port}.*web_dashboard")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                log(f"Killed PID {pid} on port {port}")
            except ProcessLookupError:
                pass
            except PermissionError:
                log(f"Cannot kill PID {pid} (access denied)", "WARN")
        # Also kill by port
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pid = int(parts[-1])
                        if pid > 4:
                            try:
                                os.kill(pid, signal.SIGTERM)
                                log(f"Killed PID {pid} listening on :{port}")
                            except (ProcessLookupError, PermissionError):
                                pass
        except Exception:
            pass


def start_dashboard(cfg, account_label):
    """Start the web dashboard as a subprocess with proper logging."""
    log_file = os.path.join(LOG_DIR, f"dashboard_{account_label.lower()}.log")
    cmd = [
        sys.executable, "-X", "utf8", "-u",
        os.path.join(BASE_DIR, "web_dashboard.py"),
        "--port", str(cfg["port"]),
        "--terminal", cfg["terminal"],
        "--login", str(cfg["login"]),
    ]
    if cfg.get("password"):
        cmd += ["--password", cfg["password"]]
    if cfg.get("server"):
        cmd += ["--server", cfg["server"]]

    env = os.environ.copy()
    if account_label == "Prop":
        env["MT5_MCP_MT5_TERMINAL_PATH"] = cfg["terminal"]
        env["MT5_MCP_MT5_LOGIN"] = str(cfg["login"])
        env["MT5_MCP_MT5_SERVER"] = cfg.get("server", "")
        env["MT5_MCP_ACCOUNT"] = "PROP"
    else:  # Deriv
        env["MT5_MCP_DERIV_MT5_TERMINAL_PATH"] = cfg["terminal"]
        env["MT5_MCP_DERIV_MT5_LOGIN"] = str(cfg["login"])
        env["MT5_MCP_DERIV_MT5_SERVER"] = cfg.get("server", "")
        env["MT5_MCP_DERIV_MT5_PASSWORD"] = cfg.get("password", "")
        env["MT5_MCP_ACCOUNT"] = "DERIV"

    log(f"Starting {account_label} dashboard on port {cfg['port']}...")
    log(f"Log file: {log_file}")

    log_fh = open(log_file, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        env=env,
        # creationflags=0x08000000,  # CREATE_NO_WINDOW - causes silent crash with MT5
    )
    log(f"{account_label} dashboard PID: {proc.pid}")
    return proc, log_fh


def wait_for_health(port, timeout=90):
    """Poll /api/ping until the dashboard is ready."""
    import urllib.request
    import json
    auth_token = "REDACTED_DASHBOARD_TOKEN"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping")
            req.add_header("X-Auth-Token", auth_token)
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def show_log_tail(log_file, lines=15):
    """Print the last N lines of a log file."""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            tail = all_lines[-lines:]
            if tail:
                log(f"--- Last {len(tail)} lines of {os.path.basename(log_file)} ---")
                for line in tail:
                    print(f"  {line.rstrip()}")
    except FileNotFoundError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Start MT5 services")
    parser.add_argument("--terminal", choices=["prop", "deriv", "both"], default="prop",
                        help="Which terminal to start (default: prop)")
    parser.add_argument("--restart", action="store_true", help="Kill existing + restart")
    parser.add_argument("--status", action="store_true", help="Show status only")
    parser.add_argument("--logs", action="store_true", help="Tail log files")
    args = parser.parse_args()

    if args.logs:
        for name in ["dashboard_prop", "dashboard_deriv", "web_dashboard"]:
            lf = os.path.join(LOG_DIR, f"{name}.log")
            if os.path.exists(lf):
                show_log_tail(lf, 25)
        return

    log("=" * 60)
    log("MT5 Services Manager")
    log("=" * 60)

    if args.restart:
        log("Restarting — killing old processes...")
        kill_old_services()
        time.sleep(2)

    procs = []
    if args.terminal in ("prop", "both"):
        p, f = start_dashboard(PROP, "Prop")
        procs.append((p, f, "Prop", PROP["port"]))
    if args.terminal in ("deriv", "both"):
        p, f = start_dashboard(DERIV, "Deriv")
        procs.append((p, f, "Deriv", DERIV["port"]))

    # Wait for health checks
    log("Waiting for dashboards to start...")
    for proc, fh, label, port in procs:
        if wait_for_health(port, timeout=90):
            log(f"  {label} dashboard: HEALTHY on :{port}")
        else:
            log(f"  {label} dashboard: TIMEOUT on :{port}", "ERROR")
            show_log_tail(os.path.join(LOG_DIR, f"dashboard_{label.lower()}.log"), 10)

    log("=" * 60)
    log("Services running. Logs in: " + LOG_DIR)
    log("Press Ctrl+C to stop.")
    log("=" * 60)

    # Keep running, tail logs
    try:
        while True:
            time.sleep(5)
            for proc, fh, label, port in procs:
                if proc.poll() is not None:
                    log(f"{label} dashboard DIED (exit code {proc.returncode})", "ERROR")
                    show_log_tail(os.path.join(LOG_DIR, f"dashboard_{label.lower()}.log"), 10)
                    log("Restarting...", "INFO")
                    new_proc, new_fh = start_dashboard(
                        PROP if label == "Prop" else DERIV, label
                    )
                    idx = [(l, p) for p, f, l, p in procs].index((label, port))
                    procs[idx] = (new_proc, new_fh, label, port)
    except KeyboardInterrupt:
        log("Shutting down...")
        for proc, fh, label, port in procs:
            try:
                proc.terminate()
                log(f"  Stopped {label} (PID {proc.pid})")
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass
        log("Done.")


if __name__ == "__main__":
    main()
