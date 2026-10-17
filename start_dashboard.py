"""Launch web_dashboard.py as a fully detached background process."""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
DETACHED = 0x00000008 | 0x00000200 | 0x01000000

with open(os.path.join(BASE, "dashboard_stdout.log"), "ab") as out:
    with open(os.path.join(BASE, "dashboard_stderr.log"), "ab") as err:
        proc = subprocess.Popen(
            [sys.executable, "-u", "web_dashboard.py"],
            cwd=BASE,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED,
            close_fds=True,
        )
print(f"Launched dashboard PID: {proc.pid}")
