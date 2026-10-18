"""Quick health check for all MT5 services."""
import json
import os
import sys
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH = os.environ.get("WEB_AUTH_TOKEN", "")

SERVICES = [
    ("Prop Dashboard", 8055),
    ("Deriv Dashboard", 8052),
]


def check_port(port, timeout=5):
    """Check if a service is up and get basic info."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ping",
            headers={"X-Auth-Token": AUTH},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return "OK", data
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", None
    except Exception as e:
        return str(e)[:60], None


def check_health(port, timeout=5):
    """Check MT5 connection status."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health",
            headers={"X-Auth-Token": AUTH},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def check_account(port, timeout=5):
    """Check account info."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/account",
            headers={"X-Auth-Token": AUTH},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def tail_log(name, lines=5):
    """Get last N lines of a log file."""
    log_path = os.path.join(BASE_DIR, "logs", name)
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()[-lines:]


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  MT5 Services Status  |  {now}")
    print(f"{'='*60}\n")

    for name, port in SERVICES:
        status, ping = check_port(port)
        print(f"[{name}] :{port}")

        if status != "OK":
            print(f"  Status: DOWN ({status})")
            # Show last log lines
            log_name = f"dashboard_{name.split()[0].lower()}.log"
            log_lines = tail_log(log_name, 5)
            if log_lines:
                print(f"  Last log lines:")
                for line in log_lines:
                    print(f"    {line.rstrip()}")
            print()
            continue

        print(f"  Status: UP")

        health = check_health(port)
        if health:
            mt5 = "Connected" if health.get("connected") else "DISCONNECTED"
            print(f"  MT5: {mt5}")
            term = health.get("terminal")
            if term:
                print(f"  Terminal: {term.get('company', '?')} build {term.get('build', '?')}")

        acct = check_account(port)
        if acct:
            print(f"  Login: {acct.get('login')}")
            print(f"  Balance: ${acct.get('balance', 0):,.2f}")
            print(f"  Equity: ${acct.get('equity', 0):,.2f}")
            print(f"  Floating P&L: ${acct.get('floating_pnl', 0):,.2f}")

        # Check for recent errors in log
        log_name = f"dashboard_{name.split()[0].lower()}.log"
        log_lines = tail_log(log_name, 3)
        errors = [l for l in log_lines if "ERROR" in l or "CRITICAL" in l]
        if errors:
            print(f"  Recent errors:")
            for e in errors[-3:]:
                print(f"    {e.rstrip()}")
        print()

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
