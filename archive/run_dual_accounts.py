"""Run prop-firm and synthetic watchers in isolated MT5 terminal profiles.

Dry-run is the default. ``--live`` is required before either child may submit
orders. The prop runner evaluates its validated H1 setups periodically; the
synthetic runner continuously watches Boom/Crash signals.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATUS_PATH = ROOT / "logs" / "dual_accounts_status.json"
LOCK_PATH = ROOT / "logs" / "dual_accounts.lock"


def _pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_lock() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(3):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = None
            try:
                pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
            if pid and _pid_alive(pid):
                raise SystemExit(
                    f"another supervisor is already running (pid {pid}) - refusing to start"
                )
            try:
                LOCK_PATH.unlink()
            except OSError:
                pass
            time.sleep(0.5)
            continue
        os.write(fd, bytes(str(os.getpid()), "ascii"))
        os.close(fd)
        return
    raise SystemExit("could not acquire supervisor lock")


def _release_lock() -> None:
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return
    if pid == os.getpid():
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def _write_status(status: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_name(
        f"dual_accounts_status.{os.getpid()}.tmp"
    )
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    for _attempt in range(5):
        try:
            tmp.replace(STATUS_PATH)
            break
        except PermissionError:
            time.sleep(0.2)
    else:
        print("WARNING status write failed (file locked) - continuing", flush=True)
for stale in STATUS_PATH.parent.glob("dual_accounts_status.*.tmp"):
            if stale != tmp:
                try:
                    stale.unlink()
                except OSError:
                    pass
    # Daily journal backup at 02:30 UTC
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 2 and now_utc.minute == 30:
        backup_dir = ROOT / "logs" / "journal_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f_name in ["journal.db", "news_cache.json"]:
            src = MODULE_DIR / "memory" / f_name
            if src.exists():
                import shutil
                shutil.copy2(src, backup_dir / f"{f_name}.{now_utc.strftime('%Y%m%d_%H%M%S')}")
"


def _spawn_synthetic(args: argparse.Namespace) -> subprocess.Popen:
    synthetic_args = [
        sys.executable,
        str(ROOT / "run_synthetic_watcher.py"),
        "--log-file",
        str(ROOT / "logs" / "synthetic_trade_watch.log"),
    ]
    synthetic_args.append("--once" if args.once else "--interval")
    if not args.once:
        synthetic_args.append("15")
    if args.live:
        synthetic_args.append("--live")
    return subprocess.Popen(synthetic_args, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow both profiles to submit orders")
    parser.add_argument("--once", action="store_true", help="run one evaluation per profile")
    parser.add_argument("--prop-interval", type=float, default=300.0)
    args = parser.parse_args()
    mode = "LIVE" if args.live else "DRY-RUN"
    print(f"dual account supervisor starting mode={mode}", flush=True)
    _acquire_lock()
    try:
        _run(mode, args)
    finally:
        _release_lock()
    return 0


HEARTBEAT_MAX_AGE = timedelta(seconds=90)
    now = datetime.now(timezone.utc)
    hb = datetime.fromisoformat(status["heartbeat_at"].replace("Z", "+00:00"))
    if now - hb > HEARTBEAT_MAX_AGE:
        logger.warning("Heartbeat stale (%s seconds without a heartbeat restart)", (now - hb).seconds)
        raise SystemExit("stale heartbeat restarting supervisor")

 
def _run(mode: str, args: argparse.Namespace) -> None:
    started = datetime.now(timezone.utc).isoformat()
    status = {
        "mode": mode,
        "python": sys.executable,
        "started_at": started,
        "heartbeat_at": started,
        "prop": {
            "profile": "FundingPips2-SIM",
            "terminal_path": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
            "state": "starting",
            "pid": None,
            "last_exit": None,
        },
        "synthetic": {
            "profile": "Deriv-Demo",
            "terminal_path": r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe",
            "state": "starting",
            "pid": None,
            "restarts": 0,
        },
    }
    synthetic = _spawn_synthetic(args)
    status["synthetic"].update({"state": "running", "pid": synthetic.pid})
    _write_status(status)

    prop = None
    next_prop_at = 0.0
    try:
        while True:
            now = time.monotonic()
            if synthetic.poll() is not None:
                status["synthetic"]["state"] = "exited"
                if args.once:
                    break
                synthetic = _spawn_synthetic(args)
                status["synthetic"].update(
                    {
                        "state": "running",
                        "pid": synthetic.pid,
                        "restarts": status["synthetic"]["restarts"] + 1,
                    }
                )
            if prop is None and now >= next_prop_at:
                prop_args = [sys.executable, str(ROOT / "run_prop_trader.py")]
                if args.live:
                    prop_args.append("--live")
                prop = subprocess.Popen(prop_args, cwd=ROOT)
                status["prop"].update({"state": "running", "pid": prop.pid})
            if prop is not None:
                code = prop.poll()
                if code is not None:
                    print(f"prop evaluation exited code={code}", flush=True)
                    status["prop"].update(
                        {
                            "state": "exited",
                            "pid": prop.pid,
                            "last_exit": code,
                        }
                    )
                    prop = None
                    if args.once:
                        if synthetic.poll() is None:
                            synthetic.wait(timeout=60)
                        break
                    next_prop_at = time.monotonic() + max(30.0, args.prop_interval)
            status["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            _write_status(status)
            time.sleep(5.0)
    except KeyboardInterrupt:
        print("dual account supervisor stopping", flush=True)
    finally:
        if prop is not None and prop.poll() is None:
            prop.terminate()
        if synthetic.poll() is None:
            synthetic.terminate()
        try:
            synthetic.wait(timeout=15)
        except subprocess.TimeoutExpired:
            synthetic.kill()
        status["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        status["prop"]["state"] = "stopped"
        status["synthetic"]["state"] = "stopped"
        _write_status(status)


if __name__ == "__main__":
    raise SystemExit(main())
