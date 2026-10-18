"""SubprocessAgent — runs agent compute in a child process to avoid GIL.

The dashboard process (parent) reads status from a JSON file that the
child process writes.  This keeps the event loop responsive even during
heavy compute.

Usage:
    class MyAgent(SubprocessAgent):
        def compute(self, payload):
            # heavy work here — runs in a child process
            for i in range(100):
                self.report(progress=i, current=f"step {i}")
            return {"result": 42}
"""
from __future__ import annotations

import datetime
import importlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("subprocess_agent")

PROJECT = Path(r"E:\Project\Mt5 Ea")
STATUS_DIR = PROJECT / "memory" / "agent_status"
STATUS_DIR.mkdir(parents=True, exist_ok=True)


class SubprocessAgent:
    """Base class for agents that run in a subprocess."""

    def __init__(self, name: str = "unnamed"):
        self.name = name
        self._proc: subprocess.Popen | None = None
        self._status_file = STATUS_DIR / f"{self.name}.json"
        self._result_file = STATUS_DIR / f"{self.name}_result.json"
        self._lock = threading.Lock()

    def _write_status(self, **kw):
        """Write status dict to JSON (called from child process)."""
        state = {"running": True, "error": None, "phase": "running",
                 "progress": 0, "current": "", "elapsed_s": 0,
                 "started_at": datetime.datetime.utcnow().isoformat()}
        state.update(kw)
        try:
            self._status_file.write_text(json.dumps(state, default=str), encoding="utf-8")
        except Exception:
            pass

    def _write_result(self, result):
        """Write final result to JSON (called from child process)."""
        try:
            data = {"date": datetime.date.today().isoformat(), **(result or {})}
            self._result_file.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _runner_alive(self) -> bool:
        """Check if a subprocess_agent_runner process for this agent name exists."""
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return f"subprocess_agent_runner.py {self.name} " in out or \
                   f"subprocess_agent_runner.py\" {self.name}" in out
        except Exception:
            # wmic missing — assume alive (safe: don't block restarts wrongly)
            return True

    def status(self) -> dict:
        """Read status from JSON file (called from parent/dashboard)."""
        try:
            if self._status_file.exists():
                state = json.loads(self._status_file.read_text(encoding="utf-8"))
                # Auto-expire: if marked running but no runner process and
                # file is older than 5 minutes, treat as dead
                if state.get("running"):
                    age = time.time() - self._status_file.stat().st_mtime
                    if age > 300 and not self._runner_alive():
                        state = {**state, "running": False, "phase": "error",
                                 "error": "agent died (stale status reset)"}
                return state
        except Exception:
            pass
        return {"running": False, "phase": "idle", "progress": 0, "current": "",
                "error": None, "elapsed_s": 0}

    def latest(self) -> dict | None:
        """Read latest result from JSON file (called from parent/dashboard)."""
        try:
            if self._result_file.exists():
                text = self._result_file.read_text(encoding="utf-8")
                # Python json.dump may emit Infinity/NaN which strict JSON
                # parsers (FastAPI) reject — sanitise to None.
                text = text.replace("Infinity", "null").replace("NaN", "null")
                data = json.loads(text)
                # recursively strip inf/nan floats that survived json.load
                def _clean(o):
                    if isinstance(o, dict):
                        return {k: _clean(v) for k, v in o.items()}
                    if isinstance(o, list):
                        return [_clean(v) for v in o]
                    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
                        return None
                    return o
                return _clean(data)
        except Exception:
            pass
        return None

    def start(self, **kwargs) -> bool:
        """Launch child process. Returns False if already running."""
        st = self.status()
        if st.get("running"):
            return False

        # Write initial status
        self._write_status(phase="starting", progress=0, current="Launching...")

        # Spawn child process (stdio to log file, not PIPE — PIPE can deadlock)
        payload_json = json.dumps(kwargs, default=str)
        cmd = [
            sys.executable, "-u", "-B", str(PROJECT / "subprocess_agent_runner.py"),
            self.name, payload_json,
        ]
        try:
            log_path = PROJECT / "logs" / f"agent_{self.name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8")
            self._proc = subprocess.Popen(
                cmd, stdout=log_file, stderr=subprocess.STDOUT,
                cwd=str(PROJECT),
            )
            logger.info("started %s (pid=%d)", self.name, self._proc.pid)
            return True
        except Exception as e:
            self._write_status(phase="error", error=str(e), running=False)
            logger.error("failed to start %s: %s", self.name, e)
            return False

    def kill(self):
        """Kill the child process."""
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            self._write_status(phase="killed", running=False, error="killed by user")


def _write_status(name, **kw):
    """Helper for child processes to write status."""
    status_file = STATUS_DIR / f"{name}.json"
    state = {"running": True, "error": None, "phase": "running",
             "progress": 0, "current": "", "elapsed_s": 0,
             "started_at": datetime.datetime.utcnow().isoformat()}
    state.update(kw)
    status_file.write_text(json.dumps(state, default=str), encoding="utf-8")


def _write_result(name, result):
    """Helper for child processes to write result."""
    result_file = STATUS_DIR / f"{name}_result.json"
    data = {"date": datetime.date.today().isoformat(), **(result or {})}
    result_file.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")


def _finish(name, **kw):
    """Mark agent as done."""
    status_file = STATUS_DIR / f"{name}.json"
    state = {"running": False, "phase": "done", "progress": 100,
             "finished_at": datetime.datetime.utcnow().isoformat()}
    state.update(kw)
    # Preserve current and elapsed_s from last write
    try:
        prev = json.loads(status_file.read_text(encoding="utf-8"))
        state.setdefault("current", prev.get("current", ""))
        state.setdefault("elapsed_s", prev.get("elapsed_s", 0))
    except Exception:
        pass
    status_file.write_text(json.dumps(state, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Agent registry — maps agent name → (module_path, function_name)
# ---------------------------------------------------------------------------
AGENT_REGISTRY = {
    "sensitivity":  ("backtest_agents", "run_sensitivity"),
    "sweep":        ("backtest_agents", "run_sweep"),
    "correlation":  ("backtest_agents", "run_correlation"),
    "monte-carlo":  ("backtest_agents", "run_monte_carlo"),
    "ml-rank":      ("backtest_agents", "run_ml_ranker"),
    "export":       ("backtest_agents", "run_export"),
    "research":     ("research_agent", "run_research"),
    "walk-forward": ("backtest_agents", "run_walk_forward"),
    "pipeline":     ("pipeline", "run_pipeline"),
}


def run_agent_subprocess(name: str, payload: dict):
    """Entry point for child processes. Loads and runs the agent function."""
    t0 = time.time()
    _write_status(name, phase="loading", progress=5, current=f"Loading {name}...")

    try:
        mod_name, func_name = AGENT_REGISTRY.get(name, (None, None))
        if not mod_name:
            raise ValueError(f"Unknown agent: {name}")

        sys.path.insert(0, str(PROJECT))
        sys.path.append(str(PROJECT / "mt5_mcp"))
        mod = importlib.import_module(mod_name)
        func = getattr(mod, func_name)
        result = func(payload)

        elapsed = round(time.time() - t0, 1)
        _write_result(name, result or {})
        _finish(name, elapsed_s=elapsed)
        logger.info("%s done in %.1fs", name, elapsed)

    except Exception as e:
        import traceback
        traceback.print_exc()
        elapsed = round(time.time() - t0, 1)
        _write_status(name, phase="error", error=str(e), running=False, elapsed_s=elapsed)
        logger.error("%s failed: %s", name, e)
