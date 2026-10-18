"""Runner entry point for subprocess agents.

Called by subprocess_agent.py:  python subprocess_agent_runner.py <agent_name> <payload_json>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subprocess_agent import run_agent_subprocess

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    run_agent_subprocess(name, payload)
