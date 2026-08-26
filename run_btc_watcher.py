"""Run the BTC trend watcher against the Prop MT5 terminal profile."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
settings = json.loads((ROOT / "mt5_mcp" / "settings.json").read_text(encoding="utf-8"))
mt5 = settings.get("mt5", {})

# Set Prop (MT5) terminal credentials
os.environ["MT5_MCP_MT5_TERMINAL_PATH"] = os.getenv(
    "PROP_MT5_PATH",
    r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
)
os.environ["MT5_MCP_MT5_LOGIN"] = str(mt5.get("login", ""))
os.environ["MT5_MCP_MT5_PASSWORD"] = str(mt5.get("password", ""))
os.environ["MT5_MCP_MT5_SERVER"] = str(mt5.get("server", ""))

from btc_trend_watch import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())