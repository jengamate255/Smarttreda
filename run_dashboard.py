"""Supervisor for the MT5 web dashboard.

Keeps web_dashboard.py running, restarting it automatically if it exits
or crashes. Runs the dashboard as a child process so restarts are clean.

Usage:  python run_dashboard.py
"""

import subprocess
import sys
import time

MAX_RESTARTS = 10


def main() -> int:
    restarts = 0
    last_start = time.monotonic()
    while True:
        print("Starting web dashboard...")
        proc = subprocess.Popen([sys.executable, "web_dashboard.py"])
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait(timeout=10)
            print("Stopped.")
            return 0

        uptime = time.monotonic() - last_start
        if uptime > 300:
            restarts = 0  # ran fine for 5 minutes, reset the crash counter
        restarts += 1
        print(f"Dashboard exited with code {code} after {uptime:.0f}s of uptime")

        if restarts > MAX_RESTARTS:
            print(f"Too many rapid restarts ({MAX_RESTARTS}); giving up.")
            return 1

        time.sleep(3)
        last_start = time.monotonic()


if __name__ == "__main__":
    sys.exit(main())
